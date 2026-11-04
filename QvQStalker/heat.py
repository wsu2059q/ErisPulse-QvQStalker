"""话题热度（3.x 公式移植，QvQChat.bak/chat/session.py update_topic_heat 思想）。

热度影响因素（每次 update 计算）：
- 基础增量 0.05
- 消息频率：与上一条间隔 <5s +0.15 / <15s +0.08 / <30s +0.03
- 问号 +0.1（有人在提问）；感叹号 +0.05（情绪激烈）
- 自然衰减：先按 min(间隔秒/60, 1) × 0.5 的比例衰减旧热度，再累加增量
- 上限 2.0（可配置）

状态按 chat_key 隔离；仅在下一条消息到来时衰减（与 3.x 一致，
get 返回的是最近一次 update 后的值）。
"""

from __future__ import annotations

import threading
import time


class TopicHeat:
    """每会话话题热度状态机（update 驱动，线程安全）。"""

    BASE_DELTA = 0.05
    GAP_FAST = 5.0       # 秒
    GAP_FAST_BONUS = 0.15
    GAP_MID = 15.0
    GAP_MID_BONUS = 0.08
    GAP_SLOW = 30.0
    GAP_SLOW_BONUS = 0.03
    QUESTION_BONUS = 0.1
    EXCLAIM_BONUS = 0.05
    DECAY_MAX_RATIO = 0.5   # 每满 1 分钟最多衰减 50%
    DEFAULT_CAP = 2.0

    def __init__(self, cap: float = DEFAULT_CAP):
        self._cap = float(cap) if cap and cap > 0 else self.DEFAULT_CAP
        self._heat: dict[str, float] = {}
        self._last_ts: dict[str, float] = {}
        self._lock = threading.Lock()

    def update(self, chat_key: str, message: str, now: float | None = None) -> float:
        """喂入一条消息，返回更新后的热度。"""
        now = float(now) if now else time.time()
        text = str(message or "")
        with self._lock:
            prev = self._last_ts.get(chat_key, 0.0)
            self._last_ts[chat_key] = now

            delta = self.BASE_DELTA
            gap = 0.0
            if prev > 0:
                gap = max(now - prev, 0.0)
                if gap < self.GAP_FAST:
                    delta += self.GAP_FAST_BONUS
                elif gap < self.GAP_MID:
                    delta += self.GAP_MID_BONUS
                elif gap < self.GAP_SLOW:
                    delta += self.GAP_SLOW_BONUS

            if "?" in text or "？" in text:
                delta += self.QUESTION_BONUS
            if "!" in text or "！" in text:
                delta += self.EXCLAIM_BONUS

            current = self._heat.get(chat_key, 0.0)
            if prev > 0:
                decay = min(gap / 60.0, 1.0) * self.DECAY_MAX_RATIO
                current *= 1.0 - decay

            current = min(current + delta, self._cap)
            self._heat[chat_key] = current
            return current

    def get(self, chat_key: str) -> float:
        """读取当前热度（不触发时间衰减）。"""
        with self._lock:
            return self._heat.get(chat_key, 0.0)

    def set(self, chat_key: str, value: float) -> None:
        """直接设置热度（管理/测试用途；不回写 _last_ts）。"""
        with self._lock:
            self._heat[chat_key] = max(0.0, min(float(value), self._cap))

    def reset(self, chat_key: str) -> None:
        with self._lock:
            self._heat.pop(chat_key, None)
            self._last_ts.pop(chat_key, None)

    def keys(self) -> list[str]:
        with self._lock:
            return list(self._heat)
