"""主动发言：冲动值模型 + 门槛检查循环（QvQChat.bak/chat/proactive.py 算法思想移植）。

冲动值（UrgeState，每 chat_key 独立状态）：
- 每条旁听消息累积：基础 0.06 + 提问 0.12 + 感叹号 0.05 + 长消息(≥20字) 0.04
  + 被@/被回复 0.15 + 热度加成 min(热度×0.05, 0.10)
- 半衰期 1 小时指数衰减（"长时间无人理睬衰减"）
- 上限 2.0

门槛链（全部满足才开口，ProactiveLoop.check）：
1. 作息（sleep_hour/wake_hour，两值相等表示不限制）
2. 全局每日上限（storage 持久化）
3. 会话沉寂门槛（距上次主动开口 ≥ min_silence_hours）
4. 冲动值 ≥ urge_threshold
5. 单会话每日上限
6. 未回复冷却（上次开口后窗口内无任何新消息 → unreplied_cooldown_hours 内不再开口）

到点调用 ``await sdk.module.call("QvQChat", "speak", chat_key=..., reason=..., prompt=...)``
——该服务由 QvQChat T15 提供，尚未实现时 try/except 防御 + warning 日志，
冲动值减半避免每轮重试，且不消耗每日配额。
"""

from __future__ import annotations

import asyncio
import random
import time
from datetime import datetime

from . import texts
from .decision import is_question
from .observe import MessageWindow, is_group_key

URGE_HALF_LIFE = 3600.0   # 冲动值半衰期（秒）
URGE_MAX = 2.0

URGE_BASE = 0.06
URGE_QUESTION = 0.12
URGE_EXCLAIM = 0.05
URGE_LONG = 0.04
URGE_AT_BOT = 0.15
URGE_HEAT_COEF = 0.05
URGE_HEAT_CAP = 0.10

DAILY_KEY = "QvQStalker:proactive:daily:{date}"
LAST_SPEAK_KEY = "QvQStalker:proactive:last_speak"

CALL_TIMEOUT = 15.0


class UrgeState:
    """每会话冲动值状态（半衰期衰减，线程安全）。"""

    def __init__(self, half_life: float = URGE_HALF_LIFE, cap: float = URGE_MAX):
        self._half_life = max(float(half_life), 1.0)
        self._cap = float(cap)
        self._urge: dict[str, float] = {}
        self._ts: dict[str, float] = {}

    def _decayed(self, chat_key: str, now: float) -> float:
        urge = self._urge.get(chat_key, 0.0)
        ts = self._ts.get(chat_key, 0.0)
        if urge <= 0 or ts <= 0:
            return max(urge, 0.0)
        elapsed = now - ts
        if elapsed <= 0:
            return urge
        return urge * (0.5 ** (elapsed / self._half_life))

    def feed(self, chat_key: str, text: str, at_bot: bool = False, replied: bool = False,
             heat: float = 0.0, now: float | None = None) -> float:
        """一条旁听消息累积冲动值，返回累积后的值。"""
        now = float(now) if now else time.time()
        urge = self._decayed(chat_key, now)
        t = str(text or "").strip()
        if t:
            urge += URGE_BASE
            if is_question(t):
                urge += URGE_QUESTION
            if "!" in t or "！" in t:
                urge += URGE_EXCLAIM
            if len(t) >= 20:
                urge += URGE_LONG
        if at_bot or replied:
            urge += URGE_AT_BOT
        urge += min(max(float(heat or 0.0), 0.0) * URGE_HEAT_COEF, URGE_HEAT_CAP)
        urge = min(urge, self._cap)
        self._urge[chat_key] = urge
        self._ts[chat_key] = now
        return urge

    def get(self, chat_key: str, now: float | None = None) -> float:
        return self._decayed(chat_key, float(now) if now else time.time())

    def set(self, chat_key: str, value: float) -> None:
        """直接设置冲动值（管理/测试用途）。"""
        self._urge[chat_key] = min(max(float(value), 0.0), self._cap)
        self._ts[chat_key] = time.time()

    def reset(self, chat_key: str) -> None:
        """冲动值清零（成功开口后调用）。"""
        self._urge[chat_key] = 0.0
        self._ts[chat_key] = time.time()

    def decay(self, chat_key: str, factor: float = 0.5) -> None:
        """按系数衰减（speak 失败/AI 沉默后调用，避免下一轮立即重试）。"""
        now = time.time()
        self._urge[chat_key] = self._decayed(chat_key, now) * factor
        self._ts[chat_key] = now

    def items(self) -> dict[str, float]:
        """全部会话的当前冲动值快照。"""
        now = time.time()
        return {k: round(self._decayed(k, now), 4) for k in list(self._urge)}


class ProactiveLoop:
    """主动发言检查循环。Main 经 spawn 运行 ``loop.run()``，on_unload 取消任务。"""

    STARTUP_DELAY = 60.0
    JITTER = (0.7, 1.3)
    MIN_INTERVAL_SECONDS = 300.0  # 检查间隔下限（防误配成秒级）

    def __init__(self, sdk, config_getter, window: MessageWindow, urge: UrgeState,
                 logger=None, fewshot=None, startup_delay: float | None = None):
        self.sdk = sdk
        self._cfg_get = config_getter
        self.window = window
        self.urge = urge
        self.logger = logger

        self.fewshot = fewshot
        self._startup_delay = self.STARTUP_DELAY if startup_delay is None else float(startup_delay)
        self._task: asyncio.Task | None = None
        self._stopped = False
        self._memory: dict[str, object] = {}  # storage 不可用时的内存兜底

    # ==================== 循环 ====================

    def start(self, spawner=None) -> "asyncio.Task | None":
        """启动循环；spawner 优先（BaseModule.spawn，任务归属本模块），否则 ensure_future。"""
        if self._task is not None and not self._task.done():
            return self._task
        coro = self.run()
        if spawner is not None:
            try:
                self._task = spawner(coro)
                return self._task
            except Exception:
                pass
        try:
            self._task = asyncio.ensure_future(coro)
        except RuntimeError:  # 无事件循环（纯构造场景）
            self._task = None
        return self._task

    async def stop(self) -> None:
        """取消并等待循环收尾。"""
        self._stopped = True
        task = self._task
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        self._task = None

    async def run(self) -> None:
        """主循环：间隔 = check_interval_minutes × uniform(0.7, 1.3)。"""
        try:
            await asyncio.sleep(self._startup_delay)
        except asyncio.CancelledError:
            return
        while not self._stopped:
            try:
                await self.check()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                self._log("debug", f"proactive check error: {e}")
            await asyncio.sleep(self._next_interval())

    def _next_interval(self) -> float:
        cfg = self._cfg_get()
        base_minutes = max(int(getattr(cfg, "check_interval_minutes", 30) or 30), 1)
        lo, hi = self.JITTER
        return max(base_minutes * 60 * random.uniform(lo, hi), self.MIN_INTERVAL_SECONDS)

    # ==================== 门槛判定 ====================

    async def check(self, now: float | None = None) -> dict:
        """执行一轮检查；返回 ``{"checked": n, "spoke": bool, "chat_key": str, "skipped": {...}}``。"""
        now = float(now) if now else time.time()
        cfg = self._cfg_get()
        skipped: dict[str, str] = {}

        if self.is_asleep(cfg, now):
            return {"checked": 0, "spoke": False, "chat_key": "", "skipped": {"*": "asleep"}}
        daily = self._daily(now)
        global_max = int(getattr(cfg, "global_max_per_day", 3) or 0)
        if global_max > 0 and int(daily.get("global", 0)) >= global_max:
            return {"checked": 0, "spoke": False, "chat_key": "", "skipped": {"*": "global_daily_limit"}}

        urge_threshold = float(getattr(cfg, "urge_threshold", 1.0) or 0.0)
        min_silence = float(getattr(cfg, "min_silence_hours", 6) or 0) * 3600
        per_chat_daily = int(getattr(cfg, "per_chat_daily", 1) or 1)
        cooldown = float(getattr(cfg, "unreplied_cooldown_hours", 12) or 0) * 3600
        last_speak = self._last_speak_map()

        candidates: list[tuple[str, float]] = []
        for chat_key in self._candidate_keys():
            urge = self.urge.get(chat_key, now)
            if urge < urge_threshold:
                continue
            last = float(last_speak.get(chat_key, 0) or 0)
            if last > 0 and now - last < min_silence:
                skipped[chat_key] = "silence_gate"
                continue
            if per_chat_daily > 0 and int(daily.get("chats", {}).get(chat_key, 0)) >= per_chat_daily:
                skipped[chat_key] = "chat_daily_limit"
                continue
            if last > 0 and cooldown > 0:
                if self.window.last_ts(chat_key) <= last and now - last < cooldown:
                    skipped[chat_key] = "unreplied_cooldown"
                    continue
            candidates.append((chat_key, urge))

        result = {"checked": len(candidates), "spoke": False, "chat_key": "", "skipped": skipped}
        if not candidates:
            return result

        # 一轮最多开口一次，取冲动值最高的会话
        candidates.sort(key=lambda x: x[1], reverse=True)
        chat_key, urge = candidates[0]
        result["chat_key"] = chat_key
        result["spoke"] = await self._speak(cfg, chat_key, urge, now)
        return result

    def _candidate_keys(self) -> list[str]:
        return [k for k in self.window.chat_keys() if is_group_key(k)]

    def is_asleep(self, cfg, now: float | None = None) -> bool:
        """作息判定：sleep_hour == wake_hour（或任一为负）表示不限制。"""
        sleep_hour = int(getattr(cfg, "sleep_hour", 2))
        wake_hour = int(getattr(cfg, "wake_hour", 8))
        if sleep_hour < 0 or wake_hour < 0 or sleep_hour == wake_hour:
            return False
        hour = datetime.fromtimestamp(now if now else time.time()).hour
        if sleep_hour > wake_hour:  # 跨午夜
            return hour >= sleep_hour or hour < wake_hour
        return sleep_hour <= hour < wake_hour

    # ==================== 发送 ====================

    async def _speak(self, cfg, chat_key: str, urge: float, now: float) -> bool:
        """调用 QvQChat.speak；失败防御 + warning，不减配额。"""
        messages = self.window.count(chat_key)
        reason = texts.t("proactive_reason", urge=urge,
                         threshold=float(getattr(cfg, "urge_threshold", 1.0) or 0.0), n=messages)
        prompt = self.window.snapshot_text(chat_key, 10)
# H8：主动发言同样带语气锚（模仿口吻，不照抄内容）
        if self.fewshot is not None and bool(getattr(self._cfg_get(), "fewshot_enabled", True)):
            try:
                examples = self.fewshot.examples_for(chat_key)
                if examples:
                    from . import texts as _texts
                    prompt += "\n\n" + _texts.t(
                        "fewshot_style_note",
                        examples="\n".join("- " + e for e in examples),
                    )
            except Exception:
                pass
        try:
            if self.sdk is None:
                raise RuntimeError("sdk unavailable")
            await self.sdk.module.call(
                "QvQChat", "speak",
                chat_key=chat_key, reason=reason, prompt=prompt,
                timeout=CALL_TIMEOUT,
            )
        except Exception as e:
            self._log("warning", texts.t("speak_failed", error=str(e)))
            self.urge.decay(chat_key, 0.5)  # 避免下一轮立即重试
            return False

        # 成功：冲动释放 + 计数 + 记录开口时间（未回复冷却判断依据）
        self.urge.reset(chat_key)
        self._bump_daily(chat_key, now)
        self._record_speak(chat_key, now)
        self._log("info", texts.t("proactive_sent", chat_key=chat_key, urge=urge))
        return True

    # ==================== 持久化（storage 优先，内存兜底） ====================

    def _store_get(self, key: str, default):
        try:
            storage = getattr(self.sdk, "storage", None)
            if storage is not None:
                return storage.get(key, default)
        except Exception:
            pass
        return self._memory.get(key, default)

    def _store_set(self, key: str, value) -> None:
        try:
            storage = getattr(self.sdk, "storage", None)
            if storage is not None:
                storage.set(key, value)
                return
        except Exception:
            pass
        self._memory[key] = value

    def _daily(self, now: float) -> dict:
        date = datetime.fromtimestamp(now).strftime("%Y-%m-%d")
        data = self._store_get(DAILY_KEY.format(date=date), {})
        return data if isinstance(data, dict) else {}

    def _bump_daily(self, chat_key: str, now: float) -> None:
        date = datetime.fromtimestamp(now).strftime("%Y-%m-%d")
        key = DAILY_KEY.format(date=date)
        data = self._daily(now)
        data["global"] = int(data.get("global", 0) or 0) + 1
        chats = data.get("chats")
        chats = chats if isinstance(chats, dict) else {}
        chats[chat_key] = int(chats.get(chat_key, 0) or 0) + 1
        data["chats"] = chats
        self._store_set(key, data)

    def _last_speak_map(self) -> dict:
        data = self._store_get(LAST_SPEAK_KEY, {})
        return data if isinstance(data, dict) else {}

    def _record_speak(self, chat_key: str, now: float) -> None:
        data = self._last_speak_map()
        data[chat_key] = now
        self._store_set(LAST_SPEAK_KEY, data)

    def _log(self, level: str, message: str) -> None:
        logger = self.logger
        if logger is None:
            return
        try:
            getattr(logger, level, logger.debug)(message)
        except Exception:
            pass
