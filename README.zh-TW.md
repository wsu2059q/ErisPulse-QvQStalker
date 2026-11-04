<div align="center">
[简体中文](README.md) | **繁體中文** | [English](README.en.md) | [日本語](README.ja.md) | [Русский](README.ru.md)

<img src="https://raw.githubusercontent.com/ErisPulse/ErisPulse/main/.github/assets/ErisPulseLogo.png" width="180" alt="QvQStalker" />

<img src="https://raw.githubusercontent.com/ErisPulse/.github/main/assets/badges/module.svg" alt="ErisPulse Module" />
</div>

# ErisPulse-QvQStalker

**[QvQChat](https://github.com/wsu2059q/ErisPulse-QvQChat) 夥伴插件：讓機器人在群裡像個真成員——平常潛水旁聽，自己判斷哪句話值得接，沒人理它時也會忍不住開口。**

多數機器人只有被 @ 或命中前綴才會說話，真人群友不是這樣：整天掛在群裡看訊息，偶爾有人問到的話題剛好會就插一句，有時候實在憋不住還會自己開話題。QvQStalker 補給 QvQChat 的就是後面這種行為。QvQChat 會在指令過濾之後、觸發判定之前，把每條群訊息以 `qvq.observe` 生命週期事件廣播出來；本插件據此為每個會話保留一小段最近訊息視窗、追蹤話題熱度。當一則訊息既沒 @ 機器人也沒命中前綴時，QvQChat 會來詢問本插件的 `qvq_decision` 服務要不要接話。另外還有一套衝動值模型，決定機器人什麼時候在完全沒人理它的情況下主動開口。

演算法移植自 QvQChat 3.x 的窺屏（潛水）模式（`should_reply` 五層判定加上 proactive 衝動值模型），但刻意沒塞進 QvQChat 本體。參與決策是種很有「個性」的行為——有人想要，有人堅持不要——所以做成獨立插件，零 QvQChat 套件依賴，所有互動只走 `sdk.lifecycle` / `sdk.module.call` / `sdk.storage` 三個框架介面。兩個模組可以各自安裝、升級、移除，任何一方缺席或版本偏舊，另一方都照常運作。

安全是預設狀態，不是選項。參與決策在你親手打開 QvQChat 的 `participation_enabled` 開關之前一直關閉。插件觸發的每次發言仍要過 QvQChat 的 SpeechBudget——每群每小時上限（預設 8 則）、同群冷卻（預設 300 秒）、主動發言全域每日上限——插件一條也繞不過去。在此之上插件還有自己的一層自律：每日配額、單會話上限、作息時段，以及對「開口後無人回應的群」的冷卻。每次決策都會回傳一句人類可讀的 `reason`，在 Dashboard 裡隨時能看清機器人為什麼說話、為什麼沉默。

## 安裝

```bash
epsdk install ErisPulse-QvQStalker
```

或

```bash
pip install ErisPulse-QvQStalker
```

裝到與 QvQChat 相同的 ErisPulse 實例即可。需要 ErisPulse >= 2.8.0、Python >= 3.10、QvQChat >= 4.0.4（提供 `qvq.observe` 事件與 `speak` 服務的版本）。兩個模組互相不 import。

## 啟用

1. 在 Dashboard 的 QvQChat 設定頁打開參與決策開關（`participation_enabled`，預設關閉）；旁邊的群白名單可限定生效範圍，留空即全部群組。
2. QvQStalker 自己的設定項出現在同一頁面的「插件」區塊——模式、機率、門檻、作息都在這裡調。儲存的值在載入時拉取、儲存時熱更新，多數改動無需重啟（個別項如視窗大小在模組載入時讀取）。
3. 到此為止。此後群裡沒 @ 機器人、也沒命中前綴的訊息，都會先交給 QvQStalker 的 `qvq_decision` 詢問；回答 `speak` 時 QvQChat 就接話。

## 運作原理

### 旁聽

QvQChat 在指令過濾後、觸發判定前，為每條群訊息廣播 `qvq.observe` 事件。承載的是純資料（`chat_key` / `platform` / `sender_id` / `sender_name` / `text` / `is_group` / `at_bot` / `ts` 等），插件唯讀，絕不把事件標記為已處理，也絕不拋錯。每則訊息進入按會話隔離的環形視窗（預設保留最近 50 條），供四個地方消費：熱度計算、沉寂判定、小模型判斷的 prompt、主動發言的摘要。若 QvQChat 版本較舊還沒有這個事件，模組照常載入，只是收不到訊息，升級後重載即恢復。

### 話題熱度

每個會話有一個 0 到 2.0 的熱度值。每則訊息先加基礎 0.05，聊得越快加得越多：與上一條間隔小於 5 秒 +0.15、小於 15 秒 +0.08、小於 30 秒 +0.03；問號 +0.1、驚嘆號 +0.05。累加之前，舊熱度會先按安靜時長衰減——每滿 1 分鐘最多衰減一半——涼掉的話題自然冷卻。

### 五層判定

QvQChat 詢問 `qvq_decision(ctx)` 時，答案沿五層逐層下沉。L1 是零成本正則：訊息看起來像提問——中文疑問詞（怎么 / 為什麼 / 什麼 / 是不是 / 嗎 / 呢 等）或以問號結尾——就以 `question_prob` 機率參與，預設 0.6，話題熱時 +0.3，封頂 0.95。L2 與 L3 自己不做決定，只豎「升級旗」：L2 在熱度超過 `heat_flag_threshold` 時按 min(熱度×0.3, 0.7) 的機率豎旗（免得不溫不火的話題回回都升級），L3 在最新一則訊息打破的沉寂超過 `silence_threshold_minutes` 分鐘時豎旗。旗豎著且 `llm_judge` 開啟時輪到 L5：帶上最近 10 條旁聽，讓 QvQChat 的模型回答一道二選一——「這句話該不該接？」——8 秒逾時，逾時或解析不了就回落 L4。L4 是所有路徑最終落地的機率層：距上次嘗試至少觀察滿 `min_messages_between_replies` 則訊息後擲骰，`default_probability` × 模式倍率（conservative ×0、balanced ×1、active ×2）加上熱度加成決定。conservative 模式把骰子歸零，只可能從提問層或小模型判定開口。

無論結果如何，回傳值形狀一致，reason 都是寫給人看的：

```python
{
    "decision": "speak",            # "speak" | "silent"
    "reason": "提問訊息命中關鍵詞（熱度 1.24），以 0.90 機率參與",
    "score": 0.9,                   # 0 ~ 1
    "urgency": "high",              # "low" | "normal" | "high"
}
```

（reason 內建簡體中文與英文兩套文案。）

### 衝動值

除了被動接話，插件為每個會話維護一個衝動值，半衰期 1 小時——群越冷清，衝動越淡。每條旁聽訊息都會累積：基礎 0.06，提問 +0.12，驚嘆號 +0.05，長訊息（≥20 字）+0.04，被 @ 或被回覆 +0.15（想回又沒回上的那股癢），另加少量熱度加成。背景迴圈大約每 30 分鐘（×0.7-1.3 隨機抖動）醒來過一遍門檻：作息時段、全域每日上限、距該會話上次開口 ≥ `min_silence_hours`、衝動值 ≥ `urge_threshold`、單會話每日上限、未回應冷卻——上次開口後群裡再無動靜，就再憋 `unreplied_cooldown_hours` 小時。全部通過時，本輪只開一次口，挑衝動值最高的會話，以最近 10 條旁聽為上下文呼叫 QvQChat 的 `speak` 服務。這次呼叫仍要過 QvQChat 的預算；服務缺失或失敗時記一條 warning，衝動值減半避免每輪重試，也不消耗配額。

## 設定

全部設定在 `QvQStalker` 模組設定下，在 QvQChat 設定頁「插件」區塊直接編輯（同時也是 ErisPulse Dashboard 裡的普通模組表單）。

| 欄位 | 預設 | 說明 |
|---|---|---|
| `mode` | `balanced` | 參與模式：`conservative` 保守（不隨機插話，僅提問層與小模型判定會開口）/ `balanced` 平衡 / `active` 積極（基礎機率加倍） |
| `default_probability` | `0.03` | balanced 檔位的基礎插話機率 |
| `question_prob` | `0.6` | 提問訊息的回覆機率（熱度 > 0.5 時 +0.3，上限 0.95） |
| `min_messages_between_replies` | `15` | 兩次插話嘗試之間至少觀察的訊息數（防洗版） |
| `silence_threshold_minutes` | `30` | 訊息打破的沉寂超過該分鐘數時豎升級旗 |
| `heat_flag_threshold` | `0.8` | 熱度超過該值（另有機率閘）時豎升級旗 |
| `llm_judge` | `false` | 升級旗豎起時交給 QvQChat 的模型做最終判斷 |
| `judge_model` | 空 | 判斷所用模型，留空跟隨 QvQChat 預設模型 |
| `judge_timeout` | `8.0` | 判斷逾時秒數，逾時回落機率層 |
| `heat_cap` | `2.0` | 話題熱度上限 |
| `heat_bonus_cap` | `0.15` | 機率層熱度加成上限 |
| `sleep_hour` | `2` | 主動發言就寢小時（0-23；與 `wake_hour` 相等表示不限制作息） |
| `wake_hour` | `8` | 主動發言起床小時 |
| `urge_threshold` | `1.0` | 會話衝動值達到該值才有資格主動開口 |
| `global_max_per_day` | `3` | 全域（所有會話合計）每日主動發言上限 |
| `per_chat_daily` | `1` | 單會話每日主動發言上限 |
| `unreplied_cooldown_hours` | `12` | 開口後無人回應的群，安靜多久（小時）內不再開口 |
| `min_silence_hours` | `6` | 同一會話距上次主動開口的最小間隔（小時） |
| `check_interval_minutes` | `30` | 衝動值檢查間隔（分鐘，×0.7-1.3 抖動） |
| `window_size` | `50` | 每會話旁聽視窗保留筆數（環形緩衝） |

## 服務

| 服務 | 簽名 | 說明 |
|---|---|---|
| `qvq_decision` | `(ctx: dict) -> dict` | QvQChat 詢問的參與決策；回傳 `{decision, reason, score, urgency}` |
| `group_heat` | `(chat_key: str = '') -> dict` | 查詢單一會話的話題熱度，省略時回傳全部會話 |
| `urge_status` | `(chat_key: str = '') -> dict` | 查詢衝動值現值（含衰減）與門檻，省略時回傳全部會話 |
| `request_speak` | `(chat_key: str, reason: str = '', prompt: str = '') -> dict` | 請求向指定會話主動發言，仍過 QvQChat 預算 |
| `qvq_settings` | `() -> dict` | 歸屬標記：讓本插件出現在 QvQChat 設定頁「插件」區塊 |

這些服務經 QvQChat 生態橋自動轉為 AI 工具（前綴 `eco_QvQStalker_`），智慧代理本身、編排流程與排程任務都能直接查熱度、查衝動值、申請發言，不用多寫一行程式。

## 授權

MIT
