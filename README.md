# mops-radar（飆股雷達）

每天凌晨掃公開資訊觀測站（MOPS）的重大訊息公告，挑出「公司公布自家獲利」的公告，配合收盤價算預估本益比、交給 AI 評級，早上推播到 Telegram，並把值得追蹤的個股寫進 Google Sheet。另外每個交易日抓 TWSE／TPEx 收盤價寫回 Supabase，供本系統與因子選股共用。

![mops-radar 架構圖](docs/architecture.png)

> 改程式前先讀 [AGENTS.md](AGENTS.md)：部署位置、scan／send 交接、Jev 抽取、評級規則等踩過的坑都記在那裡。

## 每天發生什麼事

所有排程都在 Hermes（Mac mini）上跑，程式碼在那台機器的 `~/mops_radar`（本 repo 的 git clone）。

| 時間（週一～五） | 腳本 | 做什麼 |
|---|---|---|
| 14:15 | `fetch_prices.py --tse-only` | 抓上市收盤價寫進 Supabase `stock_prices` |
| 15:45、18:00 | `fetch_prices.py` | 上市＋上櫃再抓一次，主要是補上櫃（TPEx 發布較晚） |
| 16:00～23:40 每 20 分 | `fetch-prices-persistent-retry.sh` | 上櫃還沒抓到就持續重試，23:40 仍失敗才告警 |
| 00:30 | `mops_radar.py scan` | 抓前一天的公告（週一抓週五～週日）→ 篩選 → 算本益比 → AI 評級，結果存 `pending_results.json`，**不送出** |
| 07:00 | `mops_radar.py send` | 讀 `pending_results.json` → 寫 Google Sheet → 推 Telegram，全部做完才刪檔 |

### scan 的篩選與分析

1. **關鍵字篩選**：說明含「每股盈餘」、符合條款為第 51 或 53 款，排除 `EXCLUDE_CODES`。
2. **TypeSafe Jev**：同一個 request 判斷「是不是在公布自家獲利」（剔除面額變更這類只是順帶提到 EPS 的公告），並從公告裡的數字**挑出**單月／單季／今年累計 EPS 與單月營收。程式先列出候選數字，Jev 只能原樣挑一個或選「無」，不會自己編數字。
3. **預估本益比**：預估全年 EPS 依序用單月 × 12 → 單季 × 4 → 今年 1～N 月累計 × 12/N；股價查 Supabase，比最新交易日舊會加 ⚠️ 警告。
4. **AI 評級**（OpenRouter，Gemini）：依 prompt 裡的評級標準給 🔴 強烈買進／🟠 建議買進／🟡 一般觀望／🟢 需要小心。AI 不上網，只能用公告與提供的資料；缺年增率時程式會照評級標準把過高的評級降級並註明。
5. **寫入**：🔴、🟠 寫進 Google Sheet「公告紀錄」，🔴 另外記一筆到「歷史紀錄」。

### 出錯時會怎樣

- scan 失敗或沒跑：07:00 的 send 會在 Telegram 發「⚠️ 掃描失敗」或「找不到今天的掃描結果」。
- send 中途失敗：`pending_results.json` 會留著，每筆記著做到哪一步，直接重跑 `send` 只補沒做完的，不會重複寫 Sheet 或重送訊息；隔天的 scan 也會把沒送出的帶著走。
- Jev 失敗：一律放行並退回規則式抽取，不會因此漏訊號。
- OpenRouter 暫時性錯誤會重試，主模型下架會自動改用備援模型（Telegram 標題會註明）。

## 設定

複製 `.env.example` 成 `.env`（放在 repo 目錄，不進 git），程式啟動時自己載入：

| 變數 | 必填 | 用途 |
|---|---|---|
| `OPENROUTER_KEY` | ✅ | AI 評級 |
| `TELEGRAM_TOKEN`、`TELEGRAM_CHAT_ID` | ✅ | 推播 |
| `SUPABASE_URL`、`SUPABASE_SERVICE_KEY` | ✅ | 讀寫 `stock_prices` |
| `TYPESAFE_API_KEY` | 建議 | Jev 篩選與 EPS 抽取；沒設就只用關鍵字篩選與規則式抽取 |
| `SA_KEY_FILE` | | Google service account 金鑰路徑，預設讀 repo 內的 `google-sa.json`（已 gitignore） |
| `RADAR_SHEET_ID` | | Google Sheet ID，預設為現用的那份 |
| `AI_MODELS` | | 模型優先順序（逗號分隔），預設 `gemini-3.1-flash-lite-preview` → `2.5-flash-lite` → `2.5-flash` |
| `JEV_THRESHOLD`、`JEV_MIN_CONF` | | Jev 獲利判斷門檻（0.2）、數字挑選門檻（0.8），改之前先跑 live 驗證 |
| `FACTOR_SYNC_DIR` | | `fetch_prices.py` 寫完股價後順便跑因子選股 `sheet_sync.py` 的位置 |

Python 3.9 以上，需要 `supabase`、`gspread`、`pandas`（含 `openpyxl`）。Hermes 上一律用 `/Users/iroman/.hermes/hermes-agent/venv/bin/python3`，原因見 AGENTS.md。

## 手動執行

```bash
python mops_radar.py scan     # 分析並存 pending_results.json，不送出
python mops_radar.py send     # 送出 pending_results.json（沒帶參數預設是 scan）
python fetch_prices.py        # 抓上市＋上櫃收盤價
python fetch_prices.py --tse-only
python fetch_prices.py --backfill-otc 2026-08-11 2026-08-17   # 補上櫃歷史缺口
```

## 部署

1. 本機改完、跑過測試，commit 並 push 到 `main`。
2. 到 Hermes：`cd ~/mops_radar && git pull`。兩支 Python 腳本直接在這裡執行，pull 完就生效。
3. 改了 `hermes/scripts/*.sh` 或 `hermes/skills/` 的話，另外複製到 Hermes 的 `~/.hermes/scripts/`、`~/.hermes/skills/`（cron 實際呼叫的是那裡）。

Google Drive 上的舊 `MOPS_RADAR/` 已退役，不要再改它或把排程指回去。

## 測試

```bash
pip install -r requirements-dev.txt
python -m pytest
```

測試全部離線，會擋掉所有對外連線（Hermes 上的 `.env` 是真金鑰，寧可測試失敗也不能真的送出訊息）。`tests/fixtures/` 有 22 則人工核對過的真實公告，改 Jev 題目或門檻時在 Hermes 上跑一次真打 Jev 的驗證：

```bash
cd ~/mops_radar && /Users/iroman/.hermes/hermes-agent/venv/bin/python3 tests/jev_live_check.py
```

## 目錄

```
mops_radar.py        scan／send 主程式
fetch_prices.py      每日收盤價 → Supabase stock_prices
hermes/scripts/      Hermes cron 呼叫的包裝腳本（部署時複製到 ~/.hermes/scripts/）
hermes/skills/       Hermes skill 說明
tests/               pytest 與 Jev live 驗證
docs/                架構圖（architecture.html 是原始檔，png 由它輸出）
AGENTS.md            陷阱與設計決策（改程式前必讀）
```
