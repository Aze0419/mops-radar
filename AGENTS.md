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
- **scan 與 send 是兩支獨立 cron，靠 `pending_results.json` 交接。** `mops_radar.py scan`（00:30）只分析存檔不送出；`mops_radar.py send`（07:00）才送 Telegram 並在送完刪 cache。CLI 沒帶參數預設是 `scan`——歷史上就發生過 send 腳本掉了參數，結果每天靜靜重跑 scan 從沒送出過。
- **cache 還在 = send 失敗，資料沒丟；重跑 send 只補沒做完的步驟。** 每筆 item 各自記 `gsheet_done`／`history_done`／`tg_sent`，每做完一步就回寫 cache。以前 send 先寫 Sheet 再送 Telegram，Telegram 一失敗重跑就把「公告紀錄」多插一列、公告次數多算一次（`sync_gsheet` 的 insert_row 沒去重）。Sheet 同步失敗時 send 會 exit 1 並只留那幾筆在 cache。**隔天 00:30 的 scan 也不會蓋掉沒做完的 item**，會標 `carried` 併進新 cache，Telegram 標題註明「含前次沒送出的 N 筆」、已送過的只補 Sheet。沒收到訊號時先看 `~/mops-radar-send-run.log`，再看 `pending_results.json` 是否留著；留著就直接重跑 send，不用重跑 scan。
- **`pending_results.json` 產生在這個目錄**（已 gitignore）。GDrive 那份目錄底下若又出現新的 cache，代表有腳本還指著舊路徑。
- **AI 產生的 `display_text` 不保證 HTML 標籤配對。** Telegram 用 `parse_mode: HTML` 時只要有一個 `<b>` 沒閉合，整則直接 400 拒收、當天全部訊號一起陣亡。組訊息前要檢查標籤配對，不合就降級成純文字。
- **休市日由 `fetch_prices.py` 自己擋。** 開跑先查證交所休市日曆（`market_holiday_name()`），休市就印「休市，不抓價」、exit 0，`fetch-prices-persistent-retry.sh` 看到這幾個字會寫當天標記檔、不再重跑。cron 仍是週一到五固定觸發，不用改排程。日曆查不到時照常抓價；**颱風假不在日曆上，擋不到**，那天照樣會有 23:40 告警。
- **清單頁的說明就是全文，不要再加回詳細頁。** `ajax_t05st02` 每個 form 的隱藏欄位 `h?8` 跟「詳細資料」頁一字不差（2026-09-29 全天 243 則逐一比對）。舊版另外用 `t05sr01_1` 拿 SEQ_NO 抓詳細頁，但那頁不吃日期參數，scan 改到 00:30 後每天 0 筆、白印兩百多行「無詳細頁參數」，已整段移除。懷疑漏抓 EPS 時先查 `h?8` 有沒有被截斷。
- **Jev 只負責「刪」，而且失敗一律放行。** scan 的關鍵字篩完後，對每筆問 TypeSafe Jev「是不是在公布自家獲利」，機率 < `JEV_THRESHOLD`（預設 0.2）才剔除，log 會印 `✂ 機率 代號 主旨`。沒 `TYPESAFE_API_KEY`、401、逾時都照樣放行，不能因為 Jev 掛掉漏訊號。門檻 0.2 是 2026-09-29 用 10 個交易日實測定的：面額變更／更正歷年財報 ≤ 0.06、真正的財務業務公告 ≥ 0.42。沒收到某檔訊號時，先 grep log 裡的 `✂` 看是不是被 Jev 剔掉。
- **EPS／營收由 Jev 從公告數字裡「挑」，不是 regex 抓第一個數字。** 舊的 `regex_financials()`（找第一個「每股盈餘」往後取數字）只有「注意交易資訊」固定表格抓得對，自結損益的自由文字會出事：遠東銀主旨含「每股盈餘」抓到淨利 462717、華南金／彰銀／豐泰把 1~8 月累計 EPS 當單月 ×12、營收單位仟元被當百萬——本益比一錯，AI 照「系統預算值」評級就會亂給 🔴。現在 `jev_judge()` 跟獲利判斷同一個 request 問 6 題 Choice：程式先把公告每個數字列成候選（附所在行＋依顯示寬度對齊找到的同欄表頭），Jev 只能原樣挑一個或選「無」。三道把關：信心 < `JEV_MIN_CONF`（0.8）當沒資料；單月／單季 EPS 挑到的欄位表頭含「累計／四季」由程式硬擋（高雄銀「本月份」欄空白那種表，Jev 會高信心挑累計欄；另問一題「有沒有單月EPS」實測反而誤殺浩宇、漢達，已放棄）；Jev 失敗才退回 regex。2026-09-30 用 5 天 31 則人工核對：Jev 0 錯、regex 15 錯。log 的 `抽取（jev|regex）` 那行會印挑到的數字與各題信心，懷疑 EPS 錯先看這行。
- **股價與成交量一律查 Supabase `stock_prices`**，不要讓 AI 從公告內文自己編，也不要重新引入本機 json 快取。
- **python 一律寫死 hermes-agent venv：`/Users/iroman/.hermes/hermes-agent/venv/bin/python3`**（系統 `/usr/bin/python3` 缺 gspread/supabase）。**不要**再加 `export PYTHONPATH="$HOME/Library/Python/3.9/lib/python/site-packages..."` 借舊套件：Hermes 背景自動更新會重建這顆 venv（2026-09-06 從 3.9 換成 3.11），借來的 cp39 `pydantic_core` 會讓 Supabase 讀取悄悄失敗、股價全變 0。
