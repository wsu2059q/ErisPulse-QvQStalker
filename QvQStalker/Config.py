"""QvQStalker 配置（dataclass + metadata，组织方式对照 QvQChat/Config.py 独立实现）。

metadata 的 description.i18n 键形如 ``QvQStalker.<key>``，ui 提供
Dashboard 设置表单所需的 widget/group/order 信息。
"""

from __future__ import annotations

from dataclasses import dataclass, field

try:  # 独立运行/测试环境没有 ErisPulse 时仍可实例化配置
    from ErisPulse.Core.Bases import BaseConfig
except Exception:  # pragma: no cover - 环境兜底
    BaseConfig = object  # type: ignore[assignment, misc]


def _desc(key: str, text: str, group: str, order: int, widget: str = "text",
          options: list | None = None) -> dict:
    ui: dict = {"widget": widget, "group": group, "order": order}
    if options is not None:
        ui["options"] = options
    return {"description": {"i18n": f"QvQStalker.{key}", "default": text}, "ui": ui}


@dataclass
class QvQStalkerConfig(BaseConfig):
    # ---- few-shot 双池（拟人化总纲条 7） ----
    fewshot_enabled: bool = field(
        default=True,
        metadata=_desc("fewshot_enabled", "沉淀反响好的发言为语气样例，注入插话判定与主动发言提示词", "fewshot", 1, "switch"),
    )
    fewshot_base_cap: int = field(
        default=20,
        metadata=_desc("fewshot_base_cap", "基准池容量：装满后封池不再轮换（稳定语气锚，0 关闭基准池）", "fewshot", 2, "number"),
    )
    fewshot_inc_cap: int = field(
        default=30,
        metadata=_desc("fewshot_inc_cap", "每群增量池容量：只保留最近 7 天的合格句，自动周轮换（0 关闭增量池）", "fewshot", 3, "number"),
    )

    # ---- 插话判定（五层） ----
    mode: str = field(
        default="balanced",
        metadata=_desc("mode", "参与模式：conservative 保守（不随机插话，仅提问层生效）/ balanced 平衡 / active 积极（基础概率翻倍）",
                       "decision", 1, "select", ["conservative", "balanced", "active"]),
    )
    default_probability: float = field(
        default=0.03,
        metadata=_desc("default_probability", "基础插话概率（balanced 档位值；active 档位翻倍，conservative 档位归零）",
                       "decision", 2, "number"),
    )
    question_prob: float = field(
        default=0.6,
        metadata=_desc("question_prob", "提问消息的回复概率（话题热度 > 0.5 时 +0.3，上限 0.95）", "decision", 3, "number"),
    )
    min_messages_between_replies: int = field(
        default=15,
        metadata=_desc("min_messages_between_replies", "两次插话尝试之间至少观察多少条消息（防止刷屏）", "decision", 4, "number"),
    )
    silence_threshold_minutes: float = field(
        default=30.0,
        metadata=_desc("silence_threshold_minutes", "沉寂唤醒阈值（分钟）：本条消息打破的沉寂时长超过该值时视为沉寂唤醒", "decision", 5, "number"),
    )
    heat_flag_threshold: float = field(
        default=0.8,
        metadata=_desc("heat_flag_threshold", "热度标志阈值：话题热度超过该值时按概率升级到 LLM 判断", "decision", 6, "number"),
    )

    # ---- LLM 判断（L5） ----
    llm_judge: bool = field(
        default=False,
        metadata=_desc("llm_judge", "开启后，热度/沉寂升级条件触发时交给小模型单问判断（不开启则按概率层结果）",
                       "judge", 1, "switch"),
    )
    judge_model: str = field(
        default="",
        metadata=_desc("judge_model", "L5 判断使用的模型（模型池 id），留空跟随 QvQChat 默认模型（经 QvQChat.chat 服务调用，待 T15 专用入口落地后完全生效）",
                       "judge", 2),
    )
    judge_timeout: float = field(
        default=8.0,
        metadata=_desc("judge_timeout", "L5 小模型判断超时（秒），超时按概率层结果处理", "judge", 3, "number"),
    )

    # ---- 话题热度 ----
    heat_cap: float = field(
        default=2.0,
        metadata=_desc("heat_cap", "话题热度上限", "heat", 1, "number"),
    )
    heat_bonus_cap: float = field(
        default=0.15,
        metadata=_desc("heat_bonus_cap", "概率层的热度加成上限（加成 = min(热度×0.05, 该值)）", "heat", 2, "number"),
    )

    # ---- 主动发言（冲动值 loop） ----
    sleep_hour: int = field(
        default=2,
        metadata=_desc("sleep_hour", "主动发言作息：入睡小时（0-23，与 wake_hour 相等表示不启用作息限制）", "proactive", 1, "number"),
    )
    wake_hour: int = field(
        default=8,
        metadata=_desc("wake_hour", "主动发言作息：起床小时（0-23）", "proactive", 2, "number"),
    )
    urge_threshold: float = field(
        default=1.0,
        metadata=_desc("urge_threshold", "冲动值阈值：会话冲动值达到该值才有资格主动开口", "proactive", 3, "number"),
    )
    global_max_per_day: int = field(
        default=3,
        metadata=_desc("global_max_per_day", "全局每日主动发言上限（所有会话合计）", "proactive", 4, "number"),
    )
    per_chat_daily: int = field(
        default=1,
        metadata=_desc("per_chat_daily", "单会话每日主动发言上限", "proactive", 5, "number"),
    )
    unreplied_cooldown_hours: float = field(
        default=12.0,
        metadata=_desc("unreplied_cooldown_hours", "未回复冷却（小时）：上次主动开口后没有任何新消息期间不再主动开口",
                       "proactive", 6, "number"),
    )
    min_silence_hours: float = field(
        default=6.0,
        metadata=_desc("min_silence_hours", "沉寂门槛（小时）：距上次主动开口不足该时长时不再次主动开口", "proactive", 7, "number"),
    )
    check_interval_minutes: int = field(
        default=30,
        metadata=_desc("check_interval_minutes", "主动发言检查间隔（分钟），实际间隔会乘以 0.7-1.3 的随机抖动", "proactive", 8, "number"),
    )

    # ---- 旁听窗口 ----
    window_size: int = field(
        default=50,
        metadata=_desc("window_size", "每会话旁听窗口大小（内存环形缓冲保留的最近消息条数）", "observe", 1, "number"),
    )
