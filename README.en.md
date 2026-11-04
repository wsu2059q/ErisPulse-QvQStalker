<div align="center">
[简体中文](README.md) | [繁體中文](README.zh-TW.md) | **English** | [日本語](README.ja.md) | [Русский](README.ru.md)

<img src="https://raw.githubusercontent.com/ErisPulse/ErisPulse/main/.github/assets/ErisPulseLogo.png" width="180" alt="QvQStalker" />

<img src="https://raw.githubusercontent.com/ErisPulse/.github/main/assets/badges/module.svg" alt="ErisPulse Module" />
</div>

# ErisPulse-QvQStalker

**A companion plugin for [QvQChat](https://github.com/wsu2059q/ErisPulse-QvQChat): the bot reads the group like a member, decides on its own which message is worth answering, and speaks up on its own when nobody is talking to it.**

Most bots only talk when they are @-mentioned or hit by a prefix. A person in a real group chat behaves differently: they read along all day, chime in when someone asks about something they know, and sometimes just can't hold back and bring up a topic themselves. QvQStalker gives QvQChat that second kind of behavior. QvQChat broadcasts every group message as a `qvq.observe` lifecycle event (after command filtering, before trigger evaluation); the plugin keeps a small per-chat window of recent messages and tracks how hot each topic is. When a message matched neither @ nor a prefix, QvQChat asks this plugin's `qvq_decision` service whether to reply. On top of that, an urge model decides when the bot should open its mouth with no trigger at all.

The algorithm is ported from QvQChat 3.x's lurk mode (the `should_reply` five-layer check plus the proactive urge model), but it does not live inside QvQChat on purpose. Participation is opinionated behavior — some owners want it, others refuse it outright — so it ships as a separate plugin with zero imports of the QvQChat package, talking only through three framework interfaces: `sdk.lifecycle`, `sdk.module.call` and `sdk.storage`. The two modules install, update and uninstall independently, and each keeps working when the other one is missing or older than expected.

Safety is the default state, not an option. Participation stays off until you flip QvQChat's `participation_enabled` switch yourself. Every reply the plugin triggers still passes QvQChat's SpeechBudget — per-group hourly limit (8 by default), per-group cooldown (300 seconds) and a global daily cap on proactive speech — none of which the plugin can bypass. It then adds its own restraint on top: daily quotas, per-chat caps, sleep hours, and a cooldown for chats that never responded to its last line. Every decision returns a human-readable `reason`, so in the Dashboard you can always see why the bot spoke — or why it stayed quiet.

## Installation

```bash
epsdk install ErisPulse-QvQStalker
```

or

```bash
pip install ErisPulse-QvQStalker
```

Install it into the same ErisPulse instance as QvQChat. It needs ErisPulse >= 2.8.0, Python >= 3.10 and QvQChat >= 4.0.4 (the version that provides the `qvq.observe` event and the `speak` service). The two modules never import each other.

## Getting Started

1. Open QvQChat's settings page in the Dashboard and turn on the participation switch (`participation_enabled`, off by default). The group whitelist next to it can limit where participation applies; leave it empty for all groups.
2. QvQStalker's own settings appear in the "Plugins" section of that same page — mode, probabilities, thresholds, sleep hours. Saved values are pulled at load and hot-applied on save, so most changes need no restart (a few, like the window size, are read when the module loads).
3. That's all. From now on, group messages that didn't @ the bot or match a prefix go to QvQStalker's `qvq_decision` first; when it answers `speak`, QvQChat replies to the group.

## How It Works

### Observation

QvQChat broadcasts a `qvq.observe` lifecycle event for every group message after command filtering and before trigger evaluation. The payload is plain data (`chat_key`, `platform`, `sender_id`, `sender_name`, `text`, `is_group`, `at_bot`, `ts`, ...) and the plugin treats it as strictly read-only — it never marks the event handled and never throws. Each message lands in a per-chat ring buffer (the last 50 by default) that feeds four consumers: the heat counter, the silence check, the LLM judge prompt, and the proactive-speech context. If an older QvQChat doesn't have this event yet, the module still loads normally — it just receives nothing until you upgrade and reload it.

### Topic heat

Each chat carries a heat value between 0 and 2.0. Every message adds a base of 0.05, more when the conversation is moving fast: a message less than 5 seconds after the previous one adds 0.15, under 15 seconds adds 0.08, under 30 seconds adds 0.03. A question mark adds 0.1 and an exclamation mark 0.05. Before the increment, the existing heat decays in proportion to the quiet gap — at most half of it per full minute of silence — so a dead topic cools off on its own.

### The five-layer decision

When QvQChat calls `qvq_decision(ctx)`, the answer falls through five layers. L1 is a zero-cost regex: a message that looks like a question — Chinese question words (怎么 / 为什么 / 什么 / 是不是 / 吗 / 呢 ...) or a trailing question mark — joins with `question_prob` probability, 0.6 by default, +0.3 when the topic is hot, capped at 0.95. L2 and L3 don't decide anything themselves; they raise escalation flags. L2 flags when heat crosses `heat_flag_threshold`, gated by a probability of min(heat × 0.3, 0.7) so a merely warm topic doesn't escalate every time; L3 flags when the newest message breaks a silence longer than `silence_threshold_minutes`. With a flag up and `llm_judge` enabled, L5 takes over: the plugin hands QvQChat's model the last 10 overheard messages and asks one yes/no question — should the bot join this one? — with an 8-second timeout; a timed-out or unparsable answer falls back to L4. L4 is the probability floor everything lands on: after at least `min_messages_between_replies` observed messages since the last attempt, a dice roll of `default_probability` × mode multiplier (conservative ×0, balanced ×1, active ×2) plus a heat bonus decides. Conservative mode zeroes the dice, so it only ever speaks through the question layer or the judge.

Whatever the outcome, the return value has the same shape, with a reason meant for humans:

```python
{
    "decision": "speak",            # "speak" | "silent"
    "reason": "Question keywords matched (heat 1.24); joining with probability 0.90",
    "score": 0.9,                   # 0 ~ 1
    "urgency": "high",              # "low" | "normal" | "high"
}
```

(Reason strings ship in Chinese and English.)

### The urge model

Besides reacting, the plugin keeps an urge value per chat with a one-hour half-life — the quieter the chat, the more it fades. Each overheard message adds to it: 0.06 base, +0.12 for questions, +0.05 for exclamations, +0.04 for long messages (20+ characters), +0.15 when the bot was @-mentioned or replied to (the itch of not answering), plus a small heat bonus. A background loop wakes roughly every 30 minutes (×0.7-1.3 random jitter) and walks a chain of gates: sleep hours, the global daily cap, at least `min_silence_hours` since the last proactive line in that chat, urge ≥ `urge_threshold`, the per-chat daily cap, and the unreplied cooldown — if the bot spoke and the chat went quiet, it holds back for `unreplied_cooldown_hours`. When everything passes, it speaks exactly once that round, in the chat with the highest urge, calling QvQChat's `speak` service with the last 10 overheard messages as context. That call is still budgeted by QvQChat; if the service is missing or fails, the plugin logs a warning, halves the urge so it won't retry every round, and consumes no quota.

## Configuration

All settings live under the `QvQStalker` module config and are edited in the "Plugins" section of QvQChat's settings page (they also show up as a regular module form in the ErisPulse Dashboard).

| Field | Default | Description |
|---|---|---|
| `mode` | `balanced` | Participation mode: `conservative` (no random chiming in; only the question layer and the LLM judge can speak) / `balanced` / `active` (base probability doubled) |
| `default_probability` | `0.03` | Base join probability of the balanced-mode dice roll |
| `question_prob` | `0.6` | Reply probability for question-like messages (+0.3 when heat > 0.5, capped at 0.95) |
| `min_messages_between_replies` | `15` | Messages to observe between two join attempts |
| `silence_threshold_minutes` | `30` | A message breaking a silence longer than this raises the escalation flag |
| `heat_flag_threshold` | `0.8` | Heat above this (with a probability gate) raises the escalation flag |
| `llm_judge` | `false` | Let QvQChat's model make the final yes/no call when an escalation flag is up |
| `judge_model` | *(empty)* | Model for the judge; empty = QvQChat's default |
| `judge_timeout` | `8.0` | Seconds before the judge gives up (falls back to the probability layer) |
| `heat_cap` | `2.0` | Maximum topic heat |
| `heat_bonus_cap` | `0.15` | Maximum heat bonus added in the probability layer |
| `sleep_hour` | `2` | Bedtime hour for proactive speech (0-23; equal to `wake_hour` disables the schedule) |
| `wake_hour` | `8` | Wake-up hour for proactive speech |
| `urge_threshold` | `1.0` | Urge a chat must reach to qualify for proactive speech |
| `global_max_per_day` | `3` | Daily proactive-speech cap across all chats |
| `per_chat_daily` | `1` | Daily proactive-speech cap per chat |
| `unreplied_cooldown_hours` | `12` | Hours to stay quiet in a chat that saw no messages after the bot's last line |
| `min_silence_hours` | `6` | Minimum hours since the last proactive line in the same chat |
| `check_interval_minutes` | `30` | Urge-check interval in minutes (multiplied by a random 0.7-1.3 jitter) |
| `window_size` | `50` | Overheard messages kept per chat (ring buffer) |

## Services

| Service | Signature | Description |
|---|---|---|
| `qvq_decision` | `(ctx: dict) -> dict` | The participation decision QvQChat queries; returns `{decision, reason, score, urgency}` |
| `group_heat` | `(chat_key: str = '') -> dict` | Topic heat of one chat, or of all chats when `chat_key` is omitted |
| `urge_status` | `(chat_key: str = '') -> dict` | Current (decayed) urge value and threshold of one chat, or all chats |
| `request_speak` | `(chat_key: str, reason: str = '', prompt: str = '') -> dict` | Ask the bot to speak in a chat on demand; still passes QvQChat's budget |
| `qvq_settings` | `() -> dict` | Ownership marker that makes the plugin show up in QvQChat's settings page |

Through QvQChat's ecosystem bridge, these services are automatically exposed to the agent as AI tools (prefixed `eco_QvQStalker_`), so the bot itself, orchestration flows and scheduled jobs can check heat, check urge and request a line without any extra code.

## License

MIT
