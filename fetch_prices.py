#!/usr/bin/env python3
"""抓最新收盤價：每日 14:15 執行，抓 TWSE+TPEX 存成 prices.json"""
import json, re, urllib.request
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from pathlib import Path

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
    """上市：TWSE MI_INDEX CSV（4碼代號開頭的行）"""
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
    lines = text.strip().split("\n")
    for line in lines[1:]:  # 跳過標題
        line = line.strip()
        if not line.startswith('"'):
            continue
        row = re.sub(r'^"|"$', "", line).split('","')
        if len(row) < 4:
            continue
        # TPEX 欄位：日期, 代號, 名稱, 收盤, ...
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

if __name__ == "__main__":
    main()
