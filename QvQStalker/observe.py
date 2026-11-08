"""旁听窗口：按 chat_key 存最近 N 条群消息（内存环形缓冲，锁保护）。

数据来源是 ``qvq.observe`` 生命周期事件的纯数据载荷（QvQChat T15 扩展点 A）。
窗口供三处消费：
- 决策层 L3 沉寂唤醒（最近两条消息的间隔）
- L5 小模型判断的"最近 10 条旁听"
- 主动发言的窗口摘要（prompt）
"""

from __future__ import annotations

import threading
import time
from collections import deque
from typing import Iterable


class MessageWindow:
    """每会话一个固定长度 deque 的旁听缓冲。

    :param size: 每会话保留的最大消息条数（环形淘汰最旧）
    """

    def __init__(self, size: int = 50):
        self._size = max(1, int(size))
        self._windows: dict[str, deque] = {}
        self._lock = threading.Lock()

    # ==================== 写入 ====================

    def add(self, chat_key: str, text: str, sender_id: str = "", sender_name: str = "",
            ts: float | None = None, at_bot: bool = False, reply_to_bot: bool = False,
            platform: str = "", sender_is_bot: bool = False) -> dict:
        """入窗一条旁听消息，返回入窗的条目（含归一化的 ts）。"""
        entry = {
            "chat_key": str(chat_key or ""),
            "text": str(text or ""),
            "sender_id": str(sender_id or ""),
            "sender_name": str(sender_name or ""),
            "ts": float(ts) if ts else time.time(),
            "at_bot": bool(at_bot),
            "reply_to_bot": bool(reply_to_bot),
            "platform": str(platform or ""),
# 发话者是同后端其它 bot（QvQChat ≥4.6.0 的 observe 载荷携带；旧载荷缺省 False）
            "sender_is_bot": bool(sender_is_bot),
        }
        with self._lock:
            window = self._windows.get(chat_key)
            if window is None:
                window = deque(maxlen=self._size)
                self._windows[chat_key] = window
            window.append(entry)
        return entry

    def clear(self, chat_key: str | None = None) -> None:
        """清空指定会话（缺省清空全部）。"""
        with self._lock:
            if chat_key is None:
                self._windows.clear()
            else:
                self._windows.pop(chat_key, None)

    # ==================== 读取 ====================

    def recent(self, chat_key: str, limit: int | None = None) -> list[dict]:
        """最近 limit 条（默认全部）消息的浅拷贝列表，旧→新。"""
        with self._lock:
            window = self._windows.get(chat_key)
            items = list(window) if window else []
        if limit is not None and limit >= 0:
            items = items[-limit:] if limit else []
        return [dict(item) for item in items]

    def snapshot_text(self, chat_key: str, limit: int = 10) -> str:
        """把最近 limit 条旁听格式化为多行文本（供 LLM prompt / speak 摘要）。

        兄弟 bot 的发言带 [bot] 标注——判定与发言模型都借此识别"对方也是机器人"。
        """
        lines = []
        for item in self.recent(chat_key, limit):
            name = item.get("sender_name") or item.get("sender_id") or "?"
            sid = item.get("sender_id") or "?"
            if item.get("sender_is_bot"):
                name = f"[bot]{name}"
            text = str(item.get("text") or "").strip()
            if text:
                lines.append(f"{name}({sid}): {text}")
        return "\n".join(lines)

    def count(self, chat_key: str) -> int:
        with self._lock:
            return len(self._windows.get(chat_key, ()))

    def last_ts(self, chat_key: str) -> float:
        """最新一条消息的时间戳；无记录返回 0。"""
        with self._lock:
            window = self._windows.get(chat_key)
            return float(window[-1]["ts"]) if window else 0.0

    def time_span(self, chat_key: str) -> float:
        """窗口时间跨度（最新 - 最旧，秒）；不足两条返回 0。"""
        with self._lock:
            window = self._windows.get(chat_key)
            if not window or len(window) < 2:
                return 0.0
            return float(window[-1]["ts"]) - float(window[0]["ts"])

    def silence_gap(self, chat_key: str) -> float:
        """最近两条消息之间的间隔——即最新一条消息所打破的沉寂时长（秒）。

        用于 L3 沉寂唤醒判定（观察回调先入窗、决策随后读取）。
        不足两条（刚开始旁听）时返回 -1 表示未知。
        """
        with self._lock:
            window = self._windows.get(chat_key)
            if not window or len(window) < 2:
                return -1.0
            return float(window[-1]["ts"]) - float(window[-2]["ts"])

    def chat_keys(self) -> list[str]:
        with self._lock:
            return list(self._windows)


def is_group_key(chat_key: str) -> bool:
    """按 QvQChat 会话键约定（platform:group:gid）判断是否群聊键。"""
    return ":group:" in str(chat_key or "")


def group_keys(keys: Iterable[str]) -> list[str]:
    """从键列表中筛出群聊键。"""
    return [k for k in keys if is_group_key(k)]
