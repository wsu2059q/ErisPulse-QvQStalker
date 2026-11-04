"""五层参与决策（QvQChat.bak/chat/session.py should_reply 算法思想移植）。

层级（未命中逐层下沉）：
- L1 提问关键词（零成本正则）：命中以 question_prob 概率参与
  （话题热度 > 0.5 时 +0.3，上限 0.95）；未命中概率继续下沉
- L2 热度标志：热度 > heat_flag_threshold 且 random() < min(热度×0.3, 0.7)
  → 标记升级
- L3 沉寂唤醒：最新消息打破的沉寂（窗口最近两条间隔）> silence 阈值
  → 标记升级
- L4 概率层：观察计数 >= min_messages 后，
  base_prob(0.03 × 模式档位) + min(热度×0.05, heat_bonus_cap) 命中即参与
  （conservative=0 / balanced=1 / active=2；conservative 不随机发言）
- L5 小模型单问判断：升级条件触发且 llm_judge 开启时，带最近 10 条旁听
  单问"该不该接这句话"；未开启（或判断失败）时按 L4 结果

输出统一 ``{"decision": "speak"|"silent", "reason": str, "score": float, "urgency": str}``；
所有分支 reason 必填、人类可读（供 Dashboard 展示"为什么说话/沉默"）。

契约说明：热度/旁听窗口由 qvq.observe 观察回调维护（先于决策发生），
本模块只读不写；自身维护的唯一状态是每会话"距上次插话尝试的消息计数"。
"""

from __future__ import annotations

import asyncio
import random
import re
from typing import Awaitable, Callable

from . import texts
from .heat import TopicHeat
from .observe import MessageWindow, is_group_key

# 提问模式关键词（命中则高概率回复）——移植自 3.x QUESTION_PATTERNS
QUESTION_PATTERNS = [
    r"怎么",
    r"为什么",
    r"什么",
    r"是不是",
    r"能不能",
    r"可以吗",
    r"多少",
    r"哪里",
    r"哪个",
    r"谁",
    r"何时",
    r"\?$",
    r"\？$",
    r"吗[？?]?",
    r"呢[？?]?",
]

_COMPILED = [re.compile(p) for p in QUESTION_PATTERNS]

# 模式 → 基础概率倍数（conservative 不随机发言）
MODE_MULT: dict[str, int] = {"conservative": 0, "balanced": 1, "active": 2}

QUESTION_HEAT_BONUS = 0.3    # 提问层热度加成
QUESTION_PROB_CAP = 0.95
HEAT_FLAG_GATE = 0.3         # min(heat*0.3, 0.7) 的系数
HEAT_FLAG_GATE_CAP = 0.7
HEAT_PROB_COEF = 0.05        # L4 热度加成系数

URGENCY_LOW, URGENCY_NORMAL, URGENCY_HIGH = "low", "normal", "high"


def is_question(text: str) -> bool:
    """零成本判断消息是否是提问。"""
    text = str(text or "")
    return any(p.search(text) for p in _COMPILED)


def _result(decision: str, reason: str, score: float, urgency: str) -> dict:
    return {
        "decision": decision,
        "reason": reason,
        "score": round(max(0.0, min(float(score), 1.0)), 4),
        "urgency": urgency,
    }


class DecisionProvider:
    """qvq_decision 约定服务实现（QvQChat T15 扩展点 B）。

    :param config_getter: 返回最新配置对象的 callable（支持热更新）
    :param heat: TopicHeat 实例（观察侧维护）
    :param window: MessageWindow 实例（观察侧维护）
    :param judge: async (prompt: str) -> str，L5 小模型单问入口；
        返回值经 _parse_judge 解析，异常/超时按 L4 结果处理
    :param rng: 随机源（默认 random.random，测试可注入）
    """

    def __init__(self, config_getter: Callable, heat: TopicHeat, window: MessageWindow,
                 judge: Callable[[str], Awaitable[str]] | None = None,
                 logger=None, rng: Callable[[], float] | None = None,
                 fewshot=None):
        self._cfg_get = config_getter
        self.heat = heat
        self.window = window
        self.judge = judge
        self.logger = logger
        self.fewshot = fewshot
        self._rng = rng or random.random
        self._count: dict[str, int] = {}

    # ==================== 对外入口 ====================

    async def qvq_decision(self, ctx: dict | None = None, **_) -> dict:
        """参与决策主入口（经 sdk.module.call("QvQStalker", "qvq_decision", ctx=...) 调用）。"""
        if not isinstance(ctx, dict):
            return _result("silent", texts.t("reason_invalid_ctx"), 0.0, URGENCY_LOW)
        chat_key = str(ctx.get("chat_key") or "")
        if not chat_key:
            return _result("silent", texts.t("reason_invalid_ctx"), 0.0, URGENCY_LOW)

        is_group = ctx.get("is_group")
        if is_group is None:
            is_group = is_group_key(chat_key)
        if not is_group:
            return _result("silent", texts.t("reason_not_group"), 0.0, URGENCY_LOW)

        # 确定性触发（@机器人）按设计不应到达这里；防御性直接参与
        if ctx.get("at_bot"):
            return _result("speak", texts.t("reason_at_bot"), 1.0, URGENCY_HIGH)

        cfg = self._cfg_get()
        text = str(ctx.get("text") or "")
        heat = self.heat.get(chat_key)

        # ---- L1 提问关键词（零成本） ----
        if text and is_question(text):
            prob = float(getattr(cfg, "question_prob", 0.6) or 0.0)
            if heat > 0.5:
                prob += QUESTION_HEAT_BONUS
            prob = min(prob, QUESTION_PROB_CAP)
            if self._rng() < prob:
                return _result("speak", texts.t("reason_question_hit", heat=heat, prob=prob),
                               prob, URGENCY_HIGH)
            # 概率未命中 → 继续下沉

        # ---- L2 热度标志 / L3 沉寂唤醒（升级判定） ----
        heat_flag = heat > float(getattr(cfg, "heat_flag_threshold", 0.8) or 0.0) and \
            self._rng() < min(heat * HEAT_FLAG_GATE, HEAT_FLAG_GATE_CAP)
        gap = self.window.silence_gap(chat_key)
        silence_wake = gap > float(getattr(cfg, "silence_threshold_minutes", 30) or 0) * 60

        # ---- L5 小模型单问判断（升级条件触发且 llm_judge 开启） ----
        if (heat_flag or silence_wake) and getattr(cfg, "llm_judge", False) and self.judge is not None:
            verdict = await self._ask_judge(cfg, ctx, chat_key, text, heat, gap,
                                            heat_flag=heat_flag, silence_wake=silence_wake)
            if verdict is not None:
                return verdict
            # 判断失败/无法解析 → 按 L4 结果

        # ---- L4 概率层 ----
        return self._layer_probability(cfg, chat_key, heat)

    # ==================== 内部 ====================

    def _layer_probability(self, cfg, chat_key: str, heat: float) -> dict:
        mode = str(getattr(cfg, "mode", "balanced") or "balanced")
        mode_mult = MODE_MULT.get(mode, 1)

        count = self._count.get(chat_key, 0)
        min_messages = int(getattr(cfg, "min_messages_between_replies", 15) or 0)

        if mode_mult == 0:
            self._count[chat_key] = count + 1
            return _result("silent", texts.t("reason_conservative"), 0.0, URGENCY_LOW)

        if count < min_messages:
            self._count[chat_key] = count + 1
            return _result("silent", texts.t("reason_below_min", count=count + 1, need=min_messages),
                           0.0, URGENCY_LOW)

        self._count[chat_key] = 0
        base = float(getattr(cfg, "default_probability", 0.03) or 0.0) * mode_mult
        bonus = min(heat * HEAT_PROB_COEF, float(getattr(cfg, "heat_bonus_cap", 0.15) or 0.15))
        final = base + bonus
        if self._rng() < final:
            return _result("speak", texts.t("reason_prob_hit", prob=final, mode=mode, heat=heat),
                           final, URGENCY_NORMAL)
        return _result("silent", texts.t("reason_prob_miss", prob=final, mode=mode, heat=heat),
                       final, URGENCY_LOW)

    async def _ask_judge(self, cfg, ctx: dict, chat_key: str, text: str, heat: float,
                         gap: float, heat_flag: bool, silence_wake: bool) -> dict | None:
        """L5 小模型单问；返回完整结果 dict，失败返回 None（回落 L4）。"""
        if heat_flag:
            source = texts.t("source_heat", heat=heat)
        else:
            source = texts.t("source_silence", minutes=max(gap, 0) / 60)
        mention_note = (
            texts.t("mention_note_direct") if bool(ctx.get("at_bot")) else texts.t("mention_note_none")
        )
        prompt = texts.t(
            "judge_prompt",
            window=self.window.snapshot_text(chat_key, 10) or "（暂无旁听记录）",
            sender=str(ctx.get("sender_name") or ctx.get("sender_id") or "?"),
            text=text or "（空消息）",
            mention_note=mention_note,
        )
# H8 few-shot：池里有足够样例时给判定模型一个语气锚（开关可关）
        if self.fewshot is not None and bool(getattr(cfg, "fewshot_enabled", True)):
            try:
                examples = self.fewshot.examples_for(chat_key)
                if examples:
                    prompt += "\n\n" + texts.t(
                        "fewshot_style_note",
                        examples="\n".join("- " + e for e in examples),
                    )
            except Exception:
                pass
        try:
            timeout = float(getattr(cfg, "judge_timeout", 8.0) or 8.0)
            answer = await asyncio.wait_for(self.judge(prompt), timeout=timeout)
        except Exception as e:
            if self.logger is not None:
                try:
                    self.logger.debug(f"judge failed: {e}")
                except Exception:
                    pass
            return None
        verdict = parse_judge_answer(answer)
        if verdict is None:
            return None
        if verdict:
            return _result("speak", texts.t("reason_llm_speak", source=source), 0.9, URGENCY_HIGH)
        return _result("silent", texts.t("reason_llm_silent", source=source), 0.1, URGENCY_LOW)


def parse_judge_answer(answer: str) -> bool | None:
    """解析小模型的是/否回答；无法解析返回 None（回落概率层）。"""
    if not isinstance(answer, str):
        return None
    s = answer.strip().lower()
    if not s:
        return None
    yes_marks = ("是", "yes", "yeah", "y", "该", "回", "speak", "join")
    no_marks = ("否", "不是", "不", "no", "n", "别", "silent", "skip")
    # 否定性词（如 "不是"）优先于肯定词（"是"是其子串）
    if s.startswith(no_marks) or any(word in s for word in ("不要", "不该", "别接", "不回")):
        return False
    if s.startswith(yes_marks):
        return True
    return None
