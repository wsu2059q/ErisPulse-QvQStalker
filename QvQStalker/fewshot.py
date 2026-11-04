"""H8 few-shot 双池（拟人化总纲条 7）：反响好的 bot 发言沉淀为语气样例。

数据面：
- ``qvq.replied``（QvQChat 发出）→ 机器人自己的发言，进入待判定 pending；
- ``qvq.observe`` 里的后续他人消息 → 5 分钟内出现即视为"有回应"。

双池（sdk.storage 持久化）：
- 基准池（全局，上限 ``fewshot_base_cap``，默认 20）：满员后不再进——固定锚，
  只装"发出后 5 分钟内收到至少一条非自己回应"的句子；
- 增量池（按群，上限 ``fewshot_inc_cap``，默认 30）：只保留最近 7 天的合格句，
  自然周轮换（写入与读取时惰性裁剪）。

消费：decision 判定提示词与 proactive 主动发言注入「语气参考」；
另提供 ``fewshot`` 约定服务供观测/其它模块取用。准入与池操作全部纯同步、
失败静默（样例系统绝不能影响主流程）。
"""

from __future__ import annotations

import time

REACTION_WINDOW = 300.0          # 反响判定窗口：发出后 5 分钟内有回应才算
WEEK_SECONDS = 7 * 86400.0       # 增量池轮换周期
MIN_TEXT, MAX_TEXT = 2, 60       # 合格句长度区间（太长的是报告不是语气）
MIN_POOL_TO_INJECT = 3           # 样例太少时不注入（避免锚到噪音）

BASE_KEY = "QvQStalker:fewshot:base"
INC_PREFIX = "QvQStalker:fewshot:inc:"
PENDING_PREFIX = "QvQStalker:fewshot:pending:"

_BAD_MARKS = ("```", "http://", "https://", "[合并转发", "任务没跑成")


def group_of(chat_key: str) -> str:
    """群标识：platform:group:gid[:uid] → gid（隔离会话并入群池）。"""
    parts = str(chat_key or "").split(":")
    if len(parts) >= 3 and parts[1] == "group":
        return parts[2]
    return str(chat_key or "")


def qualified_text(text: str) -> bool:
    """合格句门槛：短句、无代码块/链接/系统播报味。"""
    text = str(text or "").strip()
    if not (MIN_TEXT <= len(text) <= MAX_TEXT):
        return False
    if text.count("\n") > 1:
        return False
    if any(mark in text for mark in _BAD_MARKS):
        return False
    return True


class FewShotPools:
    """双池管理器：写入方（事件订阅）与读取方（提示词注入）共用。"""

    def __init__(self, storage, logger=None, base_cap: int = 20, inc_cap: int = 30):
        self.storage = storage
        self.logger = logger
        self.base_cap = max(0, int(base_cap))
        self.inc_cap = max(0, int(inc_cap))

    # ==================== 存取兜底 ====================

    def _get(self, key: str, default):
        try:
            return self.storage.get(key, default)
        except Exception:
            return default

    def _set(self, key: str, value) -> None:
        try:
            self.storage.set(key, value)
        except Exception as e:
            if self.logger is not None:
                try:
                    self.logger.debug(f"fewshot store failed: {e}")
                except Exception:
                    pass

    @staticmethod
    def _entry(text: str, chat_key: str, ts: float) -> dict:
        return {"text": str(text), "chat_key": str(chat_key), "ts": float(ts)}

    # ==================== 写入 ====================

    def record_bot_reply(self, chat_key: str, text: str, ts: float | None = None) -> bool:
        """机器人发言入待判定（每会话只保留最新一条 pending）。"""
        text = str(text or "").strip()
        # 多段回复取第一段（语气句通常在开头）
        first_line = text.split("\n", 1)[0].strip()
        if not qualified_text(first_line):
            return False
        self._set(
            PENDING_PREFIX + str(chat_key or ""),
            self._entry(first_line, chat_key, ts if ts else time.time()),
        )
        return True

    def record_reaction(self, chat_key: str, ts: float | None = None) -> bool:
        """他人消息到达：若命中该会话 pending 的反响窗口则收编入池。"""
        chat_key = str(chat_key or "")
        key = PENDING_PREFIX + chat_key
        pending = self._get(key, None)
        if not isinstance(pending, dict):
            return False
        now = float(ts if ts else time.time())
        try:
            self.storage.delete(key)
        except Exception:
            pass
        if not (0 <= now - float(pending.get("ts") or 0) <= REACTION_WINDOW):
            return False
        return self._admit(str(pending.get("text") or ""), chat_key, now)

    def _admit(self, text: str, chat_key: str, ts: float) -> bool:
        if not qualified_text(text):
            return False
        admitted = False
# 基准池：固定锚，满员即封池（不再轮换，防语气漂移）
        base = self._get(BASE_KEY, [])
        if isinstance(base, list) and len(base) < self.base_cap:
            base.append(self._entry(text, chat_key, ts))
            self._set(BASE_KEY, base)
            admitted = True
# 增量池：按群、只留最近 7 天、容量裁剪
        if self.inc_cap > 0:
            inc_key = INC_PREFIX + group_of(chat_key)
            inc = [e for e in self._get(inc_key, []) if isinstance(e, dict)]
            inc.append(self._entry(text, chat_key, ts))
            inc = [e for e in inc if ts - float(e.get("ts") or 0) <= WEEK_SECONDS]
            inc = inc[-self.inc_cap:]
            self._set(inc_key, inc)
        return admitted or self.inc_cap > 0

    # ==================== 读取 ====================

    def examples_for(self, chat_key: str, limit: int = 6) -> list[str]:
        """基准池 + 本群增量池的语气样例；不足 MIN_POOL_TO_INJECT 条返回空。"""
        texts: list[str] = []
        seen: set[str] = set()
        base = self._get(BASE_KEY, [])
        if isinstance(base, list):
            for entry in base:
                text = str(entry.get("text") or "")
                if text and text not in seen:
                    seen.add(text)
                    texts.append(text)
        now = time.time()
        inc = self._get(INC_PREFIX + group_of(chat_key), [])
        if isinstance(inc, list):
            fresh = [
                str(e.get("text") or "")
                for e in inc
                if isinstance(e, dict) and now - float(e.get("ts") or 0) <= WEEK_SECONDS
            ]
            for text in reversed(fresh):  # 增量池新句在前
                if text and text not in seen:
                    seen.add(text)
                    texts.append(text)
        if len(texts) < MIN_POOL_TO_INJECT:
            return []
        return texts[: max(1, int(limit))]

    def status(self) -> dict:
        base = self._get(BASE_KEY, [])
        return {
            "base": len(base) if isinstance(base, list) else 0,
            "base_cap": self.base_cap,
            "base_full": isinstance(base, list) and len(base) >= self.base_cap,
            "reaction_window": REACTION_WINDOW,
            "weekly_seconds": WEEK_SECONDS,
        }
