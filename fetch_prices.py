#!/usr/bin/env python3
"""抓最新收盤價：每日 14:15 執行，抓 TWSE+TPEX 存成 prices.json 並回寫 Supabase stock_prices"""
import json, os, re, time, urllib.error, urllib.request
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

RETRY_INTERVAL = 300      # 資料還沒發布時，隔幾秒再試一次（TWSE 建議別打太密）
RETRY_UNTIL    = (15, 30) # 最晚重試到這個時間點（台北時區），避免卡住整個排程
REQUEST_GAP    = 3        # 每次對外請求之間至少停頓幾秒，降低碰到流量限制的機率

def smart_date():
    """13:30 前用昨天，週末往前推到週五"""
    d = datetime.now(TZ)
    if d.hour < 13 or (d.hour == 13 and d.minute < 30):
        d -= timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d

def fetch_tse(d):
    """上市：TWSE 官網 MI_INDEX JSON（response=csv 已確認會 404，只有 response=json 能用；
    2026-08-10 實測比官方 openapi.twse.com.tw 的 STOCK_DAY_ALL 更早有當天資料，當主力）
    """
    url = (f"https://www.twse.com.tw/exchangeReport/MI_INDEX"
           f"?date={d.strftime('%Y%m%d')}&type=ALLBUT0999&response=json")
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        data = json.load(r)

    if data.get("date") != d.strftime("%Y%m%d"):
        # 該日查無資料（休市或尚未發布），不能把別天的資料標成 d
        return {}

    table = next((t for t in data.get("tables", []) if t.get("fields") and t["fields"][0] == "證券代號"), None)
    if not table:
        return {}

    prices = {}
    for row in table["data"]:
        code, name = row[0], row[1]
        if not re.match(r"^\d{4}$", code):
            continue
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
    """上櫃：TPEX OpenAPI tpex_mainboard_quotes（2026-08-10 起改用官方 openapi，取代網站前端的 dailyQuotes ajax 端點）
    同 fetch_tse，沒有 date 參數、永遠回最新一個交易日，用 Date 欄位比對 d，
    對不上代表當天資料還沒發布，回傳空 dict（不能把別天的資料標成 d）。
    """
    url = "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_quotes"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=60) as r:
        rows = json.load(r)

    roc_date = f"{d.year - 1911}{d.strftime('%m%d')}"
    prices = {}
    for row in rows:
        if row.get("Date") != roc_date:
            continue
        code = row.get("SecuritiesCompanyCode", "")
        if not re.match(r"^\d{4}$", code):
            continue
        try:
            close = float(row["Close"])
        except (ValueError, KeyError, TypeError):
            continue
        try:
            volume = int(row["TradingShares"])
        except (ValueError, KeyError, TypeError):
            volume = None
        if close > 0:
            prices[code] = {"name": row.get("CompanyName"), "close": close, "volume": volume, "market": "otc"}
    return prices

def fetch_tse_openapi(d):
    """上市備援：TWSE 官方 openapi.twse.com.tw STOCK_DAY_ALL（文件化正式 API，
    但 2026-08-10 實測比官網本身的 MI_INDEX JSON 慢，當第二條路而非主力）
    """
    url = "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        rows = json.load(r)

    roc_date = f"{d.year - 1911}{d.strftime('%m%d')}"
    prices = {}
    for row in rows:
        if row.get("Date") != roc_date:
            continue
        code = row.get("Code", "")
        if not re.match(r"^\d{4}$", code):
            continue
        try:
            close = float(row["ClosingPrice"])
        except (ValueError, KeyError, TypeError):
            continue
        try:
            volume = int(row["TradeVolume"])
        except (ValueError, KeyError, TypeError):
            volume = None
        if close > 0:
            prices[code] = {"name": row.get("Name"), "close": close, "volume": volume, "market": "tse"}
    return prices

def fetch_with_retry(fetch_fn, d, label, fallback_fn=None):
    """每輪都先試主力、空了再試備援（如果有），兩個都沒有才睡 RETRY_INTERVAL 再重試，
    直到拿到非空資料或超過 RETRY_UNTIL 才放棄。HTTP 429（撞到流量限制）多睡久一點。
    """
    sources = [fn for fn in (fetch_fn, fallback_fn) if fn]
    while True:
        for fn in sources:
            try:
                prices = fn(d)
                if prices:
                    return prices
            except urllib.error.HTTPError as e:
                if e.code == 429:
                    print(f"  {label} 被限流（429），多睡一會兒")
                    time.sleep(60)
                else:
                    print(f"  {label} HTTP {e.code}（{fn.__name__}）")
            except Exception as e:
                print(f"  {label} 失敗：{e}（{fn.__name__}）")
        print(f"  {label} 資料還沒發布")

        now = datetime.now(TZ)
        if (now.hour, now.minute) >= RETRY_UNTIL:
            print(f"  {label} 等到 {RETRY_UNTIL[0]}:{RETRY_UNTIL[1]:02d} 還是沒有資料，放棄")
            return {}
        time.sleep(RETRY_INTERVAL)

def upsert_supabase(d, prices):
    """把當日全市場收盤價寫入 Supabase stock_prices（code,date,close,volume；shares 單位）
    stock_basic_info 是「已登記個股」白名單，不含 ETF（0050 等），這些代碼會撞 stock_prices_code_fkey。
    整批（500 筆）撞牆就逐筆重試，只跳過真的不在白名單的代碼，不能讓少數幾筆壞資料
    連坐拖垮同一批甚至後面所有批次（2026-08-10 才發現：一失敗就整個迴圈中斷，
    後面的批次完全不會嘗試）。
    """
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
    written, skipped = 0, []
    for i in range(0, len(rows), CHUNK):
        chunk = rows[i:i + CHUNK]
        try:
            client.table("stock_prices").upsert(chunk, on_conflict="code,date").execute()
            written += len(chunk)
        except Exception:
            for row in chunk:
                try:
                    client.table("stock_prices").upsert([row], on_conflict="code,date").execute()
                    written += 1
                except Exception:
                    skipped.append(row["code"])
    note = f"（跳過不在白名單的 {len(skipped)} 筆：{','.join(skipped[:10])}{'...' if len(skipped) > 10 else ''}）" if skipped else ""
    print(f"  ✅ Supabase 回寫 {written} 筆股價{note}")

def main():
    d = smart_date()
    print(f"[{datetime.now(TZ).strftime('%H:%M:%S')}] 抓取 {d.strftime('%Y-%m-%d')} 收盤價")

    print("  上市（TWSE）...")
    tse = fetch_with_retry(fetch_tse, d, "上市", fallback_fn=fetch_tse_openapi)
    print(f"  → {len(tse)} 筆")

    time.sleep(REQUEST_GAP)

    print("  上櫃（TPEX）...")
    otc = fetch_with_retry(fetch_otc, d, "上櫃")
    print(f"  → {len(otc)} 筆")

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
