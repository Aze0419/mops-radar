#!/usr/bin/env python3
"""抓最新收盤價：每日 14:15 執行，抓 TWSE+TPEX 存成 prices.json 並回寫 Supabase stock_prices"""
import json, os, re, urllib.request
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

_env = Path(__file__).parent / ".env"
if _env.exists():
    for _line in _env.read_text().splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#"):
            _line = _line.replace("export ", "", 1)
            if "=" in _line:
                _k, _v = _line.split("=", 1)
                os.environ.setdefault(_k.strip().strip('"').strip("'"), _v.strip().strip('"').strip("'"))

TZ         = ZoneInfo("Asia/Taipei")
CACHE_FILE = Path(__file__).parent / "prices.json"

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
        try:
            volume = int(row[2].replace(",", ""))
        except (ValueError, IndexError):
            volume = None
        if close > 0:
            prices[code] = {"name": name, "close": close, "volume": volume, "market": "tse"}
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
        try:
            volume = int(row[9].replace(",", ""))
        except (ValueError, IndexError):
            volume = None
        if close > 0:
            prices[code] = {"name": row[2], "close": close, "volume": volume, "market": "otc"}
    return prices

def upsert_supabase(d, prices):
    """把當日全市場收盤價寫入 Supabase stock_prices（code,date,close,volume；shares 單位）"""
    url, key = os.environ.get("SUPABASE_URL"), os.environ.get("SUPABASE_SERVICE_KEY")
    if not url or not key:
        print("  SUPABASE_URL/SUPABASE_SERVICE_KEY 未設定，跳過回寫")
        return
    from supabase import create_client
    client = create_client(url, key)
    date_str = d.strftime("%Y-%m-%d")
    rows = [
        {"code": code, "date": date_str, "close": p["close"], "volume": p["volume"]}
        for code, p in prices.items()
    ]
    CHUNK = 500
    written = 0
    for i in range(0, len(rows), CHUNK):
        chunk = rows[i:i + CHUNK]
        client.table("stock_prices").upsert(chunk, on_conflict="code,date").execute()
        written += len(chunk)
    print(f"  ✅ Supabase 回寫 {written} 筆股價")

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

    print("  回寫 Supabase...")
    try:
        upsert_supabase(d, prices)
    except Exception as e:
        print(f"  Supabase 回寫失敗：{e}")

if __name__ == "__main__":
    main()
