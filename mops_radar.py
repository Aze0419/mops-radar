#!/usr/bin/env python3
"""MOPS 飆股雷達：每日監控重大公告 + AI分析 → Telegram + Notion"""
import re, json, time, urllib.request, urllib.parse, urllib.error
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import os, pathlib as _pl
_env = _pl.Path(__file__).parent / ".env"
if _env.exists():
    for _line in _env.read_text().splitlines():
        if _line.strip() and not _line.startswith("#") and "=" in _line:
            _k, _v = _line.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip())
# ── 設定（從環境變數讀取，或建立 .env 後用 python-dotenv 載入）──
OPENROUTER_KEY   = os.environ["OPENROUTER_KEY"]
TELEGRAM_TOKEN   = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
NOTION_TOKEN     = os.environ["NOTION_TOKEN"]
NOTION_DB_ID     = os.environ["NOTION_DB_ID"]
GSHEET_ID        = os.environ["GSHEET_ID"]
SA_KEY_FILE      = os.environ.get("SA_KEY_FILE", "/Users/iroman/ai-hedge-fund-tw/google-sa.json")
TZ               = ZoneInfo("Asia/Taipei")
AI_MODEL         = "google/gemini-3.1-flash-lite-preview"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-TW,zh;q=0.9,en-US;q=0.8",
}

# ── 工具函式 ───────────────────────────────────────────────────────
def http_get(url, headers=None, timeout=30):
    h = {**HEADERS, **(headers or {})}
    req = urllib.request.Request(url, headers=h)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", errors="replace")

def http_post(url, data, headers=None, timeout=30):
    h = {"Content-Type": "application/x-www-form-urlencoded", **HEADERS, **(headers or {})}
    body = urllib.parse.urlencode(data).encode()
    req = urllib.request.Request(url, data=body, headers=h, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", errors="replace")

def http_post_json(url, payload, headers=None, timeout=60):
    h = {"Content-Type": "application/json", **(headers or {})}
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), headers=h, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())

def strip_tags(s):
    s = re.sub(r'<br\s*/?>', '\n', s, flags=re.I)
    s = re.sub(r'<[^>]+>', '', s)
    s = s.replace('&nbsp;', ' ').replace('&amp;', '&')
    return re.sub(r'\n{3,}', '\n\n', s).strip()

def pad6(s):
    return re.sub(r'\D', '', str(s)).zfill(6)

def yyyymmdd_to_iso(s):
    m = re.match(r'^(\d{4})(\d{2})(\d{2})$', s or '')
    return f"{m.group(1)}-{m.group(2)}-{m.group(3)}" if m else ''

# ── 1. 讀取 Google Sheet 自選股清單 ───────────────────────────────
def read_watchlist():
    import gspread
    gc = gspread.service_account(filename=SA_KEY_FILE)
    ws = gc.open_by_key(GSHEET_ID).sheet1
    rows = ws.get_all_values()
    watchlist = {}
    for r in rows[1:]:
        code = (r[0] if len(r) > 0 else '').strip()
        name = (r[1] if len(r) > 1 else '').strip()
        if code:
            watchlist[code] = name
    print(f"  自選股清單：{len(watchlist)} 檔")
    return watchlist

# ── 2. 抓 MOPS 昨日公告清單 ───────────────────────────────────────
def fetch_announcements(roc_year, month, day):
    html = http_post(
        "https://mopsov.twse.com.tw/mops/web/ajax_t05st02",
        {"firstin": "true", "off": "1", "step": "1", "step00": "0",
         "TYPEK": "all", "year": roc_year, "month": month, "day": day},
        timeout=30
    )
    if '查無需求資料' in html:
        return []
    return parse_announcement_list(html)

def parse_announcement_list(html):
    out = []
    forms = re.findall(r'<form\b[^>]*>[\s\S]*?</form>', html, re.I)
    for idx, form in enumerate(forms):
        h = {}
        for m in re.finditer(r'<input[^>]*name=["\']h(\d+)["\'][^>]*value=["\']([^"\']*)["\']', form, re.I):
            h[int(m.group(1))] = m.group(2)
        base = idx * 10
        get = lambda n: h.get(base + n, '')
        code    = get(1)
        name    = get(0)
        date8   = get(2)
        time6   = get(3)
        subject = strip_tags(get(4))
        clause  = f"第{get(6)}款" if get(6) else ''
        fact8   = get(7)
        detail  = strip_tags(get(8))

        # 抓 onclick 參數，供後續抓詳細頁
        onclick_m = re.search(
            r"SEQ_NO\.value='(\d+)'[^;]*;[^;]*SPOKE_TIME\.value='(\d+)'[^;]*;[^;]*SPOKE_DATE\.value='(\d+)'",
            form, re.I
        )
        seq_no     = onclick_m.group(1) if onclick_m else ''
        spoke_time = onclick_m.group(2) if onclick_m else ''
        spoke_date = onclick_m.group(3) if onclick_m else ''

        if code or name or subject:
            t = pad6(time6)
            out.append({
                '公司代號':    code,
                '公司名稱':    name,
                '發言日期':    yyyymmdd_to_iso(date8),
                '發言時間':    f"{t[:2]}:{t[2:4]}:{t[4:]}",
                '主旨':        subject,
                '符合條款':    clause,
                '事實發生日':  yyyymmdd_to_iso(fact8),
                '說明':        detail,
                '_seq_no':     seq_no,
                '_spoke_time': spoke_time,
                '_spoke_date': spoke_date,
            })
    return out

# ── 2b. 抓公告詳細頁，取得完整「說明」────────────────────────────
def fetch_detail(company_id, spoke_time, spoke_date, seq_no):
    url = (f"https://mopsov.twse.com.tw/mops/web/ajax_t05sr01_1"
           f"?firstin=true&stp=1&step=1"
           f"&SEQ_NO={seq_no}&SPOKE_TIME={spoke_time}&SPOKE_DATE={spoke_date}&COMPANY_ID={company_id}")
    for attempt in range(3):
        try:
            html = http_get(url, timeout=30)
            m = re.search(r'<th[^>]*>說明</th>\s*<td[^>]*colspan=[\'"]?5[\'"]?[^>]*>([\s\S]*?)</td>', html, re.I)
            if m:
                return strip_tags(m.group(1))
            # fallback：找 <pre> 裡的說明
            m2 = re.search(r'<pre[^>]*>([\s\S]*?)</pre>', html, re.I)
            if m2:
                return strip_tags(m2.group(1))
            return ''
        except Exception as e:
            print(f"    詳細頁 retry {attempt+1}/3：{e}")
            time.sleep(3)
    return ''

# ── 3. 取收盤價（優先讀本日快取，否則 fallback OpenAPI）──────────
def fetch_prices():
    from pathlib import Path
    cache_file = Path(__file__).parent / "prices.json"
    if cache_file.exists():
        cache = json.loads(cache_file.read_text())
        date = cache.get("date", "")
        prices = {k: v["close"] for k, v in cache.get("prices", {}).items()}
        if prices:
            print(f"  讀快取股價：{date}，共 {len(prices)} 筆")
            return prices
    prices = {}
    try:
        with urllib.request.urlopen(
            "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL", timeout=15
        ) as r:
            for d in json.loads(r.read()):
                code = (d.get('Code') or '').strip()
                p = float((d.get('ClosingPrice') or '0').replace(',', ''))
                if code and p > 0:
                    prices[code] = p
        print(f"  OpenAPI fallback：{len(prices)} 筆")
    except Exception as e:
        print(f"  TWSE 股價 API 失敗: {e}")
    return prices

# ── 4. 預算本益比 ─────────────────────────────────────────────────
def _parse_num(s):
    s = str(s).strip()
    neg = s.startswith('(') or s.startswith('（')
    s = re.sub(r'[（()）,]', '', s)
    try:
        return (-1 if neg else 1) * float(s)
    except:
        return None

def calc_pe(detail, price):
    nums, pcts = [], []
    block = re.search(r'每股盈餘[\s\S]*', detail)
    if block:
        for m in re.finditer(r'[（(]?-?\d+[\d,]*\.?\d*[)）]?(%)?', block.group()):
            raw = m.group()
            v = _parse_num(raw.replace('%', ''))
            if v is not None:
                (pcts if m.group(1) else nums).append(v)

    m_eps = nums[0] if len(nums) > 0 else None
    q_eps = nums[1] if len(nums) > 1 else None
    m_yoy = pcts[0] if len(pcts) > 0 else None

    m_rev, r_yoy = None, None
    rev_block = re.search(r'營業收入[\s\S]*', detail)
    if rev_block:
        rn, rp = [], []
        for m2 in re.finditer(r'[（(]?-?\d+[\d,]*\.?\d*[)）]?(%)?', rev_block.group()):
            raw = m2.group()
            v = _parse_num(raw.replace('%', ''))
            if v is not None:
                (rp if m2.group(1) else rn).append(v)
        m_rev = rn[0] if rn else None
        r_yoy = rp[0] if rp else None

    eps, mult, src = m_eps, 12, '月'
    if eps is None:
        eps, mult, src = q_eps, 4, '季'
    annual = round(eps * mult, 2) if eps is not None else None
    pe = round(price / annual, 2) if (annual and annual > 0 and price > 0) else None
    pe_note = (f"{price} ÷ {annual} = {pe}倍" if pe else
               ('虧損' if annual and annual <= 0 else '無EPS資料'))
    return {
        'pre_monthly_eps':         m_eps,
        'pre_monthly_eps_yoy':     m_yoy,
        'pre_quarterly_eps':       q_eps,
        'pre_monthly_revenue':     m_rev,
        'pre_monthly_revenue_yoy': r_yoy,
        'pre_eps_source':          src,
        'pre_annual_eps':          annual,
        'pre_pe':                  pe,
        'pre_pe_note':             pe_note,
    }

# ── 5. OpenRouter AI 分析 ─────────────────────────────────────────
SYSTEM_PROMPT = """你是一位專業的台灣股票分析師，根據資料與最新網路資訊進行投資評分。

【即時搜尋要求】每次分析前先搜尋最新新聞，若找不到須說明。

【評級標準】
路徑A（低本益比）：本益比低於或等於20 + EPS成長 + 營收不衰退 → 🔴 強烈買進
路徑B（題材支撐）：本益比高於20 + 熱門題材 + EPS大幅成長 → 🔴 強烈買進
🟠 建議買進：EPS或營收正成長 + 本益比低於或等於30
🟡 一般觀望：成長有限或本益比高於30且無題材
🟢 需要小心：營收或EPS年減、虧損

【禁止事項】
- 不可使用 < > 符號（改用「大於」「小於」「低於」「高於」「不超過」等中文）
- 不可使用引用標記 [1][2] 等數字方括號
- 只可用 <b></b> 粗體標籤，其他HTML標籤一律禁止，換行用真實換行字元
- 分析中不可輸出「燈號」兩字

【輸出】只輸出純JSON，不加markdown或反引號。
欄位：display_text(string,<b></b>粗體,真實換行符), ai_rating(string,含燈號emoji), monthly_eps(number|null), eps_yoy(number|null), monthly_revenue(string), revenue_yoy(number|null), estimated_annual_eps(number|null), estimated_pe(number|null), rating_reason(string), industry_risk(string)

display_text格式：第1段燈號emoji開頭，各段標題<b></b>。"""

def analyze(ann, price, pe):
    user_msg = (
        f"請分析：\n股票：{ann['公司名稱']}（{ann['公司代號']}）\n股價：{price}元\n\n"
        f"【系統預算值】\n"
        f"單月EPS：{pe['pre_monthly_eps'] or '無'}元｜年增率：{pe['pre_monthly_eps_yoy'] or '無'}%\n"
        f"單月營收：{pe['pre_monthly_revenue'] or '無'}百萬｜年增率：{pe['pre_monthly_revenue_yoy'] or '無'}%\n"
        f"預估全年EPS：{pe['pre_annual_eps'] or '無'}元\n"
        f"預估本益比：{pe['pre_pe_note']}\n\n"
        f"公告內容：\n{ann['說明'][:3000]}"
    )
    result = http_post_json(
        "https://openrouter.ai/api/v1/chat/completions",
        {"model": AI_MODEL, "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user",   "content": user_msg}
        ]},
        headers={"Authorization": f"Bearer {OPENROUTER_KEY}"},
        timeout=120
    )
    raw = result["choices"][0]["message"]["content"]
    start = raw.find('{'); end = raw.rfind('}') + 1
    return json.loads(raw[start:end]) if start >= 0 else {"display_text": raw, "ai_rating": "🟡 一般觀望"}

# ── 6. Telegram ───────────────────────────────────────────────────
def send_telegram(text):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    for chunk in [text[i:i+4000] for i in range(0, len(text), 4000)]:
        payload = {"chat_id": TELEGRAM_CHAT_ID, "text": chunk, "parse_mode": "HTML"}
        req = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                     headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=10)

# ── 7. Notion 同步 ────────────────────────────────────────────────
def notion_req(method, path, payload=None):
    url = f"https://api.notion.com/v1/{path}"
    h = {"Authorization": f"Bearer {NOTION_TOKEN}",
         "Notion-Version": "2022-06-28",
         "Content-Type": "application/json"}
    data = json.dumps(payload).encode() if payload else None
    req = urllib.request.Request(url, data=data, headers=h, method=method)
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        print(f"  Notion {method} /{path} {e.code}: {e.read().decode()[:200]}")
        return {}

def rt(text):
    return [{"type": "text", "text": {"content": str(text)[:2000]}}]

def sync_notion(ann, ai, pe, price):
    code = ann['公司代號']
    resp = notion_req("POST", f"databases/{NOTION_DB_ID}/query", {
        "filter": {"property": "股票代號", "title": {"equals": code}},
        "sorts": [{"property": "公告次數", "direction": "descending"}],
        "page_size": 5
    })
    results = resp.get("results", [])
    max_count = max(
        (r.get("properties", {}).get("公告次數", {}).get("number", 0) or 0 for r in results),
        default=0
    )
    new_count = max_count + 1
    latest_yes = next(
        (r for r in results if r.get("properties", {}).get("是否最新", {}).get("checkbox")), None
    )
    first_date = min(
        (r["properties"]["首次發現日期"]["date"]["start"]
         for r in results
         if r.get("properties", {}).get("首次發現日期", {}).get("date", {}).get("start")),
        default=datetime.now(TZ).date().isoformat()
    )

    props = {
        "股票代號":       {"title": rt(code)},
        "股票名稱":       {"rich_text": rt(ann['公司名稱'])},
        "最新股價":       {"number": price or None},
        "AI評級燈號":     {"select": {"name": str(ai.get("ai_rating", "🟡 一般觀望"))[:100]}},
        "AI完整分析":     {"rich_text": rt(ai.get("display_text", ""))},
        "最新公告主旨":   {"rich_text": rt(ann['主旨'])},
        "最新公告說明":   {"rich_text": rt(ann['說明'][:2000])},
        "符合條款":       {"rich_text": rt(ann['符合條款'])},
        "公告次數":       {"number": new_count},
        "最新單月EPS":    {"number": ai.get("monthly_eps")},
        "EPS年增率":      {"number": ai.get("eps_yoy")},
        "最新單月營收":   {"rich_text": rt(ai.get("monthly_revenue", ""))},
        "營收年增率":     {"number": ai.get("revenue_yoy")},
        "預估全年EPS":    {"number": pe.get("pre_annual_eps")},
        "評分理由":       {"rich_text": rt(ai.get("rating_reason", ""))},
        "產業熱度與風險": {"rich_text": rt(ai.get("industry_risk", ""))},
        "是否最新":       {"checkbox": True},
        "首次發現日期":   {"date": {"start": first_date}},
    }
    if ann['發言日期']:
        props["最新公告日期"] = {"date": {"start": ann['發言日期']}}

    if latest_yes:
        notion_req("PATCH", f"pages/{latest_yes['id']}",
                   {"properties": {"是否最新": {"checkbox": False}}})
    notion_req("POST", "pages", {"parent": {"database_id": NOTION_DB_ID}, "properties": props})

# ── 主程式 ────────────────────────────────────────────────────────
def main():
    now = datetime.now(TZ)
    yesterday = now - timedelta(days=1)
    roc_year = str(yesterday.year - 1911)
    month = yesterday.strftime('%m')
    day   = yesterday.strftime('%d')
    print(f"[{now.strftime('%H:%M:%S')}] 查詢 {yesterday.strftime('%Y-%m-%d')}（民國{roc_year}/{month}/{day}）公告")

    print("讀取自選股清單...")
    watchlist = read_watchlist()
    if not watchlist:
        print("  ⚠️ 自選股清單是空的")
        return

    print("抓取 MOPS 公告...")
    announcements = fetch_announcements(roc_year, month, day)
    print(f"  公告總數：{len(announcements)} 筆")

    eps_re = re.compile(r'每股盈餘[^\n。，]*\d+\.\d+')
    matched = [a for a in announcements
               if eps_re.search(a.get('說明', '')) and a['公司代號'] in watchlist]
    print(f"  符合條件：{len(matched)} 筆")

    if not matched:
        send_telegram(f"📭 今日（{now.strftime('%Y/%m/%d')}）沒有符合訊號的公告")
        return

    # 抓詳細頁，取得完整「說明」
    print("抓取公告詳細內容...")
    for ann in matched:
        if ann.get('_seq_no') and ann.get('_spoke_time') and ann.get('_spoke_date'):
            print(f"  抓詳細頁：{ann['公司代號']} {ann['主旨'][:30]}")
            detail = fetch_detail(ann['公司代號'], ann['_spoke_time'], ann['_spoke_date'], ann['_seq_no'])
            if detail:
                ann['說明'] = detail
                print(f"    說明長度：{len(detail)} 字")
        time.sleep(2)

    print("抓取股價...")
    prices = fetch_prices()

    for ann in matched:
        code = ann['公司代號']
        ann['公司名稱'] = ann['公司名稱'] or watchlist.get(code, code)
        price = prices.get(code, 0)
        print(f"\n處理 {code} {ann['公司名稱']}（股價 {price}）")

        pe = calc_pe(ann['說明'], price)
        print(f"  預估本益比：{pe['pre_pe_note']}")

        print("  AI 分析中...")
        try:
            ai = analyze(ann, price, pe)
        except Exception as e:
            print(f"  AI 失敗：{e}")
            ai = {"display_text": f"AI分析失敗：{e}", "ai_rating": "🟡 一般觀望"}

        header = (f"📢【{ann['公司名稱']}｜{code}】\n"
                  f"📅 {ann['發言日期']} {ann['發言時間']}\n"
                  f"📌 {ann['主旨']}\n"
                  f"📑 {ann['符合條款']}\n\n"
                  f"🤖 <b>AI 分析：</b>\n")
        send_telegram(header + ai.get("display_text", ""))
        print("  ✅ Telegram 送出")

        try:
            sync_notion(ann, ai, pe, price)
            print("  ✅ Notion 同步")
        except Exception as e:
            print(f"  Notion 失敗：{e}")

        time.sleep(60)

    print(f"\n完成！共處理 {len(matched)} 筆")

if __name__ == "__main__":
    main()
