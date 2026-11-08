"""五层判定各分支（random.seed 固定 / 概率极端值双重确定性）、reason 非空、score 范围。"""

import random

import pytest

from QvQStalker import texts
from QvQStalker.decision import DecisionProvider, is_question, parse_judge_answer
from QvQStalker.heat import TopicHeat
from QvQStalker.observe import MessageWindow

from fakes import make_config

GKEY = "onebot11:group:100"


class JudgeStub:
    def __init__(self, answer="是"):
        self.answer = answer
        self.prompts = []

    async def __call__(self, prompt):
        self.prompts.append(prompt)
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


def make_provider(cfg, judge=None, window=None, heat=None):
    heat = heat if heat is not None else TopicHeat()
    window = window if window is not None else MessageWindow(size=50)
    return DecisionProvider(lambda: cfg, heat, window, judge=judge), heat, window


def ctx(text="大家觉得呢", chat_key=GKEY, **extra):
    base = {
        "chat_key": chat_key,
        "platform": "onebot11",
        "detail_type": "group",
        "group_id": "100",
        "sender_id": "u1",
        "sender_name": "小明",
        "text": text,
        "is_group": True,
        "at_bot": False,
        "ts": 1000.0,
    }
    base.update(extra)
    return base


# ==================== L1 提问关键词 ====================

async def test_l1_question_hit_speaks():
    cfg = make_config(question_prob=1.0)
    provider, _, _ = make_provider(cfg)
    random.seed(1)
    result = await provider.qvq_decision(ctx(text="这是什么？"))
    assert result["decision"] == "speak"
    assert result["urgency"] == "high"
    assert result["score"] == pytest.approx(0.95)  # min(1.0, 0.95) 封顶
    assert result["reason"]


async def test_l1_heat_bonus_raises_probability():
    cfg = make_config(question_prob=0.5)
    provider, heat, _ = make_provider(cfg)
    heat.set(GKEY, 1.0)  # > 0.5 → +0.3
    random.seed(1)  # 首个 random() = 0.134 < 0.8
    result = await provider.qvq_decision(ctx(text="为什么是这样？"))
    assert result["decision"] == "speak"
    assert result["score"] == pytest.approx(0.8)


async def test_l1_miss_falls_through_to_probability_layer():
    cfg = make_config(question_prob=0.0, min_messages_between_replies=1,
                      default_probability=1.0)
    provider, _, _ = make_provider(cfg)
    random.seed(1)  # L1 掷 0.134 ≥ 0.0 未命中 → 下沉
    first = await provider.qvq_decision(ctx(text="这是什么？"))
    assert first["decision"] == "silent"
    assert first["reason"] == texts.t("reason_below_min", count=1, need=1)
    random.seed(1)
    second = await provider.qvq_decision(ctx(text="这又是什么？"))
    assert second["decision"] == "speak"
    assert second["score"] == pytest.approx(1.0)


def test_is_question_patterns():
    assert is_question("怎么优化")
    assert is_question("为什么")
    assert is_question("可以吗")
    assert is_question("好呢？")
    assert is_question("done?")
    assert not is_question("今天天气不错")
    assert not is_question("")


# ==================== L2 热度标志 / L5 小模型 ====================

async def test_l2_heat_flag_escalates_to_judge():
    cfg = make_config(llm_judge=True, judge_timeout=2.0, default_probability=0.0,
                      min_messages_between_replies=0)
    judge = JudgeStub("是")
    provider, heat, window = make_provider(cfg, judge=judge)
    heat.set(GKEY, 1.0)  # > 0.8 → 热度标志掷 min(1.0*0.3, 0.7)=0.3
    window.add(GKEY, "旁听内容甲", sender_id="u2", sender_name="小红", ts=900.0)
    random.seed(1)  # L2 掷 0.134 < 0.3 → 升级
    result = await provider.qvq_decision(ctx(text="接得上的话题"))
    assert result["decision"] == "speak"
    assert result["urgency"] == "high"
    assert result["score"] == pytest.approx(0.9)
    assert "旁听内容甲" in judge.prompts[0]  # 判断 prompt 带最近旁听
    assert texts.t("source_heat", heat=1.0) in result["reason"]


async def test_l2_judge_silent():
    cfg = make_config(llm_judge=True, default_probability=0.0,
                      min_messages_between_replies=0)
    judge = JudgeStub("否")
    provider, heat, _ = make_provider(cfg, judge=judge)
    heat.set(GKEY, 1.0)
    random.seed(1)
    result = await provider.qvq_decision(ctx(text="接不上的话题"))
    assert result["decision"] == "silent"
    assert result["score"] == pytest.approx(0.1)
    assert result["urgency"] == "low"


async def test_l2_judge_error_falls_back_to_l4():
    cfg = make_config(llm_judge=True, default_probability=0.0,
                      min_messages_between_replies=0)
    judge = JudgeStub(RuntimeError("judge down"))
    provider, heat, _ = make_provider(cfg, judge=judge)
    heat.set(GKEY, 1.0)
    random.seed(1)  # L2 命中 → L5 失败 → L4：final=0.0+min(1.0*0.05,0.15)=0.05，掷 0.134 ≥ 0.05
    result = await provider.qvq_decision(ctx(text="随便聊聊"))
    assert result["decision"] == "silent"
    assert result["score"] == pytest.approx(0.05)
    assert result["reason"] == texts.t("reason_prob_miss", prob=0.05, mode="balanced", heat=1.0)


async def test_l2_roll_miss_judge_not_called():
    cfg = make_config(llm_judge=True, default_probability=0.0,
                      min_messages_between_replies=0)
    judge = JudgeStub("是")
    provider, heat, _ = make_provider(cfg, judge=judge)
    heat.set(GKEY, 2.0)  # 门槛 min(2.0*0.3, 0.7)=0.7
    random.seed(0)  # 掷 0.844 ≥ 0.7 → 未升级
    result = await provider.qvq_decision(ctx(text="看看"))
    assert judge.prompts == []
    assert result["decision"] == "silent"


async def test_escalated_but_judge_off_uses_l4():
    cfg = make_config(llm_judge=False, default_probability=1.0,
                      min_messages_between_replies=0)
    judge = JudgeStub("否")
    provider, heat, _ = make_provider(cfg, judge=judge)
    heat.set(GKEY, 1.0)
    random.seed(1)
    result = await provider.qvq_decision(ctx(text="聊聊"))
    assert judge.prompts == []  # llm_judge 关闭，不问模型
    assert result["decision"] == "speak"  # L4 概率 1.0 命中
    assert result["urgency"] == "normal"


# ==================== L3 沉寂唤醒 ====================

async def test_l3_silence_wake_escalates_to_judge():
    cfg = make_config(llm_judge=True, silence_threshold_minutes=30,
                      default_probability=0.0, min_messages_between_replies=0)
    judge = JudgeStub("yes")
    provider, _, window = make_provider(cfg, judge=judge)
    window.add(GKEY, "很久前的消息", ts=1000.0)
    window.add(GKEY, "打破沉寂的消息", ts=1000.0 + 2400.0)  # 沉寂 40 分钟
    result = await provider.qvq_decision(ctx(text="打破沉寂的消息"))
    assert result["decision"] == "speak"
    assert result["urgency"] == "high"
    assert texts.t("source_silence", minutes=40.0) in result["reason"]


async def test_l3_short_gap_not_escalated():
    cfg = make_config(llm_judge=True, silence_threshold_minutes=30)
    judge = JudgeStub("是")
    provider, _, window = make_provider(cfg, judge=judge)
    window.add(GKEY, "m1", ts=1000.0)
    window.add(GKEY, "m2", ts=1000.0 + 60.0)  # 仅 1 分钟
    result = await provider.qvq_decision(ctx(text="m2"))
    assert judge.prompts == []


# ==================== L4 概率层 / 模式 ====================

async def test_below_min_counts():
    cfg = make_config(min_messages_between_replies=3, default_probability=1.0,
                      question_prob=0.0)
    provider, _, _ = make_provider(cfg)
    for i in range(1, 4):
        result = await provider.qvq_decision(ctx(text="普通消息"))
        assert result["decision"] == "silent"
        assert result["reason"] == texts.t("reason_below_min", count=i, need=3)
    random.seed(1)
    result = await provider.qvq_decision(ctx(text="普通消息"))
    assert result["decision"] == "speak"  # 计数到 3 后掷 1.0 必中


async def test_conservative_never_random_speaks():
    cfg = make_config(mode="conservative", default_probability=1.0,
                      min_messages_between_replies=0, question_prob=0.0)
    provider, heat, _ = make_provider(cfg)
    heat.set(GKEY, 1.5)  # 高热度也不放行
    random.seed()
    for i in range(20):
        result = await provider.qvq_decision(ctx(text="普通消息"))
        assert result["decision"] == "silent"
        assert result["score"] == 0.0
        assert result["reason"] == texts.t("reason_conservative")


async def test_strict_mode_only_direct_address():
    """超保守：提问关键词/热度/沉寂/小模型全部不进；仅 @ 或回复机器人时参与。"""
    cfg = make_config(mode="strict", question_prob=1.0, llm_judge=False)
    provider, heat, _ = make_provider(cfg, judge=JudgeStub("是"))
    heat.set(GKEY, 1.5)
    random.seed(1)
    # 提问、高热度、普通消息一律沉默
    for text in ("这是什么？", "怎么做到的", "普通消息"):
        result = await provider.qvq_decision(ctx(text=text))
        assert result["decision"] == "silent", text
        assert result["reason"] == texts.t("reason_strict")
    # 点名即应答（@ 命中最前面的确定性触发门，reason 用 its 文案；回复机器人命中 strict 门）
    result = await provider.qvq_decision(ctx(text="你好", at_bot=True))
    assert result["decision"] == "speak"
    result = await provider.qvq_decision(ctx(text="你说得对", reply_to_bot=True))
    assert result["decision"] == "speak" and result["reason"] == texts.t("reason_strict_addressed")
    result = await provider.qvq_decision(ctx(text="你说得对", reply_to_bot=True))
    assert result["decision"] == "speak" and result["reason"] == texts.t("reason_strict_addressed")


async def test_conservative_question_layer_still_works():
    cfg = make_config(mode="conservative", question_prob=1.0)
    provider, _, _ = make_provider(cfg)
    random.seed(1)
    result = await provider.qvq_decision(ctx(text="这是什么？"))
    assert result["decision"] == "speak"


async def test_active_mode_doubles_base_probability():
    cfg = make_config(mode="active", default_probability=0.5,
                      min_messages_between_replies=0)
    provider, heat, _ = make_provider(cfg)
    random.seed(1)  # 掷 0.134 < 1.0（0.5×2 + 0）必中
    result = await provider.qvq_decision(ctx(text="普通消息"))
    assert result["decision"] == "speak"
    assert result["score"] == pytest.approx(1.0)  # 0.5*2 + 0 = 1.0


async def test_l4_heat_bonus():
    cfg = make_config(default_probability=0.0, min_messages_between_replies=0,
                      heat_bonus_cap=0.15)
    heat = TopicHeat()
    heat.set(GKEY, 2.0)  # set 受 heat_cap 封顶，取上限值验证加成
    rolls = [0.99, 0.05]  # L2 热度标志未命中（0.99 ≥ 0.6）；L4 命中（0.05 < 0.10）
    provider = DecisionProvider(lambda: cfg, heat, MessageWindow(size=50),
                                rng=lambda: rolls.pop(0))
    result = await provider.qvq_decision(ctx(text="普通消息"))
    assert result["decision"] == "speak"
    # 加成 = min(2.0*0.05, 0.15) = 0.10
    assert result["score"] == pytest.approx(0.10)
    assert result["reason"] == texts.t("reason_prob_hit", prob=0.10, mode="balanced", heat=2.0)


# ==================== 防御分支 ====================

async def test_at_bot_speaks_deterministically():
    provider, _, _ = make_provider(make_config())
    result = await provider.qvq_decision(ctx(text="@", at_bot=True))
    assert result["decision"] == "speak"
    assert result["score"] == 1.0
    assert result["reason"] == texts.t("reason_at_bot")


async def test_private_message_silent():
    provider, _, _ = make_provider(make_config(question_prob=1.0))
    result = await provider.qvq_decision(ctx(text="这是什么？", chat_key="onebot11:private:u1",
                                             is_group=False))
    assert result["decision"] == "silent"
    assert result["reason"] == texts.t("reason_not_group")


async def test_private_inferred_from_key():
    provider, _, _ = make_provider(make_config())
    result = await provider.qvq_decision({"chat_key": "onebot11:private:u1", "text": "hi"})
    assert result["decision"] == "silent"
    assert result["reason"] == texts.t("reason_not_group")


async def test_invalid_ctx_variants():
    provider, _, _ = make_provider(make_config())
    for bad in (None, "text", {}, {"text": "no key"}):
        result = await provider.qvq_decision(bad)
        assert result["decision"] == "silent"
        assert result["reason"] == texts.t("reason_invalid_ctx")
        assert result["score"] == 0.0


async def test_decision_reads_heat_without_mutating():
    provider, heat, _ = make_provider(make_config(question_prob=0.0,
                                                  min_messages_between_replies=0,
                                                  default_probability=0.0))
    heat.set(GKEY, 1.234)
    await provider.qvq_decision(ctx(text="普通消息"))
    assert heat.get(GKEY) == pytest.approx(1.234)  # 决策侧只读，热度由观察侧维护


# ==================== 输出不变量 ====================

async def test_output_invariants_across_random_conditions():
    cfg = make_config(llm_judge=True, default_probability=0.05,
                      min_messages_between_replies=2, question_prob=0.4)
    judge = JudgeStub("是")
    provider, heat, window = make_provider(cfg, judge=judge)
    random.seed(42)
    for i in range(60):
        heat.set(GKEY, random.choice([0.0, 0.5, 0.9, 2.0]))
        if window.count(GKEY) == 0 or random.random() < 0.5:
            window.add(GKEY, f"m{i}", ts=1000.0 + i * random.choice([10.0, 2000.0]))
        result = await provider.qvq_decision(ctx(text=random.choice(
            ["这是什么？", "普通聊天内容", "为什么不行", "哈哈哈哈"])))
        assert set(result) == {"decision", "reason", "score", "urgency"}
        assert result["decision"] in ("speak", "silent")
        assert isinstance(result["reason"], str) and result["reason"].strip()
        assert 0.0 <= result["score"] <= 1.0
        assert result["urgency"] in ("low", "normal", "high")


# ==================== 小模型回答解析 ====================

@pytest.mark.parametrize("answer,expected", [
    ("是", True), ("yes", True), ("YES.", True), ("yeah", True), ("该", True),
    ("否", False), ("不", False), ("no", False), ("NO", False), ("不要", False),
    ("", None), ("也许吧", None), ("banana", None),
])
def test_parse_judge_answer(answer, expected):
    assert parse_judge_answer(answer) is expected


def test_parse_judge_answer_non_string():
    assert parse_judge_answer(None) is None
    assert parse_judge_answer(123) is None


# ==================== bot 互逗（纯提示词认知） ====================

def test_judge_prompt_contains_bot_awareness_clause():
    """judge prompt 常驻 bot 认知条款（第 4 条）：AI 从行为自判其它机器人。"""
    prompt = texts.t("judge_prompt", window="w", sender="s", text="t", mention_note="m")
    assert ("别的机器人" in prompt or "other bots" in prompt.lower()) and "[bot]" in prompt


def test_window_snapshot_marks_bot_lines():
    """旁听摘要给同后端 bot 发言加 [bot] 标注（判定与发言模型都可见）。"""
    w = MessageWindow(size=10)
    w.add(GKEY, "签到出凶了", sender_id="sib", sender_name="Pippins", sender_is_bot=True)
    w.add(GKEY, "哈哈哈哈", sender_id="u9", sender_name="小明")
    snap = w.snapshot_text(GKEY)
    assert "[bot]Pippins" in snap and "小明" in snap and "[bot]小明" not in snap
