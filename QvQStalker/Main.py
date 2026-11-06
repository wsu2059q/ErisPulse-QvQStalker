"""QvQStalker 主模块（BaseModule 子类）。

与 QvQChat 的契约（T15 扩展点，双方均防御式对接）：
- 订阅 lifecycle 事件 ``qvq.observe``：QvQChat 在命令过滤后、触发判定前
  广播的纯数据载荷（chat_key/platform/detail_type/group_id/sender_id/
  sender_name/text/is_group/at_bot/ts）。事件尚不存在时本模块照常加载，
  落地后自动生效。
- 提供约定服务 ``qvq_decision(ctx)``：QvQChat 的 ParticipationManager
  在未命中确定性触发且群聊时问询"是否插话"。
- 调用 ``QvQChat.speak``：主动发言唯一出口（QvQChat 侧 SpeechBudget
  预算护栏，插件不可绕过）；服务不存在时 try/except 降级。

零 QvQChat 包依赖：仅经 sdk.lifecycle / sdk.module.call / sdk.storage 交互。
"""

from __future__ import annotations

import asyncio
import time

try:
    from ErisPulse import sdk
except ImportError:  # 独立运行/测试环境兜底
    sdk = None

try:
    from ErisPulse.Core.Bases import BaseModule
except ImportError:  # pragma: no cover
    BaseModule = object  # type: ignore[assignment, misc]

try:
    from ErisPulse.Core.Bases import ModuleMeta
except ImportError:  # pragma: no cover
    ModuleMeta = None

try:
    from ErisPulse.loaders import ModuleLoadStrategy
except ImportError:  # pragma: no cover
    ModuleLoadStrategy = None

from . import texts
from .Config import QvQStalkerConfig
from .decision import DecisionProvider
from .heat import TopicHeat
from .fewshot import FewShotPools
from .observe import MessageWindow
from .proactive import ProactiveLoop, UrgeState

__version__ = "0.2.0"

OBSERVE_EVENT = "qvq.observe"
REPLIED_EVENT = "qvq.replied"
# QvQChat 插件设置：现值拉取服务 / 热更新事件（事件尚不存在时订阅静默失败，落地后生效）
PLUGIN_CONFIG_SERVICE = "plugin_config"
PLUGIN_CONFIG_EVENT = "qvq.plugin_config"

# qvq_settings 导出的行为参数键（default 取 Config 字段默认；显示顺序即此顺序）
# 插件设置覆盖合并的键过滤白名单（on_load 拉取与 qvq.plugin_config 热更新共用）
SETTINGS_KEYS = [
    "mode", "default_probability", "question_prob", "silence_threshold_minutes",
    "min_messages_between_replies", "llm_judge", "judge_model", "sleep_hour",
    "wake_hour", "urge_threshold", "global_max_per_day", "per_chat_daily",
    "unreplied_cooldown_hours", "check_interval_minutes", "window_size",
    "fewshot_enabled", "fewshot_base_cap", "fewshot_inc_cap",
]

# services 白名单：框架 sdk.module.call 严格校验，未声明即拒绝——五个约定服务必须列全
SERVICES = [
    {
        "name": "qvq_decision",
        "description": "群聊参与决策：输入消息上下文 ctx（chat_key/text/sender/is_group/at_bot/ts），"
                       "返回 {decision: speak|silent, reason, score, urgency}",
        "signature": "(ctx: dict) -> dict",
    },
    {
        "name": "group_heat",
        "description": "查询会话当前话题热度（0~上限 2.0）；不带 chat_key 时返回全部会话",
        "signature": "(chat_key: str = '') -> dict",
    },
    {
        "name": "urge_status",
        "description": "查询会话主动发言冲动值（含半衰期衰减后的现值与阈值）；不带 chat_key 时返回全部会话",
        "signature": "(chat_key: str = '') -> dict",
    },
    {
        "name": "request_speak",
        "description": "请求向指定会话主动发言（编排/AI 工具入口，仍经 QvQChat.speak 预算护栏）",
        "signature": "(chat_key: str, reason: str = '', prompt: str = '') -> dict",
    },
    {
        "name": "fewshot_status",
        "description": "few-shot 双池状态：基准池/增量池容量与当前条数（反响好的发言语气样例）",
        "signature": "() -> dict",
    },
    {
        "name": "qvq_settings",
        "description": "归属标记：声明本模块出现在 QvQChat 设置页插件分区（配置项由本模块 ConfigClass 声明，框架 schema 自动呈现）",
        "signature": "() -> dict",
    },
    {
        "name": "qvq_schemas",
        "description": "约定服务：给 QvQChat 生态桥提供本模块服务的参数级 schema",
        "signature": "() -> dict",
    },
    {
        "name": "qvq_access",
        "description": "约定服务：自声明访问级别（只读状态对所有人开放，主动发言仅管理员）",
        "signature": "() -> dict",
    },
    {
        "name": "qvq_extensions",
        "description": "约定服务：向 QvQChat 注入参与决策的使用建议（提示词）",
        "signature": "() -> dict",
    },
]

# 参数级 schema：仅靠签名推导时模型不知道参数含义
SCHEMAS = {
    "group_heat": {
        "type": "object",
        "properties": {
            "chat_key": {"type": "string", "description": "会话键（platform:group:gid）；留空返回全部会话"},
        },
        "required": [],
    },
    "urge_status": {
        "type": "object",
        "properties": {
            "chat_key": {"type": "string", "description": "会话键；留空返回全部会话"},
        },
        "required": [],
    },
    "request_speak": {
        "type": "object",
        "properties": {
            "chat_key": {"type": "string", "description": "目标会话键"},
            "reason": {"type": "string", "description": "为什么要主动开口（记入决策日志）"},
            "prompt": {"type": "string", "description": "可选的发言提示（留空用默认插话提示）"},
        },
        "required": ["chat_key"],
    },
    "qvq_decision": {
        "type": "object",
        "properties": {
            "ctx": {"type": "object", "description": "消息上下文：{chat_key, text, sender, is_group, at_bot, ts}"},
        },
        "required": ["ctx"],
    },
}

# 访问级别自声明：查询类对所有人开放；主动发言会真的发消息，保持管理员
ACCESS = {
    "qvq_decision": "admin",
    "group_heat": "safe",
    "urge_status": "safe",
    "fewshot_status": "safe",
    "request_speak": "admin",
}

# 注入 QvQChat 系统提示的使用建议：AI 自行判断，不强制
EXTENSION_PROMPTS = [
    {
        "text": (
            "社交说话方式（像真人一样聊天）：匹配对方的能量——对方发三个字，你也别回三百字；"
            "真人不会突然打一大段字，除非真有特别的事或对方明确要详细展开，闲聊默认短回复。"
            "允许碎片化的口语回复（\"哈哈哈\"\"确实\"\"？\"这样单发也没问题）。"
            "镜像对方风格：看对方最近消息的长度和密度——短句少表情你也短句少表情，对方爱玩梗你可以接梗但别硬凹。"
            "禁 AI 腔：不写\"很高兴帮助你\"\"总的来说\"\"希望这能帮到你\"这类客服话术；"
            "闲聊不用列表、小标题，不在结尾强行总结。"
        ),
        "priority": 5,
    },
    {
        "text": (
            "社交关系感知：如果上下文、记忆或画像显示你和对方已经互动过很多次，那就是熟人——"
            "语气可以随意些、能开玩笑、能自然提起之前聊过的事；只有首次接触才需要礼貌分寸。"
            "对方在吐槽或情绪低落时，先接住情绪再说别的，别急着列解决方案——有时候人只是想说说话。"
            "画像里对方提过但还没下文的事（TODO 类条目）可以自然跟进一句（\"上次说的那个考试咋样了\"），"
            "像朋友随口关心，不像查户口。"
        ),
        "priority": 5,
    },
    {
        "text": (
            "群聊参与决策由 QvQStalker 负责：被它唤醒插话时，只需像群友一样自然接一句，不要长篇解答；"
            "需要主动开口用 request_speak（仍受发言预算与冷却限制），不要自行连续主动刷屏。"
            "group_heat / urge_status 可查看话题热度与冲动值，决定是否值得开口。"
        ),
        "priority": 4,
    },
]


class _NullLogger:
    """sdk.logger 不可用时的静默兜底。"""

    def debug(self, *a, **k):
        pass

    info = warning = error = debug


class Main(BaseModule):

    ConfigClass = QvQStalkerConfig

    def __init__(self):
        self.sdk = sdk
        self.logger = self._make_logger()
        self.window: MessageWindow | None = None
        self.heat: TopicHeat | None = None
        self.urge: UrgeState | None = None
        self.provider: DecisionProvider | None = None
        self.proactive: ProactiveLoop | None = None
        self._local_cfg = None  # 测试/独立运行注入的配置（优先于框架 cfg）
        self._observe_registered = False
        self._replied_registered = False
        self._plugin_config_registered = False

    # ==================== 元信息 ====================

    @staticmethod
    def get_meta():
        info = dict(
            name="QvQStalker",
            description="ErisPulse 群聊参与决策插件：旁听 qvq.observe、五层插话判定、冲动值主动发言（QvQChat 伴侣模块）",
            version=__version__,
            group="智能",
            tags=["stalker", "group", "participation", "qvq"],
            services=[dict(s) for s in SERVICES],
            min_sdk_version="2.8.0",
        )
        if ModuleMeta is not None:
            try:
                return ModuleMeta(**info)
            except Exception:
                return info
        return info

    @staticmethod
    def get_load_strategy():
        # 晚于 QvQChat（priority=50）加载：运行时依赖其 speak/chat 服务
        strategy = dict(lazy_load=False, priority=30)
        if ModuleLoadStrategy is not None:
            try:
                return ModuleLoadStrategy(**strategy)
            except Exception:
                pass
        return strategy

    # ==================== 生命周期 ====================

    async def on_load(self, event) -> bool:
        try:
            texts.register_texts()
        except Exception:
            pass
# 插件设置覆盖先于组件构造拉取（window_size 等构造期读取的键也要吃到覆盖）
        await self._pull_plugin_overrides()
        cfg = self._cfg()
        self.window = MessageWindow(size=int(getattr(cfg, "window_size", 50) or 50))
        self.heat = TopicHeat(cap=float(getattr(cfg, "heat_cap", 2.0) or 2.0))
        self.urge = UrgeState()
        self.fewshot = FewShotPools(
            self.sdk.storage, logger=self.logger,
            base_cap=int(getattr(cfg, "fewshot_base_cap", 20) or 0),
            inc_cap=int(getattr(cfg, "fewshot_inc_cap", 30) or 0),
        )
        self.provider = DecisionProvider(self._cfg, self.heat, self.window,
                                         judge=self._judge, logger=self.logger,
                                         fewshot=self.fewshot)
        self.proactive = ProactiveLoop(self.sdk, self._cfg, self.window, self.urge,
                                       logger=self.logger, fewshot=self.fewshot)

        # 订阅 qvq.observe（事件可能尚不存在——订阅与回调全防御）
        try:
            self.sdk.lifecycle.register(OBSERVE_EVENT, self._on_observe)
            self._observe_registered = True
            self.logger.info(texts.t("observe_registered", event=OBSERVE_EVENT))
        except Exception as e:
            self.logger.warning(texts.t("observe_register_failed", event=OBSERVE_EVENT, error=e))

        # 订阅 qvq.plugin_config（QvQChat 保存插件设置后的热更新广播）
        try:
            self.sdk.lifecycle.register(PLUGIN_CONFIG_EVENT, self._on_plugin_config)
            self._plugin_config_registered = True
        except Exception as e:
            self.logger.debug(f"plugin_config register failed: {e}")

        # H8：订阅 qvq.replied（机器人自己的发言）→ few-shot 待判定
        try:
            self.sdk.lifecycle.register(REPLIED_EVENT, self._on_replied)
            self._replied_registered = True
        except Exception as e:
            self.logger.debug(f"qvq.replied register failed: {e}")

        # 启动主动发言循环（任务归属本模块，on_unload 取消）
        self.proactive.start(spawner=self._spawn)
        self.logger.info(texts.t("loop_started",
                                 minutes=int(getattr(cfg, "check_interval_minutes", 30) or 30)))
        return True

    async def on_unload(self, event) -> bool:
        if self._observe_registered:
            try:
                self.sdk.lifecycle.unregister(OBSERVE_EVENT, self._on_observe)
            except Exception:
                pass
            self._observe_registered = False
        if self._plugin_config_registered:
            try:
                self.sdk.lifecycle.unregister(PLUGIN_CONFIG_EVENT, self._on_plugin_config)
            except Exception:
                pass
            self._plugin_config_registered = False
        if self._replied_registered:
            try:
                self.sdk.lifecycle.unregister(REPLIED_EVENT, self._on_replied)
            except Exception:
                pass
            self._replied_registered = False
        if self.proactive is not None:
            try:
                await self.proactive.stop()
            except Exception:
                pass
        return True

    async def on_config_update(self, old, new) -> None:
        return

    def _spawn(self, coro):
        """优先 BaseModule.spawn（任务登记到模块名下），失败退回 ensure_future。"""
        try:
            return self.spawn(coro)
        except Exception:
            return asyncio.ensure_future(coro)

    # ==================== 观察回调（qvq.observe） ====================

    async def _on_observe(self, data) -> None:
        """旁听入窗：只读载荷入窗口/热度/冲动值，绝不 event.done、绝不抛错。"""
        try:
            if not isinstance(data, dict):
                return
            chat_key = str(data.get("chat_key") or "")
            if not chat_key:
                return
            is_group = data.get("is_group")
            if is_group is None:
                is_group = ":group:" in chat_key
            if not is_group:
                return
            text = str(data.get("text") or "")
            try:
                ts = float(data.get("ts") or 0) or time.time()
            except (TypeError, ValueError):
                ts = time.time()
            entry = self.window.add(
                chat_key, text,
                sender_id=str(data.get("sender_id") or ""),
                sender_name=str(data.get("sender_name") or ""),
                ts=ts,
                at_bot=bool(data.get("at_bot")),
                reply_to_bot=bool(data.get("reply_to_bot")),
                platform=str(data.get("platform") or ""),
            )
            heat = self.heat.update(chat_key, text, now=entry["ts"])
            self.urge.feed(chat_key, text,
                           at_bot=entry["at_bot"], replied=entry["reply_to_bot"],
                           heat=heat, now=entry["ts"])
# H8：他人消息到达 = 潜在回应，尝试收编 pending 的 bot 发言
            if self.fewshot is not None:
                try:
                    self.fewshot.record_reaction(chat_key, entry["ts"])
                except Exception:
                    pass
        except Exception as e:
            try:
                self.logger.debug(f"observe handler error: {e}")
            except Exception:
                pass

    async def _on_replied(self, data):
        """H8：机器人发言落 pending，等 5 分钟内的下一条他人消息判定反响。"""
        try:
            if self.fewshot is None:
                return
            if not isinstance(data, dict):
                return
            chat_key = str(data.get("chat_key") or "")
            reply = str(data.get("reply") or "")
            if chat_key and reply:
                self.fewshot.record_bot_reply(chat_key, reply)
        except Exception as e:
            try:
                self.logger.debug(f"replied handler error: {e}")
            except Exception:
                pass

    # ==================== 对外服务（services 白名单内） ====================

    async def fewshot_status(self, **_) -> dict:
        """观测出口：双池状态 + 指定群的样例（group= 群号）。"""
        if self.fewshot is None:
            return {"ok": False, "error": "fewshot not ready"}
        status = self.fewshot.status()
        return {"ok": True, **status}

    async def qvq_decision(self, ctx: dict | None = None, **_) -> dict:
        """约定服务：是否插话的参与决策（详见 decision.py 五层说明）。"""
        provider = self.provider or self._rebuild()
        return await provider.qvq_decision(ctx)

    async def group_heat(self, chat_key: str = "", **_) -> dict:
        """查询话题热度（0 ~ heat_cap）。"""
        heat = self.heat or TopicHeat()
        if chat_key:
            return {"chat_key": chat_key, "heat": round(heat.get(chat_key), 4)}
        return {"chats": {k: round(heat.get(k), 4) for k in heat.keys()}}

    async def urge_status(self, chat_key: str = "", **_) -> dict:
        """查询冲动值（衰减后现值 + 阈值）。"""
        urge = self.urge or UrgeState()
        cfg = self._cfg()
        threshold = float(getattr(cfg, "urge_threshold", 1.0) or 0.0)
        if chat_key:
            return {
                "chat_key": chat_key,
                "urge": round(urge.get(chat_key), 4),
                "threshold": threshold,
            }
        return {"threshold": threshold, "chats": urge.items()}

    async def request_speak(self, chat_key: str, reason: str = "", prompt: str = "", **_) -> dict:
        """编排/工具入口：请求向会话主动发言（仍走 QvQChat.speak 预算护栏）。"""
        chat_key = str(chat_key or "")
        if not chat_key:
            return {"ok": False, "error": "chat_key required"}
        reason = f"[QvQStalker] {reason or texts.t('request_speak_default_reason')}"
        prompt = prompt or (self.window.snapshot_text(chat_key) if self.window else "")
        try:
            await self.sdk.module.call(
                "QvQChat", "speak",
                chat_key=chat_key, reason=reason, prompt=prompt,
            )
            return {"ok": True, "chat_key": chat_key}
        except Exception as e:
            self.logger.warning(texts.t("request_speak_failed", error=e))
            return {"ok": False, "chat_key": chat_key, "error": str(e)}

    # ==================== 插件设置（QvQChat 约定） ====================

    async def qvq_settings(self, **_) -> dict:
        """约定服务：归属标记——只有返回该服务的模块才出现在 QvQChat 设置页插件分区。

        配置项不在服务里逐条描述：本模块 ConfigClass（_desc 声明式）即唯一事实源，
        QvQChat 端经框架 resolve_config_schema 自动渲染表单、经 sdk.config 读写。
        """
        return {"title": "QvQStalker 群聊参与决策"}

    # ==================== QvQChat 约定服务：schema / 访问级别 / 使用建议 ====================

    async def qvq_schemas(self, **_) -> dict:
        """参数级 schema：生态桥优先用它覆盖「从签名推导」的弱 schema。"""
        return {name: dict(schema) for name, schema in SCHEMAS.items()}

    async def qvq_access(self, **_) -> dict:
        """访问级别自声明：查询类对所有人开放，主动发言保持管理员。"""
        return dict(ACCESS)

    async def qvq_extensions(self, **_) -> dict:
        """统一扩展入口：注入参与决策的使用建议（提示词），AI 自行判断。"""
        return {"prompts": [dict(item) for item in EXTENSION_PROMPTS]}

    async def _pull_plugin_overrides(self) -> None:
        """经 QvQChat.plugin_config 拉取设置现值；服务不存在/失败静默。

        新契约下 getConfig 值即覆盖（{模块名: {k: v}} 嵌套形，兼容裸 {k: v}）。
        """
        try:
# 位置传参：服务参数名与 sdk.module.call 首参撞名时 kwargs 会冲突
            data = await self.sdk.module.call("QvQChat", PLUGIN_CONFIG_SERVICE, "QvQStalker")
        except Exception:
            return
        self._apply_plugin_overrides(self._extract_overrides(data))

    @staticmethod
    def _extract_overrides(data) -> dict:
        """plugin_config 返回 {模块名: {k: v}} 嵌套形；兼容裸 {k: v}（防主体版本差异）。"""
        if not isinstance(data, dict):
            return {}
        inner = data.get("QvQStalker")
        if isinstance(inner, dict):
            return inner
        return {k: v for k, v in data.items() if not isinstance(v, dict)}

    def _apply_plugin_overrides(self, values) -> None:
        """覆盖值合并进 cfg：仅 SETTINGS_KEYS 内且 cfg 存在的键生效，失败静默。"""
        if not isinstance(values, dict):
            return
        cfg = self._cfg()
        for key in SETTINGS_KEYS:
            if key not in values:
                continue
            try:
                if hasattr(cfg, key):
                    setattr(cfg, key, values[key])
            except Exception:
                continue

    async def _on_plugin_config(self, data) -> None:
        """热更新回调：module 匹配才重合并，绝不抛错。"""
        try:
            if not isinstance(data, dict) or str(data.get("module") or "") != "QvQStalker":
                return
            self._apply_plugin_overrides(data.get("values"))
        except Exception:
            pass

    # ==================== L5 小模型入口 ====================

    async def _judge(self, prompt: str) -> str:
        """经 QvQChat.chat 服务单问判断；judge_model 预留（服务不支持该参数时自动去掉重试）。"""
        if self.sdk is None:
            return ""
        cfg = self._cfg()
        kwargs = {"session_id": "QvQStalker:judge", "prompt": prompt}
        judge_model = str(getattr(cfg, "judge_model", "") or "")
        if judge_model:
            kwargs["model"] = judge_model
        try:
            return await self.sdk.module.call("QvQChat", "chat", **kwargs)
        except TypeError:
            # 目标服务不接受 model 参数（当前 QvQChat.chat 签名）——去掉重试
            kwargs.pop("model", None)
            try:
                return await self.sdk.module.call("QvQChat", "chat", **kwargs)
            except Exception as e:
                self.logger.warning(f"judge call failed: {e}")
                return ""
        except Exception as e:
            self.logger.warning(f"judge call failed: {e}")
            return ""

    # ==================== 辅助 ====================

    def _cfg(self):
        """配置获取：测试注入（_local_cfg）> 框架实时 cfg > 兜底默认实例。"""
        local = getattr(self, "_local_cfg", None)
        if local is not None:
            return local
        try:
            cfg = getattr(self, "cfg")
            if cfg is not None:
                return cfg
        except Exception:
            pass
        self._local_cfg = QvQStalkerConfig()
        return self._local_cfg

    def _rebuild(self) -> DecisionProvider:
        """服务在 on_load 前被调用的兜底（懒加载场景）。"""
        cfg = self._cfg()
        if self.window is None:
            self.window = MessageWindow(size=int(getattr(cfg, "window_size", 50) or 50))
        if self.heat is None:
            self.heat = TopicHeat(cap=float(getattr(cfg, "heat_cap", 2.0) or 2.0))
        if self.urge is None:
            self.urge = UrgeState()
        self.provider = DecisionProvider(self._cfg, self.heat, self.window,
                                         judge=self._judge, logger=self.logger)
        return self.provider

    def _make_logger(self):
        try:
            if self.sdk is not None and getattr(self.sdk, "logger", None) is not None:
                return self.sdk.logger.get_child("QvQStalker")
        except Exception:
            pass
        return _NullLogger()
