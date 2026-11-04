<div align="center">

简体中文 | [繁體中文](README.zh-TW.md) | [English](README.en.md) | [日本語](README.ja.md) | [Русский](README.ru.md)

<img src="https://raw.githubusercontent.com/ErisPulse/ErisPulse/main/.github/assets/ErisPulseLogo.png" width="180" alt="QvQStalker" />

<img src="https://raw.githubusercontent.com/ErisPulse/.github/main/assets/badges/module.svg" alt="ErisPulse Module" />

</div>

# ErisPulse-QvQStalker

**[QvQChat](https://github.com/wsu2059q/ErisPulse-QvQChat) 伴侣插件：让机器人在群里像个真成员——平时旁听，自己判断哪句话值得接，没人搭理它时也会忍不住开口。**

多数机器人只有被 @ 或命中前缀才会说话，真人群友不是这样：整天挂在群里看消息，偶尔有人问到懂的话题就插一句，有时候实在憋不住还会自己起话头。QvQStalker 给 QvQChat 补上的就是后面这种行为。QvQChat 会在命令过滤之后、触发判定之前，把每条群消息以 `qvq.observe` 生命周期事件广播出来；本插件据此为每个会话保留一小段最近消息窗口、跟踪话题热度。当一条消息既没 @ 机器人也没命中前缀时，QvQChat 会来问询本插件的 `qvq_decision` 服务要不要接话。另外还有一套冲动值模型，决定机器人什么时候在完全没人理它的情况下主动开口。

算法移植自 QvQChat 3.x 的窥屏模式（`should_reply` 五层判定加 proactive 冲动值模型），但刻意没有塞进 QvQChat 本体。参与决策是种很有"性格"的行为——有人想要，有人坚决不要——所以做成独立插件，零 QvQChat 包依赖，所有交互只走 `sdk.lifecycle` / `sdk.module.call` / `sdk.storage` 三个框架接口。两个模块可以各自安装、升级、卸载，任何一方缺席或版本偏旧，另一方都照常工作。

安全是默认状态，不是可选项。参与决策在你亲手打开 QvQChat 的 `participation_enabled` 开关之前一直关闭。插件触发的每次发言仍要过 QvQChat 的 SpeechBudget——每群每小时上限（默认 8 条）、同群冷却（默认 300 秒）、主动发言全局每日上限——插件一条也绕不过去。在此之上插件还有自己的一层自律：每日配额、单会话上限、作息时段，以及对"开口后无人回应的群"的冷却。每次决策都会返回一句人类可读的 `reason`，在 Dashboard 里随时能看清机器人为什么说话、为什么沉默。

<p>

  <a href="https://www.python.org/"><img src="https://img.shields.io/badge/Python-3.10+-FFD43B?style=for-the-badge&logo=python&logoColor=blue" alt="Python"></a>

  <a href="https://github.com/ErisPulse/ErisPulse"><img src="https://img.shields.io/badge/ErisPulse-2.8.0+-00A3FF?style=for-the-badge" alt="ErisPulse"></a>

  <a href="./LICENSE"><img src="https://img.shields.io/badge/License-MIT-blue?style=for-the-badge" alt="License"></a>

</p>

## 安装

```bash
epsdk install ErisPulse-QvQStalker
```

或

```bash
pip install ErisPulse-QvQStalker
```

装到与 QvQChat 相同的 ErisPulse 实例即可。要求 ErisPulse >= 2.8.0、Python >= 3.10、QvQChat >= 4.0.4（提供 `qvq.observe` 事件与 `speak` 服务的版本）。两个模块互相不 import。

## 启用

1. 在 Dashboard 的 QvQChat 设置页打开参与决策开关（`participation_enabled`，默认关闭）；旁边的群白名单可限定生效范围，留空即全部群。
2. QvQStalker 自己的设置项出现在同一页面的「插件」分区——模式、概率、阈值、作息都在这里调。保存的值在加载时拉取、保存时热更新，多数改动无需重启（个别项如窗口大小在模块加载时读取）。
3. 到此为止。此后群里没 @ 机器人、也没命中前缀的消息，都会先交给 QvQStalker 的 `qvq_decision` 问询；回答 `speak` 时 QvQChat 就接话。

## 工作原理

### 旁听

QvQChat 在命令过滤后、触发判定前，为每条群消息广播 `qvq.observe` 事件。载荷是纯数据（`chat_key` / `platform` / `sender_id` / `sender_name` / `text` / `is_group` / `at_bot` / `ts` 等），插件只读，绝不把事件标记为已处理，也绝不抛错。每条消息进入按会话隔离的环形窗口（默认保留最近 50 条），供四处消费：热度计算、沉寂判定、小模型判断的 prompt、主动发言的摘要。如果 QvQChat 版本较老还没有这个事件，模块照常加载，只是收不到消息，升级后重载即恢复。

### 话题热度

每个会话有一个 0 到 2.0 的热度值。每条消息先加基础 0.05，聊得越快加得越多：与上一条间隔小于 5 秒 +0.15，小于 15 秒 +0.08，小于 30 秒 +0.03；问号 +0.1，感叹号 +0.05。累加之前，旧热度会先按安静时长衰减——每满 1 分钟最多衰减一半——凉掉的话题自然冷却。

### 五层判定

QvQChat 问询 `qvq_decision(ctx)` 时，答案沿五层逐层下沉。L1 是零成本正则：消息看着像提问——中文疑问词（怎么 / 为什么 / 什么 / 是不是 / 吗 / 呢 等）或以问号结尾——就以 `question_prob` 概率参与，默认 0.6，话题热时 +0.3，封顶 0.95。L2 和 L3 自己不做决定，只竖"升级旗"：L2 在热度超过 `heat_flag_threshold` 时按 min(热度×0.3, 0.7) 的概率竖旗（免得不温不火的话题回回都升级），L3 在最新一条消息打破的沉寂超过 `silence_threshold_minutes` 分钟时竖旗。旗竖着且 `llm_judge` 开启时轮到 L5：带上最近 10 条旁听，让 QvQChat 的模型回答一道二选一——"这句话该不该接？"——8 秒超时，超时或解析不了就回落 L4。L4 是所有路径最终落地的概率层：距上次尝试至少观察满 `min_messages_between_replies` 条消息后掷骰，`default_probability` × 模式倍率（conservative ×0、balanced ×1、active ×2）加热度加成决定。conservative 模式把骰子归零，只可能从提问层或小模型判定开口。

无论结果如何，返回值形状一致，reason 都是写给人看的：

```python
{
    "decision": "speak",            # "speak" | "silent"
    "reason": "提问消息命中关键词（热度 1.24），以 0.90 概率参与",
    "score": 0.9,                   # 0 ~ 1
    "urgency": "high",              # "low" | "normal" | "high"
}
```

（reason 内置中文与英文两套文案。）

### 冲动值

除了被动接话，插件为每个会话维护一个冲动值，半衰期 1 小时——群越冷清，冲动越淡。每条旁听消息都会累积：基础 0.06，提问 +0.12，感叹号 +0.05，长消息（≥20 字）+0.04，被 @ 或被回复 +0.15（想回又没回上的那股劲），另加少量热度加成。后台循环大约每 30 分钟（×0.7-1.3 随机抖动）醒来过一遍门槛：作息时段、全局每日上限、距该会话上次开口 ≥ `min_silence_hours`、冲动值 ≥ `urge_threshold`、单会话每日上限、未回复冷却——上次开口后群里再没动静，就再憋 `unreplied_cooldown_hours` 小时。全部通过时，本轮只开一次口，挑冲动值最高的会话，以最近 10 条旁听为上下文调用 QvQChat 的 `speak` 服务。这次调用仍要过 QvQChat 的预算；服务缺失或失败时记一条 warning，冲动值减半避免每轮重试，也不消耗配额。

## 配置

全部配置在 `QvQStalker` 模块配置下，在 QvQChat 设置页「插件」分区直接编辑（同时也是 ErisPulse Dashboard 里的普通模块表单）。

| 字段 | 默认 | 说明 |
|---|---|---|
| `mode` | `balanced` | 参与模式：`conservative` 保守（不随机插话，仅提问层与小模型判定会开口）/ `balanced` 平衡 / `active` 积极（基础概率翻倍） |
| `default_probability` | `0.03` | balanced 档位的基础插话概率 |
| `question_prob` | `0.6` | 提问消息的回复概率（热度 > 0.5 时 +0.3，封顶 0.95） |
| `min_messages_between_replies` | `15` | 两次插话尝试之间至少观察的消息数（防刷屏） |
| `silence_threshold_minutes` | `30` | 消息打破的沉寂超过该分钟数时竖升级旗 |
| `heat_flag_threshold` | `0.8` | 热度超过该值（另有概率门）时竖升级旗 |
| `llm_judge` | `false` | 升级旗竖起时交给 QvQChat 的模型做最终判断 |
| `judge_model` | 空 | 判断所用模型，留空跟随 QvQChat 默认模型 |
| `judge_timeout` | `8.0` | 判断超时秒数，超时回落概率层 |
| `heat_cap` | `2.0` | 话题热度上限 |
| `heat_bonus_cap` | `0.15` | 概率层热度加成上限 |
| `sleep_hour` | `2` | 主动发言入睡小时（0-23；与 `wake_hour` 相等表示不限制作息） |
| `wake_hour` | `8` | 主动发言起床小时 |
| `urge_threshold` | `1.0` | 会话冲动值达到该值才有资格主动开口 |
| `global_max_per_day` | `3` | 全局（所有会话合计）每日主动发言上限 |
| `per_chat_daily` | `1` | 单会话每日主动发言上限 |
| `unreplied_cooldown_hours` | `12` | 开口后无人回应的群，安静多久（小时）内不再开口 |
| `min_silence_hours` | `6` | 同一会话距上次主动开口的最小间隔（小时） |
| `check_interval_minutes` | `30` | 冲动值检查间隔（分钟，×0.7-1.3 抖动） |
| `window_size` | `50` | 每会话旁听窗口保留条数（环形缓冲） |

## 服务

| 服务 | 签名 | 说明 |
|---|---|---|
| `qvq_decision` | `(ctx: dict) -> dict` | QvQChat 问询的参与决策；返回 `{decision, reason, score, urgency}` |
| `group_heat` | `(chat_key: str = '') -> dict` | 查询单个会话的话题热度，省略时返回全部会话 |
| `urge_status` | `(chat_key: str = '') -> dict` | 查询冲动值现值（含衰减）与阈值，省略时返回全部会话 |
| `request_speak` | `(chat_key: str, reason: str = '', prompt: str = '') -> dict` | 请求向指定会话主动发言，仍过 QvQChat 预算 |
| `qvq_settings` | `() -> dict` | 归属标记：让本插件出现在 QvQChat 设置页「插件」分区 |

这些服务经 QvQChat 生态桥自动转为 AI 工具（前缀 `eco_QvQStalker_`），智能体本身、编排流程和定时任务都能直接查热度、查冲动值、申请发言，不用写一行代码。

## License

MIT
