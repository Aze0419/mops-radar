# mops-radar（~/mops_radar）

掃 MOPS 重大訊息公告，篩 EPS 條件，AI 分析後推 Telegram 與 Google Sheet。另外每日抓
TWSE/TPEX 收盤價寫回 Supabase `stock_prices`（`fetch_prices.py`，因子選股表也讀這張）。

## 陷阱

- **這個目錄（本機 git clone）現在才是 `fetch_prices.py` 實際執行的位置。**
  2026-09-07 之前排程跑的是 Google Drive 那份
  `~/Library/CloudStorage/GoogleDrive-shih.sa@gmail.com/我的雲端硬碟/01_WORK/MOPS_RADAR/`，
  結果那次 GDrive 掛載點持續不穩（不是偶發），cwd 落在該目錄讓 Python 啟動時直接
  fatal crash，繞開 cwd 之後 import 機制本身掃該目錄一樣被 EINTR 中斷，兩支收盤價
  排程（`fetch-prices.sh` / `fetch-prices-tse-only.sh`）當天整個斷線，
  上櫃股價停在好幾天前沒人發現。修法是把 `fetch_prices.py` 的執行位置改到這個本機
  clone，GDrive 那份現在只當原始碼保管／編輯用——**改完程式兩邊都要同步（先在
  GDrive 改，再 `cp` 或用 git pull 同步到這裡），否則明天跑的還是舊版。**
  `.env`（含 SUPABASE_URL/SUPABASE_SERVICE_KEY）也要跟 GDrive 那份保持一致，
  這裡的版本落後過，2026-09-07 已用 GDrive 那份覆蓋（舊版備份成 `.env.bak-*`）。
- **`mops_radar.py`（scan/send）走另一條路，已經有 runner 包一層。** 由
  `~/.hermes/scripts/mops_radar_runner.py` 執行，全程不 chdir 進 GDrive、也不讓
  GDrive 目錄進 sys.path（用絕對路徑 `open()` 讀原始碼後 `exec()`），外層再包
  timeout+重試防呆。`fetch_prices.py` 目前沒套用同一招，改用「搬到本機執行」
  取代；如果之後 GDrive 掛載又不穩導致這個本機 clone 落後太久造成困擾，可以考慮
  改用跟 `mops_radar_runner.py` 一樣的 exec+timeout 包法，讓 GDrive 重新變回單一
  執行位置，不用手動同步兩邊。
- **scan 與 send 是兩支獨立 cron，靠 `pending_results.json` 交接。** `mops_radar.py scan`（00:30）只分析存檔不送出；`mops_radar.py send`（07:10）才送 Telegram 並在送完刪 cache。CLI 沒帶參數預設是 `scan`——歷史上就發生過 send 腳本掉了參數，結果每天靜靜重跑 scan 從沒送出過。
- **cache 還在 = send 失敗，資料沒丟。** 沒收到訊號時先看 `~/mops-radar-send-run.log`，再看 `pending_results.json` 是否留著；留著就能補送，不用重跑 scan。
- **`~/mops_radar/` 底下出現 `pending_results.json` 是警訊**，代表腳本 cd 錯目錄（正常應該產生在 GDrive 那份）。
- **AI 產生的 `display_text` 不保證 HTML 標籤配對。** Telegram 用 `parse_mode: HTML` 時只要有一個 `<b>` 沒閉合，整則直接 400 拒收、當天全部訊號一起陣亡。組訊息前要檢查標籤配對，不合就降級成純文字。
- **股價與成交量一律查 Supabase `stock_prices`**，不要讓 AI 從公告內文自己編，也不要重新引入本機 json 快取。
- **python 一律寫死 `/usr/bin/python3`**（同 hermes-tw-stock-system 的 PATH 汙染問題）。
