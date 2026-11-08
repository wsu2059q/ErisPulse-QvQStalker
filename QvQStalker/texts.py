"""QvQStalker 双语文案（zh-CN / en）。

组织方式参考 QvQChat/texts.py，但完全独立：
- ``STRINGS``：key → {lang → 带占位符模板}
- ``t(key, **kwargs)``：取当前语言模板并格式化；ErisPulse i18n
  可用时优先走框架翻译（register_texts 注册），否则退回本地表。
"""

from __future__ import annotations

DOMAIN = "QvQStalker"

STRINGS: dict[str, dict[str, str]] = {
    # ---- 决策 reason（Dashboard "为什么说话/为什么沉默" 展示用，必须人类可读） ----
    "fewshot_style_note": {
        "zh-CN": "这个号过往发言里反响好的句子（只模仿口吻和节奏，禁止照抄内容）：\n{examples}",
        "en": "Past replies of this account that landed well (imitate only the tone and rhythm, never copy content):\n{examples}",
    },
    "reason_invalid_ctx": {
        "zh-CN": "无效的决策上下文（缺少 ctx 或 chat_key），保持沉默",
        "en": "Invalid decision context (missing ctx or chat_key); staying silent",
    },
    "reason_not_group": {
        "zh-CN": "非群聊消息（QvQStalker 仅在群聊中插话）",
        "en": "Not a group message (QvQStalker only speaks up in group chats)",
    },
    "reason_at_bot": {
        "zh-CN": "消息 @ 了机器人，属于确定性触发，直接参与",
        "en": "Message @-mentioned the bot; deterministic trigger, join directly",
    },
    "reason_question_hit": {
        "zh-CN": "提问消息命中关键词（热度 {heat:.2f}），以 {prob:.2f} 概率参与",
        "en": "Question keywords matched (heat {heat:.2f}); joining with probability {prob:.2f}",
    },
    "reason_prob_hit": {
        "zh-CN": "概率命中：基础概率 {prob:.3f}（模式 {mode}，热度 {heat:.2f} 加成后）",
        "en": "Probability hit: base {prob:.3f} (mode {mode}, with heat {heat:.2f} bonus)",
    },
    "reason_prob_miss": {
        "zh-CN": "概率未命中（{prob:.3f}，模式 {mode}，热度 {heat:.2f}），继续旁观",
        "en": "Probability missed ({prob:.3f}, mode {mode}, heat {heat:.2f}); keep lurking",
    },
    "reason_below_min": {
        "zh-CN": "距上次插话尝试仅 {count} 条消息（阈值 {need}），继续旁观",
        "en": "Only {count} messages since last attempt (threshold {need}); keep lurking",
    },
    "reason_conservative": {
        "zh-CN": "保守模式（conservative）：基础插话概率归零，仅提问层生效",
        "en": "Conservative mode: base join probability is zero; only the question layer applies",
    },
    "reason_strict": {
        "zh-CN": "超保守模式（strict）：未被点名，保持沉默",
        "en": "Strict mode: not directly addressed; stay silent",
    },
    "reason_strict_addressed": {
        "zh-CN": "超保守模式（strict）：被点名（@/回复机器人），直接参与",
        "en": "Strict mode: directly addressed (@/reply to the bot); join",
    },
    "reason_llm_speak": {
        "zh-CN": "触发 {source}，小模型判断「该接这句话」",
        "en": "{source} triggered; small-model judge says join",
    },
    "reason_llm_silent": {
        "zh-CN": "触发 {source}，小模型判断「不该接这句话」",
        "en": "{source} triggered; small-model judge says stay silent",
    },
    "source_heat": {
        "zh-CN": "话题热度标志（热度 {heat:.2f}）",
        "en": "topic heat flag (heat {heat:.2f})",
    },
    "source_silence": {
        "zh-CN": "沉寂唤醒（沉寂 {minutes:.0f} 分钟）",
        "en": "silence wake-up ({minutes:.0f} min of silence)",
    },
    "judge_prompt": {
        "zh-CN": (
            "以下是群聊最近的旁听记录（每行一条，格式 昵称(id): 内容；带 [bot] 前缀的是机器人）:\n{window}\n\n"
            "当前消息：{sender}: {text}\n{mention_note}\n"
            "你是这个群里的普通成员（一个机器人），这条消息没有点你的名，你在考虑要不要像群友一样自然地搭句话。"
            "判断时注意：\n"
            "1. 先分辨对方是在跟别人聊话题，还是在向你提问。只有当你能给出对方真正需要的回应（答案、补充、共情）时才值得插话；插不上话就保持安静，安静是常态。\n"
            "2. 消息若引用回复了某条旧消息：话题几乎总是关于被引用的那条内容，去旁听记录里找到那条内容来理解问题即可，不存在「不知道在问哪条」的情况。\n"
            "3. 决定插话时，语气要像群友随口接话：口语化、简短、有来有回；不要书面腔，不要长篇大论。\n"
            "4. 群里可能还有别的机器人：除了 [bot] 标注的，从行为也能看出来——响应用户命令出签到卡/运势卡/骰子图的、发言机械模板化的，多半就是。它们的娱乐行为（签到结果、抽签运势）是群里的趣事，可以像围观群众一样吐槽逗一句（打趣它的运势、问它是谁家的小机器人），但只是评论，不替它回答、不接管话题。\n\n"
            "请判断此刻插话是否自然、有价值。只回答「是」或「否」，不要输出任何其他内容。"
        ),
        "en": (
            "Recent group messages overheard (one per line, format name(id): text; lines prefixed [bot] are bots):\n{window}\n\n"
            "Current message: {sender}: {text}\n{mention_note}\n"
            "You are an ordinary member of this group (a bot). This message does not address you; you are deciding whether to join in naturally, like a fellow group member. Keep in mind:\n"
            "1. First tell whether the sender is chatting with others or asking you. Speak up only when you can give the response they actually need (an answer, a supplement, empathy); staying silent is the norm.\n"
            "2. If the message quote-replies to an older one, the topic is almost always about the quoted content - find it in the overheard log; there is no unrecorded-message confusion.\n"
            "3. If you do join in, talk like a casual group member: colloquial, brief, conversational; no formal tone, no walls of text.\n"
            "4. There may be other bots here: besides those marked [bot], you can tell from behavior - accounts that respond to commands with check-in/fortune/dice cards or post in mechanical template patterns are likely bots. Their fun moments (check-in results, draw fortunes) are group entertainment: feel free to tease like an onlooker (joke about their luck, ask whose little bot they are), but only comment - don't answer for them or take over the topic.\n\n"
            "Decide whether joining now feels natural and useful. Answer with exactly YES or NO."
        ),
    },
    "mention_note_direct": {
        "zh-CN": "（注意：这条消息 @ 了你，是在向你提问或点名，回复它就是你分内的事。）",
        "en": "(Note: this message mentions you - answering it is your job.)",
    },
    "mention_note_none": {
        "zh-CN": "（注意：这条消息没有提到你，你只是旁听后考虑要不要主动搭话。）",
        "en": "(Note: this message does not mention you; you are merely considering jumping in after overhearing.)",
    },
    # ---- bot 互逗（纯提示词认知：judge_prompt 第 4 条，无结构化分支） ----
    # ---- 主动发言 ----
    "proactive_reason": {
        "zh-CN": "[QvQStalker] 冲动值 {urge:.2f} ≥ {threshold}（{n} 条旁听累积），主动发言",
        "en": "[QvQStalker] urge {urge:.2f} >= {threshold} (accumulated from {n} overheard messages); speaking proactively",
    },
    "speak_failed": {
        "zh-CN": "QvQChat.speak 调用失败（服务尚未实现或不可达），本轮主动发言放弃：{error}",
        "en": "QvQChat.speak call failed (service not implemented or unreachable); giving up this round: {error}",
    },
    "proactive_sent": {
        "zh-CN": "主动发言已提交至 QvQChat.speak（{chat_key}，冲动值 {urge:.2f}）",
        "en": "Proactive speech submitted to QvQChat.speak ({chat_key}, urge {urge:.2f})",
    },
    # ---- 服务 ----
    "request_speak_default_reason": {
        "zh-CN": "编排/工具请求主动发言",
        "en": "proactive speech requested via orchestration/tool",
    },
    "request_speak_failed": {
        "zh-CN": "request_speak 调用 QvQChat.speak 失败：{error}",
        "en": "request_speak failed to call QvQChat.speak: {error}",
    },
    # ---- 加载/生命周期 ----
    "observe_registered": {
        "zh-CN": "已订阅 {event} 事件（若 QvQChat 尚未实现该事件，将在其落地后自动生效）",
        "en": "Subscribed to {event} (takes effect automatically once QvQChat emits it)",
    },
    "observe_register_failed": {
        "zh-CN": "订阅 {event} 失败（框架 lifecycle 不可用）：{error}",
        "en": "Failed to subscribe {event} (lifecycle unavailable): {error}",
    },
    "loop_started": {
        "zh-CN": "主动发言循环已启动（检查间隔 {minutes} 分钟 × 0.7-1.3 抖动）",
        "en": "Proactive loop started (check every {minutes} min with 0.7-1.3 jitter)",
    },
}

DEFAULT_LANG = "zh-CN"
_lang = DEFAULT_LANG


def set_language(lang: str) -> None:
    """设置默认语言（zh-CN / en）。"""
    global _lang
    if lang in (DEFAULT_LANG, "en"):
        _lang = lang


def register_texts() -> None:
    """把文案注册进 ErisPulse i18n（框架可用时）；失败静默（本地表兜底）。"""
    try:
        from ErisPulse.Core import i18n

        for lang, table in _by_lang().items():
            i18n.register(lang, {f"{DOMAIN}.{key}": text for key, text in table.items()}, domain=DOMAIN)
    except Exception:
        pass


def _by_lang() -> dict[str, dict[str, str]]:
    langs: dict[str, dict[str, str]] = {}
    for key, translations in STRINGS.items():
        for lang, text in translations.items():
            langs.setdefault(lang, {})[key] = text
    return langs


def t(key: str, default: str | None = None, lang: str | None = None, **kwargs) -> str:
    """取文案并格式化占位符。

    优先走 ErisPulse i18n（已注册时），失败/未安装时退回本地 STRINGS。
    """
    table = STRINGS.get(key, {})
    text = None
    if default is None:
        default = table.get("en", key)
    try:
        from ErisPulse.Core import i18n

        text = i18n.t(f"{DOMAIN}.{key}", default=default, **kwargs)
    except Exception:
        text = None
    if not text or text == default:
        # i18n 未注册/不可用时回退本地表
        text = table.get(lang or _lang) or table.get(DEFAULT_LANG) or default
    if kwargs and "{" in text:
        try:
            text = text.format(**kwargs)
        except (KeyError, IndexError, ValueError):
            pass
    return text
