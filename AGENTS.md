# mops-radar（~/mops_radar）

掃 MOPS 重大訊息公告，篩 EPS 條件，AI 分析後推 Telegram 與 Google Sheet。

## 陷阱

- **這個目錄不是 cron 實際執行的位置。** 排程跑的是 Google Drive 那份 `~/Library/CloudStorage/GoogleDrive-shih.sa@gmail.com/我的雲端硬碟/01_WORK/MOPS_RADAR/`。改完程式兩邊都要同步，否則明天跑的還是舊版。
- **scan 與 send 是兩支獨立 cron，靠 `pending_results.json` 交接。** `mops_radar.py scan`（00:30）只分析存檔不送出；`mops_radar.py send`（07:10）才送 Telegram 並在送完刪 cache。CLI 沒帶參數預設是 `scan`——歷史上就發生過 send 腳本掉了參數，結果每天靜靜重跑 scan 從沒送出過。
- **cache 還在 = send 失敗，資料沒丟。** 沒收到訊號時先看 `~/mops-radar-send-run.log`，再看 `pending_results.json` 是否留著；留著就能補送，不用重跑 scan。
- **`~/mops_radar/` 底下出現 `pending_results.json` 是警訊**，代表腳本 cd 錯目錄（正常應該產生在 GDrive 那份）。
- **AI 產生的 `display_text` 不保證 HTML 標籤配對。** Telegram 用 `parse_mode: HTML` 時只要有一個 `<b>` 沒閉合，整則直接 400 拒收、當天全部訊號一起陣亡。組訊息前要檢查標籤配對，不合就降級成純文字。
- **股價與成交量一律查 Supabase `stock_prices`**，不要讓 AI 從公告內文自己編，也不要重新引入本機 json 快取。
- **python 一律寫死 `/usr/bin/python3`**（同 hermes-tw-stock-system 的 PATH 汙染問題）。
