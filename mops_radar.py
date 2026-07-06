#!/usr/bin/env python3
"""MOPS 飆股雷達：每日監控重大公告 + AI分析 → Telegram + Google Sheet"""
import re, json, time, urllib.request, urllib.parse, urllib.error
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import os, pathlib as _pl
_env = _pl.Path(__file__).parent / ".env"
if _env.exists():
    for _line in _env.read_text().splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#"):
            _line = _line.replace("export ", "", 1)
            if "=" in _line:
                _k, _v = _line.split("=", 1)
                os.environ.setdefault(_k.strip().strip(chr(34)).strip(chr(39)), _v.strip().strip(chr(34)).strip(chr(39)))
# ── 設定（從環境變數讀取，或建立 .env 後用 python-dotenv 載入）──
OPENROUTER_KEY   = os.environ["OPENROUTER_KEY"]
TELEGRAM_TOKEN   = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
GSHEET_ID        = os.environ["GSHEET_ID"]
RADAR_SHEET_ID   = os.environ.get("RADAR_SHEET_ID", "1UulUtCjGbBUk_36xCK7TuRSrEvBFCCr7okKWBlJBvuc")
RADAR_SHEET_NAME = "公告紀錄"
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
        return json.loads(r.read()) or {}

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


# ── Dashboard Excel cache ───────────────────────────────────────────────────
from pathlib import Path as _Path
_DASHBOARD_PATH = _Path.home() / "投資系統" / "dashboard_latest.xlsx"
_dashboard_cache = {"df": None, "mtime": 0.0}

def _get_dashboard_row(code):
    try:
        mtime = _DASHBOARD_PATH.stat().st_mtime if _DASHBOARD_PATH.exists() else 0.0
        if mtime and mtime != _dashboard_cache["mtime"]:
            import pandas as pd
            _dashboard_cache["df"] = pd.read_excel(_DASHBOARD_PATH, dtype={"股號": str})
            _dashboard_cache["mtime"] = mtime
    except Exception:
        pass
    df = _dashboard_cache.get("df")
    if df is None:
        return None
    row = df[df["股號"] == str(code)]
    return row.iloc[0].to_dict() if not row.empty else None


# ── 1. 讀取 Google Sheet（快取股價，避免同一次執行重複連線）
_sheet_rows_cache = None

def _get_sheet_rows():
    global _sheet_rows_cache
    if _sheet_rows_cache is None:
        import gspread
        gc = gspread.service_account(filename=SA_KEY_FILE)
        _sheet_rows_cache = gc.open_by_key(GSHEET_ID).sheet1.get_all_values()
    return _sheet_rows_cache

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
    for form in forms:
        h = {}
        for m in re.finditer(r'<input[^>]*name=["\']h(\d+)["\'][^>]*value=["\']([^"\']*)["\']', form, re.I):
            h[int(m.group(1))] = m.group(2)
        if not h:
            continue  # 搜尋/導覽 form，無資料欄位
        base = (min(h.keys()) // 10) * 10  # 從實際 h-key 推導 base，不依賴 form 索引
        get = lambda n, _b=base: h.get(_b + n, '')
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
            r"SEQ_NO\.value='(\d+)'.*?SPOKE_TIME\.value='(\d+)'.*?SPOKE_DATE\.value='(\d+)'",
            form, re.I | re.DOTALL
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

# ── 2b. 從 t05sr01_1 取得最新公告的 onclick 參數（SEQ_NO 等）─────
def fetch_onclick_params():
    """GET t05sr01_1（無日期參數）→ 解析 onclick SEQ_NO/SPOKE_TIME/SPOKE_DATE/COMPANY_ID。
    早上 6 AM 時此頁面顯示的是昨日公告，與 fetch_announcements 查的日期吻合。"""
    try:
        html = http_get("https://mopsov.twse.com.tw/mops/web/t05sr01_1", timeout=30)
    except Exception as e:
        print(f"  t05sr01_1 取得失敗: {e}")
        return {}
    pattern = re.compile(
        r"SEQ_NO\.value='(\d+)'.*?SPOKE_TIME\.value='(\d+)'.*?"
        r"SPOKE_DATE\.value='(\d+)'.*?COMPANY_ID\.value='([^']+)'",
        re.DOTALL
    )
    params = {}
    for m in pattern.finditer(html):
        seq_no, spoke_time, spoke_date, company_id = m.groups()
        company_id = company_id.strip()
        key = (company_id, spoke_date, spoke_time)  # 三元組：同日多筆各自保留
        params[key] = (seq_no, spoke_time, spoke_date)
    print(f"  t05sr01_1 onclick 參數：{len(params)} 筆")
    return params

# ── 2c. 抓公告詳細頁，取得完整「說明」────────────────────────────
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
            m2 = re.search(r'<pre[^>]*>([\s\S]*?)</pre>', html, re.I)
            if m2:
                return strip_tags(m2.group(1))
            # HTML 載入但 regex 未中，繼續下一次嘗試（不 sleep）
        except Exception as e:
            print(f"    詳細頁 retry {attempt+1}/3：{e}")
            time.sleep(3)
    return ''

# ── 3. 取收盤價（優先讀 Google Sheet C欄，fallback prices.json）──
def fetch_prices():
    # 優先：從 Google Sheet 讀（與 V5.05 DataTable 同邏輯）
    try:
        rows = _get_sheet_rows()
        prices = {}
        for r in rows[1:]:
            code  = r[0].strip() if r else ''
            price = r[2].strip() if len(r) > 2 else ''
            if code and price:
                try:
                    prices[code] = float(price.replace(',', ''))
                except ValueError:
                    pass
        if prices:
            print(f"  讀 Google Sheet 股價：{len(prices)} 筆")
            return prices
    except Exception as e:
        print(f"  Google Sheet 股價讀取失敗：{e}，改用 prices.json")

    # Fallback：prices.json
    from pathlib import Path
    cache_file = Path(__file__).parent / "prices.json"
    if cache_file.exists():
        cache = json.loads(cache_file.read_text())
        date = cache.get("date", "")
        prices = {k: v["close"] for k, v in cache.get("prices", {}).items()}
        if prices:
            print(f"  讀快取股價：{date}，共 {len(prices)} 筆")
            return prices
    return {}

# ── 4. 預算本益比 ─────────────────────────────────────────────────
def _parse_num(s):
    s = str(s).strip()
    neg = s.startswith('(') or s.startswith('（')
    s = re.sub(r'[（()）,]', '', s)
    try:
        return (-1 if neg else 1) * float(s)
    except (ValueError, TypeError):
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
    pe = round(price / annual, 2) if (annual is not None and annual > 0 and price > 0) else None
    pe_note = (f"{price} ÷ {annual} = {pe}倍" if pe else
               ('虧損' if annual is not None and annual <= 0 else
                ('無股價資料' if not price else '無EPS資料')))
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
SYSTEM_PROMPT = """你是一位專業的台灣股票分析師，請根據提供的資料與你取得的最新網路資訊，進行簡潔明確的投資評分與風險提示。

【即時搜尋要求 - 非常重要請嚴格遵守】
1.你必須搜尋並引用該公司與其所屬產業的最新新聞與產業動態，不得只依賴使用者提供的公告內容與財報數據。
2.每次分析前，請先搜尋最新新聞與產業資訊，再進行評分與分析。
3.若找不到相關新聞，必須在最終輸出中明確說明：未能取得最新新聞，以下評估僅根據現有財務與公告資料。
4.當預估本益比大於20時，必須特別搜尋產業與個股熱度資訊，由你自行判斷該產業是否屬於當前市場追捧的熱門題材，並在產業熱度評估段落中清楚說明理由。

【資料來源與優先順序 - 領先指標模式】
為了取得市場領先消息，請務必依照下列順序擷取數據，由上而下檢索，一旦取得即停止往下搜尋：
1.單月 EPS（絕對優先）：若 Input 資料中有最近一月或當月的 EPS 數據，請直接使用此數據推估全年。邏輯：假設該月獲利能力能持續全年。
2.單季 EPS（次要優先）：若無單月數據，但有最近一季 EPS，則依此推估。
3.累計 EPS（最後手段）：僅在上述兩者皆缺席時，才使用最近四季累計或年度 EPS。
若缺少上述所有數據，須明確標示：缺 EPS 數據，無法計算預估本益比。

【括號數字規則】
括號內數字代表負數，如 (0.01) = -0.01。

【預估本益比 - 使用系統預算值，不要自己算】
⚠️ 重要：使用者輸入中有「系統預算值」區塊，包含系統已計算好的「預估全年EPS」和「預估本益比」。
- 請直接使用這些預算值進行評級判斷和分析，不要自己重新計算
- 在 display_text 的「關鍵數據」段落中，直接引用系統預算的本益比數值
- 在 estimated_annual_eps 和 estimated_pe 欄位，直接填入系統預算值
- 若系統預算值顯示「無法提取」或「無法計算」，代表從公告中找不到 EPS 數據，請在文字中說明缺少 EPS 資料
- 若 EPS 為負數，預估本益比無意義，estimated_pe 填 null

【你仍需要做的】
- 在 monthly_eps 欄位填入你從公告中辨識到的當月 EPS（用於驗證系統提取是否正確）
- 在 display_text 中展示完整算式過程供讀者參考（格式：股價 ÷ 預估全年EPS = X 倍）
- 根據系統提供的預估本益比來判斷評級

【評級標準 - 依優先順序綜合判斷，請嚴格執行】

🔴 強烈買進（滿足路徑 A 或路徑 B 任一即可）：

路徑 A：低本益比路線（預估本益比低於或等於20）
  - 預估本益比低於或等於20
  - EPS 有成長（年增率大於0%，含轉虧為盈）
  - 營收不衰退（年增率大於或等於0%）
  → 直接給 🔴，不需要題材比對

路徑 B：高本益比但有強力題材（預估本益比大於20）
  - 預估本益比大於20
  - 必須有熱門題材支撐（搜尋確認，由你自行判斷）
  - 且至少滿足以下之一：
    a. EPS 年增率大於30%（大幅成長）
    b. EPS 成長率大幅優於營收成長率（獲利品質優良，例如 EPS 年增 50% 但營收僅增 5%）
  - 必須在 industry_risk 欄位明確寫出匹配的題材名稱
  - 必須在 rating_reason 中說明高本益比的合理性
  → 可給 🔴
  - 若搜尋後判斷缺乏熱門題材支撐 → 降級為 🟠 建議買進

🟠 建議買進：
- 營收或獲利有正成長（EPS 年增率大於0% 或營收年增率大於0%）
- 預估本益比低於或等於30
- 不需要強烈題材支撐，但基本面健康
- 或：符合 🔴 大部分條件但題材支撐不足

🟡 一般觀望：
- 成長有限（EPS 和營收年增率均小於10%）
- 或估值偏高（預估本益比大於30）且無強力題材
- 或基本面尚可但缺乏明確成長亮點
- 或：公司仍虧損但虧損有收窄趨勢（轉虧為盈前夕）

🟢 需要小心：
- 營收年減或 EPS 年減（任一為負）
- 財務惡化、盈轉虧、仍處虧損且無收窄
- EPS 大幅衰退大於30%
- 或營收大幅衰退大於20%

【分析要求】
1.必須查詢該公司最新消息，搜尋公司與產業的近期新聞，了解營收或獲利大幅變動的原因。將與公告內容、財報數據相關的重點新聞納入分析。
2.必須顯示燈號，每個評級前必須有對應的燈號圖示（🔴🟠🟡🟢）。
3.深度分析：說明獲利成長或衰退的驅動因素、產業趨勢、競爭優勢或劣勢等。
4.當預估本益比大於20時，必須搜尋產業與個股熱度，評估是否為熱門產業或題材股，並說明高本益比是否合理。

【輸出格式 - 嚴格遵守】
你必須只輸出一個純 JSON 物件，不要輸出任何 JSON 以外的文字，不要加任何 markdown 標記或反引號（不要用 ```json 包住內容）。

【禁止事項 - 嚴格遵守】
1. 所有分析文字絕對不可包含引用標記如 [1]、[2]、[3] 等數字方括號。
2. 不可在文字中出現「根據來源1」「參考資料2」等搜尋引用文字。
3. 所有內容必須使用繁體中文，專有名詞（如 EPS、AI、PCB）可保留英文縮寫，但一般描述全部用中文。
4. 不可在 JSON 外部加任何說明、前言、或結語文字。
5. HTML 標籤限制：display_text 中只可使用 <b></b> 標籤做粗體。絕對禁止使用 <br>、<p>、<div>、<h1>、<ul>、<li> 等任何其他 HTML 標籤。換行請直接用真實的換行字元，不要用 <br>。
6. 嚴禁在分析內文中使用「<」或「>」符號（會造成 Telegram HTML 傳送失敗）。請改用中文描述：
   - 「小於」或「低於」取代 <（例：本益比低於20）
   - 「大於」或「高於」取代 >（例：EPS 年增大於30%）
   - 「介於...至...」表示區間
   - 「不超過」「至多」「至少」等語彙
   - 注意：此規則僅限制內文比較符號，<b></b> 粗體標籤仍可正常使用。
7. 不可輸出任何其他 HTML 實體（如 &lt;、&gt;、&amp;）或未成對的單一角括號符號。

JSON 欄位定義：
- display_text (string)：4段分析的完整可讀文字，段落間用換行分隔。必須使用 <b></b> 標籤標示重點（標籤必須成對出現，有 <b> 就一定要有 </b>）。

格式範例（直接用燈號 emoji，不要輸出「燈號」這兩個字）：
<b>🔴 強烈買進 - 大量(3167)</b>
<b>關鍵數據：</b>
• EPS：1.68元，年增率 242.86%
• 營收：7.73億元，年增率 146.18%
• 預估本益比：672 ÷ 20.16 = 33.33倍
<b>評分理由與成長動能分析：</b>
分析內容...
<b>產業熱度評估與風險提醒：</b>
匹配題材：<b>AI伺服器</b>。風險內容...

重要：
1. 第1段必須以真實的燈號 emoji 開頭（🔴、🟠、🟡、🟢 其中一個），絕對不可輸出「燈號」這兩個中文字
2. 每段標題必須用 <b></b> 包裹
3. 分析內文中，請自行判斷哪些是讀者最需要注意的重點，用 <b></b> 粗體標示
4. 每個 <b> 必須配對一個 </b>，絕對不可漏掉閉合標籤
- ai_rating (string)：必須是「🔴 強烈買進」或「🟠 建議買進」或「🟡 一般觀望」或「🟢 需要小心」
- monthly_eps (number 或 null)：當月EPS數值，虧損用負數，缺資料填 null
- eps_yoy (number 或 null)：EPS年增率百分比數值（如 778.57 代表 778.57%），衰退用負數，缺資料填 null
- monthly_revenue (string)：當月營收含單位（如「3.03億元」），缺資料填「缺資料」
- revenue_yoy (number 或 null)：營收年增率百分比數值，缺資料填 null
- estimated_annual_eps (number 或 null)：預估全年EPS（單月EPS x 12），缺資料填 null
- estimated_pe (number 或 null)：預估本益比數值，缺資料填 null
- rating_reason (string)：評分理由與成長動能分析的完整文字
- industry_risk (string)：產業熱度評估與風險提醒的完整文字

若缺少關鍵數據（如 EPS），在 display_text 中標示缺資料，對應數值欄位填 null。"""

def analyze(ann, price, pe, dashboard=None):
    v = lambda x: x if x is not None else '無'
    def dv(key):
        if dashboard is None:
            return '無'
        import math
        val = dashboard.get(key)
        return '無' if (val is None or (isinstance(val, float) and math.isnan(val))) else val
    NL = chr(10)
    dash_block = (
        NL + "【選股儀表板補充資料（請直接使用，毋需另行搜尋題材）】" + NL
        + "題材：" + str(dv('題材')) + NL
        + "毛利率：" + str(dv('毛利率')) + "%" + NL
        + "近4季ROE：" + str(dv('近4季ROE%')) + "%" + NL
        + "去年同期EPS：" + str(dv('去年同期EPS')) + "元" + NL
    ) if dashboard else ''
    user_msg = (
        f"請分析：\n股票：{ann['公司名稱']}（{ann['公司代號']}）\n股價：{price}元\n\n"
        f"【系統預算値】\n"
        f"單月EPS：{v(pe['pre_monthly_eps'])}元｜年增率：{v(pe['pre_monthly_eps_yoy'])}%\n"
        f"單月營收：{v(pe['pre_monthly_revenue'])}百萬｜年增率：{v(pe['pre_monthly_revenue_yoy'])}%\n"
        f"預估全年EPS：{v(pe['pre_annual_eps'])}元\n"
        f"預估本益比：{pe['pre_pe_note']}"
        + dash_block
        + f"\n公告內容：\n{ann['說明'][:3000]}"
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
    while text:
        if len(text) <= 4000:
            chunk, text = text, ''
        else:
            cut = text.rfind('\n', 0, 4000)
            cut = cut if cut != -1 else 4000  # 找不到換行才硬切
            chunk, text = text[:cut], text[cut+1:]
        payload = {"chat_id": TELEGRAM_CHAT_ID, "text": chunk, "parse_mode": "HTML"}
        req = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                     headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=10)

# ── 7. Google Sheet 同步 ─────────────────────────────────────────
RADAR_HEADERS = [
    "股票代號", "股票名稱", "最新股價", "AI評級燈號", "最新成交量", "公告次數",
    "最新單月EPS", "EPS年增率", "最新單月營收", "營收年增率", "預估全年EPS",
    "評分理由", "產業熱度與風險", "是否最新", "首次發現日期", "最新公告日期",
]
_radar_ws = None

def _get_radar_ws():
    global _radar_ws
    if _radar_ws is None:
        import gspread
        gc = gspread.service_account(filename=SA_KEY_FILE)
        ss = gc.open_by_key(RADAR_SHEET_ID)
        try:
            _radar_ws = ss.worksheet(RADAR_SHEET_NAME)
        except gspread.WorksheetNotFound:
            _radar_ws = ss.add_worksheet(RADAR_SHEET_NAME, rows=1000, cols=len(RADAR_HEADERS))
            _radar_ws.append_row(RADAR_HEADERS)
    return _radar_ws

_volumes_cache = None

def _get_volume(code):
    """從 prices.json 快取讀成交量（Google Sheet 沒有這欄，只能吃 fetch_prices.py 寫的快取）"""
    global _volumes_cache
    if _volumes_cache is None:
        cache_file = _pl.Path(__file__).parent / "prices.json"
        _volumes_cache = {}
        if cache_file.exists():
            cache = json.loads(cache_file.read_text())
            _volumes_cache = {k: v.get("volume") for k, v in cache.get("prices", {}).items()}
    v = _volumes_cache.get(code)
    return round(v / 1000) if v is not None else None

def sync_gsheet(ann, ai, pe, price):
    ws = _get_radar_ws()
    all_rows = ws.get_all_values()
    code = ann['公司代號']
    today = datetime.now(TZ).date().isoformat()

    # 找同股票代號的舊資料列（row_num 從 1 計，第 1 列是 header）
    code_rows = [
        (i + 2, r) for i, r in enumerate(all_rows[1:])
        if r and r[0] == code
    ]
    max_count = max((int(r[5]) for _, r in code_rows if len(r) > 5 and r[5].isdigit()), default=0)
    first_date = min(
        (r[14] for _, r in code_rows if len(r) > 14 and r[14]),
        default=today
    )

    # 把舊的「是否最新」改為 FALSE
    for row_num, r in code_rows:
        if len(r) > 13 and r[13] == '✅':
            ws.update_cell(row_num, 14, '❌')

    def v(x): return x if x is not None else ''
    new_row = [
        int(code) if code.isdigit() else code,  # 純數字代號存成數字，Sheet 才不會顯示成文字（'開頭）
        ann['公司名稱'],
        v(price),
        ai.get('ai_rating', '🟡 一般觀望'),
        v(_get_volume(code)),
        max_count + 1,
        v(ai.get('monthly_eps')),
        v(ai.get('eps_yoy')),
        ai.get('monthly_revenue', ''),
        v(ai.get('revenue_yoy')),
        v(pe.get('pre_annual_eps')),
        ai.get('rating_reason', ''),
        ai.get('industry_risk', ''),
        '✅',
        first_date,
        ann['發言日期'] or '',
    ]
    ws.insert_row(new_row, 2, value_input_option='RAW')

# ── 主程式 ────────────────────────────────────────────────────────
def main():
    now = datetime.now(TZ)
    days_back_list = [3, 2, 1] if now.weekday() == 0 else [1]  # 星期一補查上星期五六日三天，其他查前一天
    target_dates = [now - timedelta(days=d) for d in days_back_list]

    print("抓取 MOPS 公告...")
    announcements = []
    for target_date in target_dates:
        roc_year = str(target_date.year - 1911)
        month = target_date.strftime('%m')
        day   = target_date.strftime('%d')
        print(f"[{now.strftime('%H:%M:%S')}] 查詢 {target_date.strftime('%Y-%m-%d')}（民國{roc_year}/{month}/{day}）公告")
        announcements += fetch_announcements(roc_year, month, day)
    print(f"  公告總數：{len(announcements)} 筆")

    if not announcements:
        send_telegram(f"📭 今日（{now.strftime('%Y/%m/%d')}）沒有公告")
        return

    # 從 t05sr01_1 取 onclick 參數（SEQ_NO 等），供詳細頁使用
    print("取得公告 onclick 參數...")
    onclick_params = fetch_onclick_params()

    # 抓詳細頁，取得完整「說明」（在 EPS 過濾前執行，避免漏掉 EPS 只在詳細頁的公告）
    print("抓取公告詳細內容...")
    for ann in announcements:
        code = ann['公司代號']
        spoke_date8 = ann.get('發言日期', '').replace('-', '')
        key = (code, spoke_date8, ann.get('_spoke_time', ''))
        if key in onclick_params:
            seq_no, spoke_time, spoke_date = onclick_params[key]
            ann['_seq_no']     = seq_no
            ann['_spoke_time'] = spoke_time
            ann['_spoke_date'] = spoke_date
        if ann.get('_seq_no') and ann.get('_spoke_time') and ann.get('_spoke_date'):
            print(f"  抓詳細頁：{code} {ann['主旨'][:30]}")
            detail = fetch_detail(code, ann['_spoke_time'], ann['_spoke_date'], ann['_seq_no'])
            if detail:
                ann['說明'] = detail
                print(f"    說明長度：{len(detail)} 字")
            time.sleep(2)
        else:
            print(f"  {code} 無詳細頁參數（非 6AM 排程時正常），使用清單頁說明")

    # 篩選：說明含「每股盈餘」且符合條款為 51 或 53 款
    matched = [
        a for a in announcements
        if "每股盈餘" in a.get("說明", "")
        and ("51" in a.get("符合條款", "") or "53" in a.get("符合條款", ""))
    ]
    print(f"  符合條件：{len(matched)} 筆")

    if not matched:
        send_telegram(f"📭 今日（{now.strftime('%Y/%m/%d')}）沒有符合訊號的公告")
        return

    print("抓取股價...")
    prices = fetch_prices()

    for ann in matched:
        code = ann['公司代號']
        ann['公司名稱'] = ann['公司名稱'] or code
        price = prices.get(code, 0)
        print(f"\n處理 {code} {ann['公司名稱']}（股價 {price}）")

        pe = calc_pe(ann['說明'], price)
        dashboard = _get_dashboard_row(code)
        print(f"  預估本益比：{pe['pre_pe_note']}")

        print("  AI 分析中...")
        try:
            ai = analyze(ann, price, pe, dashboard)
        except Exception as e:
            print(f"  AI 失敗：{e}")
            ai = {"display_text": f"AI分析失敗：{e}", "ai_rating": "🟡 一般觀望"}

        header = (f"📢【{ann['公司名稱']}｜{code}】\n"
                  f"📅 {ann['發言日期']} {ann['發言時間']}\n"
                  f"📑 {ann['符合條款']}\n\n"
                  f"🤖 <b>AI 分析：</b>\n")
        send_telegram(header + ai.get("display_text", ""))
        print("  ✅ Telegram 送出")

        if ai.get('ai_rating', '') in ('🔴 強烈買進', '🟠 建議買進'):
            try:
                sync_gsheet(ann, ai, pe, price)
                print("  ✅ Google Sheet 同步")
            except Exception as e:
                print(f"  Google Sheet 失敗：{e}")
        else:
            print(f"  略過 Google Sheet（{ai.get('ai_rating')}）")

        time.sleep(30)

    print(f"\n完成！共處理 {len(matched)} 筆")

if __name__ == "__main__":
    main()
