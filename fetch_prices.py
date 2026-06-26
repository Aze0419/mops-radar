#!/usr/bin/env python3
"""抓最新收盤價：每日 14:15 執行，抓 TWSE+TPEX 存成 prices.json 並回寫 Google Sheet"""
import json, os, re, urllib.request
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

_env = Path(__file__).parent / ".env"
if _env.exists():
    for _line in _env.read_text().splitlines():
        if _line.strip() and not _line.startswith("#") and "=" in _line:
            _k, _v = _line.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip())

TZ         = ZoneInfo("Asia/Taipei")
CACHE_FILE = Path(__file__).parent / "prices.json"
SA_KEY_FILE = os.environ.get("SA_KEY_FILE", "/Users/iroman/ai-hedge-fund-tw/google-sa.json")
GSHEET_ID   = os.environ.get("GSHEET_ID", "")

def smart_date():
    """13:30 前用昨天，週末往前推到週五"""
    d = datetime.now(TZ)
    if d.hour < 13 or (d.hour == 13 and d.minute < 30):
        d -= timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d

def fetch_tse(d):
    """上市：TWSE MI_INDEX CSV"""
    url = (f"https://www.twse.com.tw/exchangeReport/MI_INDEX"
           f"?date={d.strftime('%Y%m%d')}&type=ALLBUT0999&response=csv")
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        text = r.read().decode("utf-8", errors="replace")

    prices = {}
    for line in text.split("\n"):
        line = line.strip()
        if not re.match(r'^"\d{4}",', line):
            continue
        row = re.sub(r'^"|"$', "", line).split('","')
        if len(row) < 9:
            continue
        code, name = row[0], row[1]
        try:
            close = float(row[8].replace(",", ""))
        except (ValueError, IndexError):
            continue
        if close > 0:
            prices[code] = {"name": name, "close": close, "market": "tse"}
    return prices

def fetch_otc(d):
    """上櫃：TPEX DAILY_CLOSE CSV"""
    url = (f"https://www.tpex.org.tw/web/stock/aftertrading/DAILY_CLOSE_quotes/"
           f"stk_quote_result.php?l=zh-tw&o=data&d={d.strftime('%Y/%m/%d')}&response=Text")
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        text = r.read().decode("utf-8", errors="replace")

    prices = {}
    for line in text.strip().split("\n")[1:]:
        line = line.strip()
        if not line.startswith('"'):
            continue
        row = re.sub(r'^"|"$', "", line).split('","')
        if len(row) < 4:
            continue
        code = row[1]
        if not re.match(r"^\d{4}$", code):
            continue
        try:
            close = float(row[3].replace(",", ""))
        except (ValueError, IndexError):
            continue
        if close > 0:
            prices[code] = {"name": row[2], "close": close, "market": "otc"}
    return prices

def update_sheet(prices):
    """把 Sheet 裡每支股票的股價欄（第3欄）更新為最新收盤價"""
    if not GSHEET_ID:
        print("  GSHEET_ID 未設定，跳過 Sheet 回寫")
        return
    import gspread
    gc = gspread.service_account(filename=SA_KEY_FILE)
    ws = gc.open_by_key(GSHEET_ID).sheet1
    rows = ws.get_all_values()
    if len(rows) <= 1:
        print("  Sheet 無股票資料，跳過回寫")
        return

    updates = []
    for i, row in enumerate(rows[1:], start=2):  # 從第2列開始（跳過標題）
        code = (row[0] if len(row) > 0 else "").strip()
        if not code:
            continue
        price = prices.get(code, {}).get("close")
        if price is not None:
            updates.append({"range": f"C{i}", "values": [[price]]})

    if updates:
        ws.batch_update(updates)
        print(f"  ✅ Sheet 回寫 {len(updates)} 筆股價")
    else:
        print("  Sheet 股票代號均無對應收盤價")

def main():
    d = smart_date()
    print(f"[{datetime.now(TZ).strftime('%H:%M:%S')}] 抓取 {d.strftime('%Y-%m-%d')} 收盤價")

    print("  上市（TWSE）...")
    tse = fetch_tse(d)
    print(f"  → {len(tse)} 筆")

    print("  上櫃（TPEX）...")
    try:
        otc = fetch_otc(d)
        print(f"  → {len(otc)} 筆")
    except Exception as e:
        print(f"  上櫃失敗：{e}")
        otc = {}

    prices = {**tse, **otc}
    CACHE_FILE.write_text(json.dumps({
        "date":    d.strftime("%Y-%m-%d"),
        "updated": datetime.now(TZ).isoformat(),
        "prices":  prices,
    }, ensure_ascii=False))
    print(f"  已存 {len(prices)} 筆 → {CACHE_FILE}")

    print("  回寫 Google Sheet...")
    try:
        update_sheet(prices)
    except Exception as e:
        print(f"  Sheet 回寫失敗：{e}")

if __name__ == "__main__":
    main()
