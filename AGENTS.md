# mops-radar（~/mops_radar）

掃 MOPS 重大訊息公告，篩 EPS 條件，AI 分析後推 Telegram 與 Google Sheet。另外每日抓
TWSE/TPEX 收盤價寫回 Supabase `stock_prices`（`fetch_prices.py`，因子選股表也讀這張）。

![mops-radar 架構圖](docs/architecture.png)

（原始檔／可編輯版本：[docs/architecture.html](docs/architecture.html)）

## 陷阱

- **這個目錄（本機 git clone）是 `fetch_prices.py` 與 `mops_radar.py` 唯一的執行位置，GDrive 那份已退役。**
  早期兩支都跑在 Google Drive 那份
  `~/Library/CloudStorage/GoogleDrive-shih.sa@gmail.com/我的雲端硬碟/01_WORK/MOPS_RADAR/`，
  但 GDrive 掛載點反覆出事：cwd 落在該目錄讓 import 被 EINTR 中斷、FileProvider
  整個 degraded 時連 `open()` 都卡住不回應。2026-09-07 先把 `fetch_prices.py` 搬來這裡；
  2026-09-28 Hermes 背景自動更新後 gateway 改由 `osascript` 拉起，macOS 跳出
  「osascript 想要取用由 Google Drive 管理的檔案」權限框，沒人按之前 cron 行程卡在
  `open()`，scan/send 各逾時 4 次——同日把 `mops_radar.py` 也搬來這裡，
  `mops_radar_runner.py`（exec+timeout 包法）一併移除。
  **部署 = 這裡 commit+push（或 git pull），不用再同步 GDrive**；GDrive 那份只是舊檔，
  不要再改它，也不要再把任何排程指回去。`.env` 放這裡（不進 git）。
- **scan 與 send 是兩支獨立 cron，靠 `pending_results.json` 交接。** `mops_radar.py scan`（00:30）只分析存檔不送出；`mops_radar.py send`（07:10）才送 Telegram 並在送完刪 cache。CLI 沒帶參數預設是 `scan`——歷史上就發生過 send 腳本掉了參數，結果每天靜靜重跑 scan 從沒送出過。
- **cache 還在 = send 失敗，資料沒丟。** 沒收到訊號時先看 `~/mops-radar-send-run.log`，再看 `pending_results.json` 是否留著；留著就能補送，不用重跑 scan。
- **`pending_results.json` 產生在這個目錄**（已 gitignore）。GDrive 那份目錄底下若又出現新的 cache，代表有腳本還指著舊路徑。
- **AI 產生的 `display_text` 不保證 HTML 標籤配對。** Telegram 用 `parse_mode: HTML` 時只要有一個 `<b>` 沒閉合，整則直接 400 拒收、當天全部訊號一起陣亡。組訊息前要檢查標籤配對，不合就降級成純文字。
- **股價與成交量一律查 Supabase `stock_prices`**，不要讓 AI 從公告內文自己編，也不要重新引入本機 json 快取。
- **python 一律寫死 `/usr/bin/python3`**（同 hermes-tw-stock-system 的 PATH 汙染問題）。
