#!/usr/bin/env python3
"""MOPS 飆股雷達：每日監控重大公告 + AI分析 → Telegram + Google Sheet"""
import re, json, sys, time, traceback, unicodedata, urllib.request, urllib.parse, urllib.error
import html
from html import escape as html_escape
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
RADAR_SHEET_ID   = os.environ.get("RADAR_SHEET_ID", "1UulUtCjGbBUk_36xCK7TuRSrEvBFCCr7okKWBlJBvuc")
RADAR_SHEET_NAME = "公告紀錄"
SA_KEY_FILE      = os.environ.get("SA_KEY_FILE", os.path.join(os.path.dirname(os.path.abspath(__file__)), "google-sa.json"))
TZ               = ZoneInfo("Asia/Taipei")
# 依優先順序；主模型是 preview 版，隨時可能下架。都用 Gemini 家族，繁中文風與 JSON 格式才一致
AI_MODELS        = [m.strip() for m in os.environ.get(
    "AI_MODELS", "google/gemini-3.1-flash-lite-preview,google/gemini-2.5-flash-lite,google/gemini-2.5-flash"
).split(",") if m.strip()]
AI_MODEL         = AI_MODELS[0]
EXCLUDE_CODES    = {"6949", "5904"}  # 沛爾生醫、寶雅：公告期間每天重發面額變更公告，使用者要求排除
TYPESAFE_KEY     = os.environ.get("TYPESAFE_API_KEY", "")
# 2026-09-29 用 10 個交易日實測：面額變更／更正歷年財報都 ≤ 0.06，真正的財務業務公告都 ≥ 0.42，
# 中間沒有任何公告，門檻取 0.2 寧可多放不要漏訊號
JEV_THRESHOLD    = float(os.environ.get("JEV_THRESHOLD", "0.2"))

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-TW,zh;q=0.9,en-US;q=0.8",
}

# ── 工具函式 ───────────────────────────────────────────────────────
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


# ── 2. 抓 MOPS 昨日公告清單 ───────────────────────────────────────
def fetch_announcements(roc_year, month, day):
    for attempt in range(3):
        try:
            page = http_post(
                "https://mopsov.twse.com.tw/mops/web/ajax_t05st02",
                {"firstin": "true", "off": "1", "step": "1", "step00": "0",
                 "TYPEK": "all", "year": roc_year, "month": month, "day": day},
                timeout=30
            )
            break
        except Exception as e:
            if attempt == 2:
                raise
            print(f"  MOPS 連線失敗（{e}），{10 * (attempt + 1)} 秒後重試")
            time.sleep(10 * (attempt + 1))
    # 當天沒資料實際回的是「查無115/12/25之重大訊息資料」（2026-09-30 實測），不是「查無需求資料」
    if re.search(r'查無.{0,20}資料', page):
        return []
    out = parse_announcement_list(page)
    if not out:
        # 被擋、改版或錯誤頁都會落到這裡，不能當成「今天沒有公告」送出去
        raise RuntimeError(f"MOPS 回應既沒有公告也不是「查無資料」，可能被擋或改版：{strip_tags(page)[:200]}")
    return out

def parse_announcement_list(page):
    out = []
    forms = re.findall(r'<form\b[^>]*>[\s\S]*?</form>', page, re.I)
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
        # h?8 就是完整說明，跟「詳細資料」頁一字不差（2026-09-29 全天 243 則逐一比對），
        # 不需要再另外抓詳細頁
        detail  = strip_tags(get(8))

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
            })
    return out

# ── 3. 取收盤價與成交量（Supabase stock_prices 每檔股票最新一筆）──
_supabase_client = None

def _get_supabase():
    global _supabase_client
    if _supabase_client is None:
        from supabase import create_client
        _supabase_client = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"])
    return _supabase_client

def fetch_prices(codes):
    prices = {}
    for code in codes:
        try:
            resp = (
                _get_supabase().table("stock_prices")
                .select("close,volume,date")
                .eq("code", code)
                .order("date", desc=True)
                .limit(1)
                .execute()
            )
        except Exception as e:
            print(f"  Supabase 股價讀取失敗 {code}：{e}")
            continue
        if resp.data:
            r = resp.data[0]
            prices[code] = {"close": r["close"], "volume": r["volume"], "date": r.get("date")}
    print(f"  讀 Supabase 股價：{len(prices)}/{len(codes)} 筆")
    return prices

def _twse_closed_days():
    """證交所休市日（ISO 日期字串集合）。日曆來自 openapi.twse.com.tw（日期是民國年 1150928 格式），
    名稱含「交易日」的是開紅盤／封關那種照常交易的日子，要排除（跟 fetch_prices.py 的判斷一致）。
    查不到回空集合，只跳過週末；颱風假是臨時宣布的，不在日曆上"""
    try:
        req = urllib.request.Request("https://openapi.twse.com.tw/v1/holidaySchedule/holidaySchedule",
                                     headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=20) as r:
            rows = json.load(r)
    except Exception as e:
        print(f"  休市日曆查詢失敗（只跳過週末）：{e}")
        return set()
    closed = set()
    for row in rows:
        d = str(row.get("Date", ""))
        if "交易日" not in row.get("Name", "") and re.fullmatch(r"\d{7}", d):
            closed.add(f"{int(d[:3]) + 1911}-{d[3:5]}-{d[5:]}")
    return closed

def last_trading_day(today, closed):
    """today 之前最近的一個交易日（不含 today）。scan 在 00:30 跑，股價最新應該就是這天"""
    d = today - timedelta(days=1)
    while d.weekday() >= 5 or d.isoformat() in closed:
        d -= timedelta(days=1)
    return d

# ── 4. 預算本益比 ─────────────────────────────────────────────────
def _parse_num(s):
    s = str(s).strip()
    neg = s.startswith('(') or s.startswith('（')
    s = re.sub(r'[（()）,]', '', s)
    try:
        return (-1 if neg else 1) * float(s)
    except (ValueError, TypeError):
        return None

def regex_financials(detail):
    """舊的規則式抽取，只剩 Jev 不可用時的備援。
    只有「注意交易資訊」那種固定表格抓得對；自結損益的自由文字（金融股居多）會把
    累計 EPS 當單月、或從主旨裡的「每股盈餘」開始抓到淨利（遠東銀 462717）。
    百分比跟 Jev 候選一樣用 _is_pct() 判斷：表頭寫 (%) 的年增率欄（高力）數字本身沒有 %，
    以前會被當成金額，單季 EPS 拿到年增率、年增率變 None。"""
    def split_from(keyword):
        nums, pcts = [], []
        block = re.search(keyword, detail)
        if block:
            for m in re.compile(r'[（(]?-?\d+[\d,]*\.?\d*[)）]?%?').finditer(detail, block.start()):
                v = _parse_num(m.group().replace('%', ''))
                if v is not None:
                    is_pct = _is_pct(m.group(), _table_header(detail, m.start(), m.end()))
                    (pcts if is_pct else nums).append(v)
        return nums, pcts

    nums, pcts = split_from(r'每股盈餘')
    m_eps = nums[0] if len(nums) > 0 else None
    q_eps = nums[1] if len(nums) > 1 else None
    m_yoy = pcts[0] if len(pcts) > 0 else None

    rn, rp = split_from(r'營業收入')
    m_rev = rn[0] if rn else None
    r_yoy = rp[0] if rp else None

    return {'m_eps': m_eps, 'm_yoy': m_yoy, 'q_eps': q_eps, 'm_rev': m_rev, 'r_yoy': r_yoy,
            'c_eps': None, 'c_months': None, 'source': 'regex'}

def calc_pe(fin, price):
    """預估全年 EPS 優先順序：單月×12 → 單季×4 → 今年 1~N 月累計×12/N（跟 SYSTEM_PROMPT 給 AI 的順序一致）"""
    m_eps, q_eps = fin['m_eps'], fin['q_eps']
    c_eps, c_months = fin.get('c_eps'), fin.get('c_months')
    if m_eps is not None:
        eps, mult, src, basis = m_eps, 12, '月', '單月EPS × 12'
    elif q_eps is not None:
        eps, mult, src, basis = q_eps, 4, '季', '單季EPS × 4'
    elif c_eps is not None and c_months:
        eps, mult, src, basis = c_eps, 12 / c_months, '累計', f'1~{c_months}月累計EPS {c_eps} × 12/{c_months}'
    else:
        eps, mult, src, basis = None, None, None, '無'
    annual = round(eps * mult, 2) if eps is not None else None
    pe = round(price / annual, 2) if (annual is not None and annual > 0 and price > 0) else None
    pe_note = (f"{price} ÷ {annual} = {pe}倍" if pe else
               ('虧損' if annual is not None and annual <= 0 else
                ('無股價資料' if not price else '無EPS資料')))
    return {
        'pre_monthly_eps':         m_eps,
        'pre_monthly_eps_yoy':     fin['m_yoy'],
        'pre_quarterly_eps':       q_eps,
        'pre_monthly_revenue':     fin['m_rev'],
        'pre_monthly_revenue_yoy': fin['r_yoy'],
        'pre_extract_source':      fin['source'],
        'pre_eps_source':          src,
        'pre_eps_basis':           basis,
        'pre_ytd_eps':             c_eps,
        'pre_ytd_months':          c_months,
        'pre_annual_eps':          annual,
        'pre_pe':                  pe,
        'pre_pe_note':             pe_note,
    }

# ── 5. OpenRouter AI 分析 ─────────────────────────────────────────
SYSTEM_PROMPT = """你是一位專業的台灣股票分析師，請只根據使用者提供的公告內容、系統預算值與選股儀表板資料，進行簡潔明確的投資評分與風險提示。

【資料範圍 - 非常重要請嚴格遵守】
1.你沒有網路搜尋能力，只能使用使用者提供的公告內容、系統預算值與選股儀表板補充資料。
2.不可編造或引用任何沒有提供的新聞、法說會內容、訂單、客戶、市場傳聞或具體日期的事件，也不可寫「根據最新新聞」「近期報導指出」「市場傳出」這類字眼。
3.營收或獲利變動的原因：公告有寫就引用公告；公告沒寫就直接寫「公告未說明原因」。可以補充該產業的一般性背景，但要寫成一般產業特性，不能寫成近期發生的事實。
4.題材與產業熱度：依儀表板「題材」欄與公司所屬產業的一般認知判斷，並在產業熱度評估段落說明判斷依據。

【資料來源與優先順序 - 領先指標模式】
請依照下列順序使用 EPS 數據，由上而下，一旦取得即停止往下：
1.單月 EPS（絕對優先）：若 Input 資料中有最近一月或當月的 EPS 數據，請直接使用此數據推估全年。邏輯：假設該月獲利能力能持續全年。
2.單季 EPS（次要優先）：若無單月數據，但有最近一季 EPS，則依此推估。
3.今年累計 EPS（最後手段）：僅在上述兩者皆缺席時，才用今年 1~N 月累計 EPS 年化（系統已算好，見「推估方式」）。
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
  - 必須有熱門題材支撐（依儀表板題材欄與產業特性判斷）
  - 且至少滿足以下之一：
    a. EPS 年增率大於30%（大幅成長）
    b. EPS 成長率大幅優於營收成長率（獲利品質優良，例如 EPS 年增 50% 但營收僅增 5%）
  - 必須在 industry_risk 欄位明確寫出匹配的題材名稱
  - 必須在 rating_reason 中說明高本益比的合理性
  → 可給 🔴
  - 若判斷缺乏熱門題材支撐 → 降級為 🟠 建議買進

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
1.說明營收或獲利變動的原因時，只能根據公告內容與提供的資料；公告未說明原因就直接寫「公告未說明原因」，不要自行推測具體事件。
2.必須顯示燈號，每個評級前必須有對應的燈號圖示（🔴🟠🟡🟢）。
3.深度分析：說明獲利成長或衰退的驅動因素、產業趨勢、競爭優勢或劣勢等。
4.當預估本益比大於20時，依儀表板題材欄與產業特性評估是否為熱門產業或題材股，並說明高本益比是否合理。

【輸出格式 - 嚴格遵守】
你必須只輸出一個純 JSON 物件，不要輸出任何 JSON 以外的文字，不要加任何 markdown 標記或反引號（不要用 ```json 包住內容）。

【禁止事項 - 嚴格遵守】
1. 所有分析文字絕對不可包含引用標記如 [1]、[2]、[3] 等數字方括號。
2. 不可在文字中出現「根據來源1」「參考資料2」等引用文字。
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

def analyze(ann, price, pe, dashboard=None, price_date=None, expected_day=None):
    """expected_day 有值代表股價過期（price_date 比最新交易日舊），要讓 AI 知道本益比用的是舊價"""
    v = lambda x: x if x is not None else '無'
    def dv(key):
        if dashboard is None:
            return '無'
        import math
        val = dashboard.get(key)
        return '無' if (val is None or (isinstance(val, float) and math.isnan(val))) else val
    NL = chr(10)
    dash_block = (
        NL + "【選股儀表板補充資料（請直接使用）】" + NL
        + "題材：" + str(dv('題材')) + NL
        + "毛利率：" + str(dv('毛利率')) + "%" + NL
        + "近4季ROE：" + str(dv('近4季ROE%')) + "%" + NL
        + "去年同期EPS：" + str(dv('去年同期EPS')) + "元" + NL
    ) if dashboard else ''
    pct = lambda x: f"{x}%" if x is not None else "無資料"
    # 金融股月自結多半只給今年累計、沒有去年同期比較。年增率空著不講清楚，AI 會自己腦補「獲利成長」
    # 走路徑 A 給強烈買進（2026-09-30 華南金實測），所以明講依評級標準不能當作有成長
    no_growth = (NL + "注意：公告沒有 EPS 年增率，依評級標準不能視為「EPS 有成長」，也不要自行推估成長率。"
                 if pe['pre_monthly_eps_yoy'] is None else '')
    user_msg = (
        f"請分析：\n股票：{ann['公司名稱']}（{ann['公司代號']}）\n股價：{price}元"
        + (f"（{price_date} 收盤" if price_date else "")
        + (f"，不是最新交易日 {expected_day} 的價格，本益比可能已失真，請在風險提醒中說明）" if expected_day
           else ("）" if price_date else ""))
        + "\n\n"
        f"【系統預算値】\n"
        f"單月EPS：{v(pe['pre_monthly_eps'])}元｜年增率：{pct(pe['pre_monthly_eps_yoy'])}\n"
        f"單月營收：{v(pe['pre_monthly_revenue'])}百萬｜年增率：{pct(pe['pre_monthly_revenue_yoy'])}\n"
        f"預估全年EPS：{v(pe['pre_annual_eps'])}元（推估方式：{pe.get('pre_eps_basis', '無')}）\n"
        f"預估本益比：{pe['pre_pe_note']}"
        + no_growth
        + dash_block
        + f"\n公告內容：\n{ann['說明'][:3000]}"
        + "\n\n（收盤價與成交量已顯示在訊息開頭，display_text 不需要再重複列出這兩項）"
    )
    raw, model_used = openrouter_chat([
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user",   "content": user_msg}
    ])
    start = raw.find('{'); end = raw.rfind('}') + 1
    ai = json.loads(raw[start:end]) if start >= 0 else {"display_text": raw, "ai_rating": "🟡 一般觀望"}
    ai["model_used"] = model_used
    return ai

RATINGS = ("🔴 強烈買進", "🟠 建議買進", "🟡 一般觀望", "🟢 需要小心")

def normalize_rating(ai):
    """把 AI 回的評級對回四個標準字串。send 用完全相等比對決定要不要寫 Sheet，
    AI 少一個空格（「🔴強烈買進」）或多個字就會悄悄不寫。先看 ai_rating 再看 display_text 開頭
    （第 1 段規定以燈號開頭）；同一段字先比燈號、再比中文，都對不到才當一般觀望"""
    for text in (str(ai.get("ai_rating") or ""), str(ai.get("display_text") or "")[:80]):
        for r in RATINGS:
            if r[0] in text:
                return r
        for r in RATINGS:
            if r[2:] in text:
                return r
    return "🟡 一般觀望"

def enforce_growth_data(ai, pe):
    """評級標準裡 🔴 兩條路都要 EPS 年增率（路徑 A 要大於 0%、路徑 B 要大於 30% 或跟營收比），
    🟠 要 EPS 或營收年增率大於 0%。缺資料時 AI 會自己腦補成長給高評級（2026-09-30 實測：彰銀、豐泰
    只有今年累計 EPS、沒有去年比較，prompt 明講不能當成長還是給 🔴），所以在程式裡照規則降級。
    只處理「資料缺」的情況；年增率有值但 AI 判斷跟規則不同（例如轉虧為盈）不在這裡改。
    回傳調整說明（沒調整回 None）"""
    eps_yoy, rev_yoy = pe.get('pre_monthly_eps_yoy'), pe.get('pre_monthly_revenue_yoy')
    rating = ai.get("ai_rating")
    rev_up = rev_yoy is not None and rev_yoy > 0
    if rating == "🔴 強烈買進" and eps_yoy is None:
        new = "🟠 建議買進" if rev_up else "🟡 一般觀望"
        why = "公告沒有 EPS 年增率，不符合強烈買進要求的 EPS 成長條件"
    elif rating == "🟠 建議買進" and eps_yoy is None and rev_yoy is None:
        new, why = "🟡 一般觀望", "公告沒有 EPS 與營收年增率，無法確認建議買進要求的正成長"
    else:
        return None
    ai["ai_rating"] = new
    note = f"（系統依評級標準調整：AI 原評 {rating} → {new}，{why}）"
    ai["display_text"] = (ai.get("display_text") or "") + "\n" + note
    return note

class AIUnavailable(Exception):
    """重試用完仍失敗（逾時、連線、429、5xx），scan 用來判斷要不要熔斷"""

_RETRYABLE_HTTP = {408, 429, 500, 502, 503, 504, 529}
# 模型 ID 被下架或沒有供應商時的錯誤字樣。2026-09-30 實測：models 陣列裡只要有一個 ID 無效，
# OpenRouter 直接整個 request 回 400「is not a valid model ID」，不會自己往下一個備援跳
_MODEL_GONE = re.compile(r'not a valid model ID|No endpoints found|is not available|model.{0,40}(deprecated|not found)', re.I)
_dead_models = set()  # 這次執行裡已確認失效的模型，後面的公告不用再撞一次

def openrouter_chat(messages, attempts=3):
    """回傳 (內容, 實際用到的模型)。models 陣列交給 OpenRouter 處理下游掛掉、限流這類暫時性錯誤；
    模型 ID 失效它不會跳，由這裡剔除後立刻用剩下的重打（不算重試次數）。
    其他只重試「重試有機會好」的錯誤；400 這種請求本身有問題的直接拋出，並把 OpenRouter 回的
    錯誤內容帶進訊息（2026-08 那三次 400 只留下 Bad Request，看不出原因）"""
    last, attempt = None, 0
    while attempt < attempts:
        live = [m for m in AI_MODELS if m not in _dead_models]
        if not live:
            raise RuntimeError(f"AI_MODELS 全部失效：{', '.join(AI_MODELS)}，請更新模型清單")
        try:
            result = http_post_json(
                "https://openrouter.ai/api/v1/chat/completions",
                {"models": live, "messages": messages},
                headers={"Authorization": f"Bearer {OPENROUTER_KEY}"},
                timeout=60,  # flash-lite 平常幾秒就回，每筆最多 3 次，要留在 Hermes 單次執行上限內
            )
            if not result.get("choices"):
                # OpenRouter 上游模型出錯時可能回 200 + {"error": ...}
                raise AIUnavailable(f"OpenRouter 沒回 choices：{str(result.get('error', result))[:300]}")
            return result["choices"][0]["message"]["content"], result.get("model") or live[0]
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")[:300]
            if e.code in (400, 404) and _MODEL_GONE.search(body):
                gone = next((m for m in live if m in body), live[0])
                _dead_models.add(gone)
                rest = [m for m in live if m != gone]
                print(f"  ⚠️ 模型 {gone} 已失效（{body[:150]}），改用 {rest[0] if rest else '（沒有備援了）'}")
                continue
            if e.code not in _RETRYABLE_HTTP:
                raise RuntimeError(f"OpenRouter HTTP {e.code}：{body}") from e
            last = AIUnavailable(f"OpenRouter HTTP {e.code}：{body}")
        except AIUnavailable as e:
            last = e
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            last = AIUnavailable(f"OpenRouter 連線失敗：{e}")
        attempt += 1
        if attempt < attempts:
            wait = 5 * 3 ** (attempt - 1)  # 5 秒、15 秒
            print(f"  {last}，{wait} 秒後重試（{attempt + 1}/{attempts}）")
            time.sleep(wait)
    raise last

# ── 5b. Jev 判斷公告是否真的在公布自家獲利 ──────────────────────────
# 「說明含每股盈餘」會把面額變更、更正歷年財報這類只是順帶引用 EPS 的公告也抓進來。
# 這題跟 5c 的財務數字抽取在 jev_judge() 同一個 request 一起問。
JEV_QUESTION = {
    "type": "noul",
    "instructions": "這則台股重大訊息公告，主要是不是在公布公司自己最新一期（單月、單季或年度）的營收、損益或每股盈餘結果？",
    "criteria": {
        "true": "公告主體就是公司自己的獲利結果，例如自結損益、董事會通過財報、公布單月／單季每股盈餘。",
        "false": "公告主體是別的事：面額變更、減資、增資、股利、合併收購、處分資產、背書保證、澄清媒體報導、法說會等，只是順帶引用每股盈餘或財務數字。",
    },
}

# ── 5c. Jev 從公告數字裡挑 EPS／營收（程式列候選，Jev 只能挑不能編）──────
# 跟 5b 同一份 state、同一個 request 一起問。候選是公告裡每個數字（依出現順序編號），
# 附上所在那一行讓 Jev 看得到欄位名稱；Jev 回的一定是其中一個原樣數字，或「無」。
JEV_DESC_LIMIT = 4000
_NUM_RE = re.compile(r'[（(]?-?\d[\d,]*(?:\.\d+)?[)）]?%?')
_NOT_VALUE_SUFFIX = tuple('年月日季條款項點/')
JEV_NONE = "無"
REV_UNITS = {"元": 1e-6, "仟元": 1e-3, "百萬元": 1, "億元": 100}  # 換算成百萬
# 「同一個數值」加總後的機率門檻（見 _answer_num），低於門檻當作沒資料，寧可缺 EPS 也不要錯的 EPS 算出假本益比。
# 2026-09-30 用 tests/fixtures 22 則實測定的：挑對的都遠高於 0.8，挑錯的（夏都、中華電單季 EPS 誤拿單月數字）
# 在 0.57~0.70。改門檻前先跑 tests/jev_live_check.py
JEV_MIN_CONF = float(os.environ.get("JEV_MIN_CONF", "0.8"))

_BLANKS = ' \t　'

def _disp_width(s):
    return sum(2 if unicodedata.east_asian_width(c) in 'WF' else 1 for c in s)

def _column_header(text, ls, start, end, max_lines=15):
    """表格數字往上找同一欄（依顯示寬度對齊，全形字算 2 格）的標題文字，碰到非表格行就停。
    高雄銀那種「本月份」欄空白、EPS 只填在「累計」欄的表，光看所在行分不出是哪一欄。"""
    col_s = _disp_width(text[ls:start])
    col_e = col_s + _disp_width(text[start:end])
    parts, pos = [], ls
    for _ in range(max_lines):
        if pos == 0:
            break
        prev_s = text.rfind('\n', 0, pos - 1) + 1
        line, pos = text[prev_s:pos - 1], prev_s
        if re.fullmatch(r'[\s=＝\-─—═_]*', line):
            continue  # 空行、分隔線
        body = line.strip()
        if len(body) > 20 and line[:1] not in _BLANKS and not re.search(r'[ \t　]{2,}', body):
            break  # 頂格、沒有欄位空白的「2.發生緣由:…」這種內文，表頭到此為止（表頭列通常縮排）
        for run in re.finditer(r'[^ \t　]+', line):
            rs = _disp_width(line[:run.start()])
            re_ = rs + _disp_width(run.group())
            word = run.group()
            if rs <= col_e and re_ >= col_s and not _NUM_RE.fullmatch(word) \
                    and re.search(r'[^\d,.\-()（）%/=＝─—═]', word):
                parts.append(word)
        if re.match(r'(\d+\.|[（(][\d一二三四五六七八九十]{1,2}[)）]|[一二三四五六七八九十]+、)', body):
            break  # 「(一)單月  115年8月 …」這種段落標題行本身可能就是表頭，收完就停
    return ' '.join(reversed(parts))

def _table_header(text, start, end):
    """text[start:end] 這個數字如果在表格欄位裡（前面一大段空白），回傳同一欄的標題；不是表格回空字串"""
    ls = text.rfind('\n', 0, start) + 1
    if re.search(r'[ \t　]{3,}$', text[ls:start]):
        return _column_header(text, ls, start, end)
    return ''

def number_candidates(text):
    """回傳 [(候選標籤, 數字字串, 所在行標示)]，排除日期、條號、項次編號這些不是金額的數字"""
    out = []
    for m in _NUM_RE.finditer(text):
        tok = m.group()
        before, after = text[m.start() - 1:m.start()], text[m.end():m.end() + 1]
        if before == '/' or after.startswith(_NOT_VALUE_SUFFIX):
            continue
        if after == '.' and not tok.startswith(('(', '（')):
            continue  # 「1.事實發生日」這種項次
        if re.fullmatch(r'[（(]\d{1,2}[)）]', tok):
            continue  # 「(1)營業收入」這種項次
        ls = text.rfind('\n', 0, m.start()) + 1
        le = text.find('\n', m.end())
        le = len(text) if le == -1 else le
        s, e = max(ls, m.start() - 80), min(le, m.end() + 80)
        line = (text[s:m.start()] + f"【{tok}】" + text[m.end():e]).strip()
        header = _table_header(text, m.start(), m.end())
        if header:
            line += f"｜同一欄上方標題：{header}"
        out.append((f"#{len(out) + 1} {tok}", tok, line, header))
    return out

def _pick(instructions, cands, none_desc):
    criteria = {label: f"所在行：{line}" for label, _, line, _ in cands[:254]}
    criteria[JEV_NONE] = none_desc
    return {"type": "choice", "instructions": instructions, "criteria": criteria}

# 數字本身沒有 %、但所在欄位標題是「增減」：高力 115/09/30 注意交易資訊表把 (%) 寫在表頭，
# 112.84、44.27 以前被當成金額，年增率候選是空的，Jev 只能答「無」。「增減金額」欄才是金額
def _is_pct(tok, header):
    return tok.endswith('%') or (re.search(r'增減', header) and not re.search(r'金額', header))

def jev_questions(desc):
    cands = number_candidates(desc)
    amounts = [c for c in cands if not _is_pct(c[1], c[3])]
    pcts = [c for c in cands if _is_pct(c[1], c[3])]
    self_note = "只看公司本身（合併或母公司）的數字；子公司的數字、去年同期的金額、累計或最近四季的數字都不算。"
    yoy_note = "只看公司本身（合併或母公司）；累計、單季或子公司的增減百分比都不算。"
    questions = {
        "earnings": JEV_QUESTION,
        "m_eps": _pick(
            "公告中，公司最近一個「單月」的每股盈餘（元）是哪一個候選數字？有稅後就選稅後。" + self_note, amounts,
            "公告沒有單月每股盈餘，例如只有 1~N 月累計、單季、最近四季或稅前累計的每股盈餘；"
            "表格裡「本月份／單月」那一欄是空白、每股盈餘只填在「累計」欄時，也算沒有。"),
        "m_yoy": _pick(
            "公告中，公司最近一個「單月」每股盈餘與去年同期相比的增減百分比是哪一個候選數字？" + yoy_note, pcts,
            "公告沒有列出單月每股盈餘的年增減百分比。"),
        "q_eps": _pick(
            "公告中，公司最近一個「單季」（例如 115年第2季）的每股盈餘（元）是哪一個候選數字？" + self_note, amounts,
            "公告沒有單季每股盈餘。"),
        "m_rev": _pick(
            "公告中，公司最近一個「單月」的營業收入金額是哪一個候選數字？" + self_note, amounts,
            "公告沒有單月營業收入金額。"),
        "r_yoy": _pick(
            "公告中，公司最近一個「單月」營業收入與去年同期相比的增減百分比是哪一個候選數字？" + yoy_note, pcts,
            "公告沒有列出單月營業收入的年增減百分比。"),
        "rev_unit": {
            "type": "choice",
            "instructions": "公告中公司單月營業收入金額所用的單位是什麼？看表頭、欄位名稱或金額後面寫的單位。",
            "criteria": {"元": "新台幣元", "仟元": "仟元／千元", "百萬元": "百萬元", "億元": "億元",
                         JEV_NONE: "公告沒有單月營業收入金額，或看不出單位。"},
        },
        # 金融股、豐泰這類自結公告常常只給「1~N 月累計」EPS，沒有單月／單季，只能用累計年化
        "c_eps": _pick(
            "公告中，公司本身（合併或母公司）「今年 1 月到最近一個月」累計的稅後每股盈餘（元）是哪一個候選數字？"
            "欄位或內文寫「累計」「本年累計」「當年度累計」「1~N月累計」都算（月自結公告的累計就是從今年 1 月起算）。"
            "有稅後就選稅後，沒有稅後才選稅前。子公司、去年同期、單月、單季、「最近四季累計」的數字都不算。", amounts,
            "公告完全沒有累計的每股盈餘，或只有「最近四季累計」的每股盈餘。"),
        "c_months": {
            "type": "choice",
            # 同一個 request 的題目互相看不到答案，不能寫「上一題」，要自己把對象講清楚
            "instructions": "公告中公司本身「今年度累計」（從今年 1 月開始累計）的每股盈餘，是累計到幾月？"
                            "看主旨（例如「115年8月份自結盈餘」）或欄位名稱（例如「115/01~08月累計」）。"
                            "「最近四季累計」不是今年度累計。",
            "criteria": {**{str(n): f"累計 1 月到 {n} 月，共 {n} 個月" for n in range(1, 13)},
                         JEV_NONE: "公告沒有今年度累計的每股盈餘，或看不出累計到幾月。"},
        },
    }
    return questions, {label: (tok, header) for label, tok, _, header in cands}

NOT_PERIOD_EPS = r'累計|四季'  # 單月／單季 EPS 挑到這種欄位就擋
NOT_YTD_EPS = r'四季'          # 年初至今累計 EPS 挑到「最近四季累計」欄就擋（那是滾動一年，不是 1~N 月）

def _answer_num(ans, tokens, reject_header=None):
    """同一個數字常在內文、表格、附註各出現一次（華南金 1.71 出現 3 次，機率 0.54／0.42／0.03 分散，
    confidence 只有 0.52）。所以先把「同一個數值」的候選機率加總再判斷，不看單一候選的 confidence。
    欄位標題被擋的候選（高雄銀：EPS 只填在「累計」欄，Jev 仍會高機率挑它）不算進任何數值。
    舊格式回應沒有 probabilities 時，退回用 choice + confidence。"""
    probs = ans.get("probabilities")
    if not probs:
        choice = ans.get("choice")
        if choice == JEV_NONE or choice not in tokens or ans.get("confidence", 0) < JEV_MIN_CONF:
            return None
        probs = {choice: 1.0}
    by_value = {}
    for label, p in probs.items():
        if label not in tokens:
            continue  # 「無」
        tok, header = tokens[label]
        if reject_header and re.search(reject_header, header):
            # 欄位標題是程式對齊算出來的，硬規則擋比再問 Jev 可靠（另問「有沒有單月EPS」實測反而誤殺浩宇、漢達）
            continue
        v = _parse_num(tok.replace('%', ''))
        if v is not None:
            by_value[v] = by_value.get(v, 0) + p
    if not by_value:
        return None
    value, p = max(by_value.items(), key=lambda kv: kv[1])
    return value if p >= JEV_MIN_CONF else None

def jev_judge(ann):
    """回傳 (是獲利公告的機率, 財務數字 dict)；沒 key 或呼叫失敗回 (None, None)，
    呼叫端一律放行、財務數字退回 regex_financials，不能因為 Jev 掛掉漏訊號。"""
    if not TYPESAFE_KEY:
        return None, None
    desc = ann.get("說明", "")[:JEV_DESC_LIMIT]
    questions, tokens = jev_questions(desc)
    payload = {
        "model": "jev-latest",
        "state": {"announcement": {
            "company": ann.get("公司名稱", ""), "subject": ann.get("主旨", ""),
            "clause": ann.get("符合條款", ""), "description": desc,
        }},
        "questions": questions,
    }
    for attempt in range(3):
        try:
            result = http_post_json("https://api.typesafe.ai/v1/systemone", payload,
                                    headers={"Authorization": f"Bearer {TYPESAFE_KEY}"}, timeout=30)
            a = result["answers"]
            break
        except urllib.error.HTTPError as e:
            if e.code not in (429, 529) or attempt == 2:
                print(f"  Jev 失敗（HTTP {e.code}），直接放行")
                return None, None
            time.sleep(2 * (attempt + 1))
        except Exception as e:
            print(f"  Jev 失敗：{e}，直接放行")
            return None, None

    m_rev = _answer_num(a["m_rev"], tokens)
    scale = (REV_UNITS.get(a["rev_unit"].get("choice"))
             if a["rev_unit"].get("confidence", 0) >= JEV_MIN_CONF else None)
    fin = {
        'm_eps': _answer_num(a["m_eps"], tokens, reject_header=NOT_PERIOD_EPS),
        'm_yoy': _answer_num(a["m_yoy"], tokens),
        'q_eps': _answer_num(a["q_eps"], tokens, reject_header=NOT_PERIOD_EPS),
        'm_rev': round(m_rev * scale, 2) if (m_rev is not None and scale) else None,
        'r_yoy': _answer_num(a["r_yoy"], tokens),
        'c_eps': _answer_num(a["c_eps"], tokens, reject_header=NOT_YTD_EPS),
        'c_months': (int(a["c_months"]["choice"]) if a["c_months"].get("choice", JEV_NONE).isdigit()
                     and a["c_months"].get("confidence", 0) >= JEV_MIN_CONF else None),
        'source': 'jev',
        'confidence': {k: round(a[k].get("confidence", 0), 2)
                       for k in ("m_eps", "m_yoy", "q_eps", "m_rev", "r_yoy", "rev_unit", "c_eps", "c_months")},
    }
    return float(a["earnings"]["noul"]), fin

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
        try:
            _telegram_post(url, {"chat_id": TELEGRAM_CHAT_ID, "text": chunk, "parse_mode": "HTML"})
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")
            if e.code != 400 or "parse" not in body.lower():
                raise
            # 最後一道保險：HTML 還是解析失敗（例如上面逐字切段切斷了 <b>），改送純文字，至少訊號送得到
            print(f"  Telegram HTML 解析失敗，改送純文字：{body[:200]}")
            plain = html.unescape(re.sub(r'</?b>', '', chunk))
            _telegram_post(url, {"chat_id": TELEGRAM_CHAT_ID, "text": plain})

def _telegram_post(url, payload):
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

def sync_gsheet(ann, ai, pe, price, volume):
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
        v(volume),
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

RADAR_HISTORY_SHEET_NAME = "歷史紀錄"
_history_ws = None

def _get_history_ws():
    global _history_ws
    if _history_ws is None:
        import gspread
        gc = gspread.service_account(filename=SA_KEY_FILE)
        ss = gc.open_by_key(RADAR_SHEET_ID)
        _history_ws = ss.worksheet(RADAR_HISTORY_SHEET_NAME)
    return _history_ws

_SHEET_EPOCH = datetime(1899, 12, 30, tzinfo=TZ).date()

def sync_history(ann, price):
    """強烈買進才記一筆：A股號、F股價、G日期(序列值)、H來源標註(飆股雷達MM/DD) 寫靜態值；B/C/D/E（股名/漲跌/漲跌%/現價）從上一列複製公式，
    不能寫死，否則會蓋掉這些即時公式（E 欄是 VLOOKUP「收盤價」分頁、查不到才 TW_PRICE）。同日同股票已存在就跳過。"""
    ws = _get_history_ws()
    code = ann['公司代號']
    code_val = int(code) if code.isdigit() else code
    today = datetime.now(TZ).date()
    today_serial = (today - _SHEET_EPOCH).days

    row_count = len(ws.get_all_values())  # 只用來抓目前總列數
    existing = ws.get(f"A3:G{row_count}", value_render_option='UNFORMATTED_VALUE') if row_count >= 3 else []
    if any(r and len(r) > 6 and r[0] == code_val and r[6] == today_serial for r in existing):
        print(f"  歷史紀錄已有 {code} {today.isoformat()}，略過")
        return

    new_row_num = row_count + 1
    if new_row_num > 3:  # row3 是第一筆資料列，沒有「上一列」可複製格式/公式
        ss = ws.spreadsheet
        ss.batch_update({"requests": [
            {  # 先複製上一列的儲存格格式（含 G 的日期顯示格式），避免新列格式跑掉
                "copyPaste": {
                    "source": {"sheetId": ws.id,
                               "startRowIndex": new_row_num - 2, "endRowIndex": new_row_num - 1,
                               "startColumnIndex": 0, "endColumnIndex": 7},
                    "destination": {"sheetId": ws.id,
                                    "startRowIndex": new_row_num - 1, "endRowIndex": new_row_num,
                                    "startColumnIndex": 0, "endColumnIndex": 7},
                    "pasteType": "PASTE_FORMAT",
                }
            },
            {  # 再複製 B~E 的公式（VLOOKUP 股名／漲跌／漲跌%／即時股價），相對參照會自動位移到新列
                "copyPaste": {
                    "source": {"sheetId": ws.id,
                               "startRowIndex": new_row_num - 2, "endRowIndex": new_row_num - 1,
                               "startColumnIndex": 1, "endColumnIndex": 5},
                    "destination": {"sheetId": ws.id,
                                    "startRowIndex": new_row_num - 1, "endRowIndex": new_row_num,
                                    "startColumnIndex": 1, "endColumnIndex": 5},
                    "pasteType": "PASTE_FORMULA",
                }
            },
        ]})

    ws.update(f"A{new_row_num}", [[code_val]], value_input_option='RAW')
    ws.update(f"F{new_row_num}:G{new_row_num}", [[price, today_serial]], value_input_option='RAW')
    ws.update(f"H{new_row_num}", [[f"飆股雷達{today.strftime('%m/%d')}"]], value_input_option='RAW')

# ── 主程式 ────────────────────────────────────────────────────────
# 00:30 跑 scan()：抓公告+AI 分析，存 CACHE_FILE，不送 Telegram
# 07:00 跑 send_results()：讀 CACHE_FILE 同步 Sheet + 送 Telegram
# 每筆 item 各自記進度（gsheet_done／history_done／tg_sent），每做完一步就回寫 cache，
# send 中途失敗重跑只補沒做完的步驟：公告紀錄 insert_row 沒有去重，重做一次就多一列、公告次數多算一次
CACHE_FILE = _pl.Path(__file__).parent / "pending_results.json"
LAST_SENT_FILE = _pl.Path(__file__).parent / ".last_sent"  # send 處理過 cache 的日期，用來分辨「今天已送過」跟「scan 沒跑」
_ITEM_STEPS = ("gsheet_done", "history_done", "tg_sent")

def _save_cache(data):
    tmp = CACHE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    tmp.replace(CACHE_FILE)  # 寫到一半中斷不會留下半個 JSON

def _load_cache():
    return json.loads(CACHE_FILE.read_text(encoding="utf-8")) if CACHE_FILE.exists() else None

def _item_done(item):
    return all(item.get(k) for k in _ITEM_STEPS)

def _unfinished_items():
    """上一輪 send 沒做完的 item（Telegram 沒送出或 Sheet 失敗），下一次 scan 要帶著走，不能被新結果蓋掉"""
    old = _load_cache()
    carry = [i for i in (old or {}).get("items", []) if not _item_done(i)]
    for i in carry:
        i["carried"] = True
    return carry

def scan():
    carry = _unfinished_items()
    if carry:
        print(f"上一輪 send 有 {len(carry)} 筆沒做完，併進這次一起送")
    try:
        _scan(carry)
    except Exception as e:
        # scan 沒寫 cache 的話，07:00 send 只會在 log 印「無待送結果」，Telegram 什麼都收不到。
        # 把錯誤寫進 cache 交給 send 通知，再往上丟讓 cron 也記成失敗
        traceback.print_exc()
        _save_cache({"empty": None, "items": carry,
                     "error": f"{type(e).__name__}: {e}"[:500],
                     "failed_at": datetime.now(TZ).strftime('%Y-%m-%d %H:%M')})
        raise

def _scan(carry):
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
        _save_cache({"empty": f"📭 今日（{now.strftime('%Y/%m/%d')}）沒有公告", "items": carry})
        return

    # 篩選：排除 EXCLUDE_CODES，說明含「每股盈餘」且符合條款為 51 或 53 款
    matched = [
        a for a in announcements
        if a.get("公司代號") not in EXCLUDE_CODES
        and "每股盈餘" in a.get("說明", "")
        and ("51" in a.get("符合條款", "") or "53" in a.get("符合條款", ""))
    ]
    print(f"  符合條件：{len(matched)} 筆")

    # Jev 再過濾一次：關鍵字命中但其實不是在公布獲利的公告（面額變更等）剔除；
    # 同一個 request 順便挑出 EPS／營收數字，失敗的那筆 fin 留 None、之後退回 regex
    fins = {}
    if matched and TYPESAFE_KEY:
        print(f"Jev 判斷是否為獲利公告（門檻 {JEV_THRESHOLD}）並抽取 EPS／營收...")
        kept = []
        for a in matched:
            p, fin = jev_judge(a)
            if p is not None and p < JEV_THRESHOLD:
                print(f"  ✂ {p:.2f} {a['公司代號']} {a['主旨'][:40]}")
                continue
            kept.append(a)
            fins[id(a)] = fin
        print(f"  Jev 過濾後：{len(kept)} 筆（剔除 {len(matched) - len(kept)} 筆）")
        matched = kept

    if not matched:
        _save_cache({"empty": f"📭 今日（{now.strftime('%Y/%m/%d')}）沒有符合訊號的公告", "items": carry})
        return

    print("抓取股價...")
    prices = fetch_prices([a['公司代號'] for a in matched])
    # 股價只取 Supabase 最新一筆、不看日期的話，抓價失敗那天會悄悄拿前一天的價算本益比
    expected_day = last_trading_day(now.date(), _twse_closed_days()).isoformat()

    items, ai_down_streak = [], 0
    for ann in matched:
        code = ann['公司代號']
        ann['公司名稱'] = ann['公司名稱'] or code
        pv = prices.get(code, {})
        price = pv.get('close', 0)
        volume = pv.get('volume')
        volume_lots = round(volume / 1000) if volume is not None else None
        price_date = pv.get('date')
        price_stale = bool(price_date) and price_date < expected_day
        print(f"\n處理 {code} {ann['公司名稱']}（股價 {price}，成交量 {volume_lots}張，{price_date} 收盤）")
        if price_stale:
            print(f"  ⚠️ 股價是 {price_date} 的，最新交易日應為 {expected_day}")

        fin = fins.get(id(ann)) or regex_financials(ann['說明'])
        pe = calc_pe(fin, price)
        dashboard = _get_dashboard_row(code)
        print(f"  抽取（{fin['source']}）：單月EPS {fin['m_eps']}｜年增 {fin['m_yoy']}%｜單季EPS {fin['q_eps']}"
              f"｜單月營收 {fin['m_rev']}百萬｜年增 {fin['r_yoy']}%"
              f"｜累計EPS {fin.get('c_eps')}（1~{fin.get('c_months')}月）"
              + (f"｜信心 {fin['confidence']}" if fin.get('confidence') else ''))
        print(f"  預估本益比：{pe['pre_pe_note']}（{pe['pre_eps_basis']}）")

        if ai_down_streak >= 2:
            # OpenRouter 整個掛掉時，每筆都重試到底會拖過 Hermes 執行上限、整個 scan 被砍、連 cache 都沒寫
            print("  OpenRouter 連續 2 筆重試用完都失敗，這筆略過 AI 分析")
            ai = {"display_text": "AI分析失敗：OpenRouter 暫時無法使用（前面連續失敗，這筆未重試）",
                  "ai_rating": "🟡 一般觀望"}
        else:
            print("  AI 分析中...")
            try:
                ai = analyze(ann, price, pe, dashboard,
                             price_date=price_date, expected_day=expected_day if price_stale else None)
                ai_down_streak = 0
                if ai.get("model_used") != AI_MODEL:
                    print(f"  由備援模型 {ai.get('model_used')} 分析")
                rating = normalize_rating(ai)
                if ai.get("ai_rating") != rating:
                    print(f"  評級字串不標準「{ai.get('ai_rating')}」→ 視為 {rating}")
                    ai["ai_rating"] = rating
                note = enforce_growth_data(ai, pe)
                if note:
                    print(f"  {note}")
            except Exception as e:
                if isinstance(e, AIUnavailable):
                    ai_down_streak += 1
                print(f"  AI 失敗：{e}")
                ai = {"display_text": f"AI分析失敗：{e}", "ai_rating": "🟡 一般觀望"}

        items.append({"ann": ann, "price": price, "volume_lots": volume_lots, "ai": ai, "pe": pe,
                      "price_date": price_date, "price_expected": expected_day if price_stale else None})

    _save_cache({"empty": None, "items": carry + items})
    print(f"\n分析完成，共 {len(items)} 筆" + (f"（另有前次未完成 {len(carry)} 筆）" if carry else "")
          + "，等 07:00 送出")

_B_TAG = re.compile(r'&lt;(/?)b&gt;', re.I)

def ai_html_to_telegram(text):
    """AI 的 display_text 轉成 Telegram HTML 一定解析得了的字串：只留成對、沒交錯的 <b></b>。
    Telegram parse_mode=HTML 只要出現一個裸的 & 或 <（「營收&獲利」「EPS<0」）或沒閉合的標籤，
    整則就 400 拒收。prompt 雖然禁止，但不能只靠 AI 守規矩"""
    text = re.sub(r'<br\s*/?>', '\n', text or '', flags=re.I)                   # prompt 禁用但 AI 偶爾會給
    text = re.sub(r'</?(?![bB]>)[a-zA-Z][a-zA-Z0-9]*(\s[^<>]*)?>', '', text)        # <p>、<ul> 等其他標籤直接拿掉
    text = html_escape(html.unescape(text), quote=False)                           # AI 自己寫的 &amp; 先還原，避免跳脫兩次
    text = _B_TAG.sub(lambda m: f"<{m.group(1)}b>", text)
    depth = 0
    for m in re.finditer(r'<(/?)b>', text):
        depth += -1 if m.group(1) else 1
        if depth not in (0, 1):
            break
    if depth != 0:
        # 漏打閉合、多打閉合或巢狀：寧可拿掉粗體也要送得出去
        text = re.sub(r'</?b>', '', text)
    return text

def _render_block(item):
    ann, ai = item["ann"], item["ai"]
    e = lambda v: html_escape(str(v), quote=False)
    volume_lots = item["volume_lots"]
    md = lambda iso: f"{iso[5:7]}/{iso[8:10]}"
    stale = (f"\n⚠️ 這是 {md(item['price_date'])} 的收盤價，最新交易日應為 {md(item['price_expected'])}"
             f"（抓價可能失敗或個股停牌，本益比可能失真）"
             if item.get("price_expected") and item.get("price_date") else "")
    return (f"📢【{e(ann['公司名稱'])}｜{e(ann['公司代號'])}】\n"
            f"📅 {e(ann['發言日期'])} {e(ann['發言時間'])}\n"
            f"📑 {e(ann['符合條款'])}\n"
            f"💰 收盤價: {e(item['price'])} | 成交量: {e(volume_lots) if volume_lots is not None else '無資料'}"
            f"{e(stale)}\n\n"
            f"🤖 <b>AI 分析：</b>\n"
            f"{ai_html_to_telegram(ai.get('display_text', ''))}")

def _sync_sheets(cache):
    for item in cache.get("items", []):
        ann, ai, pe = item["ann"], item["ai"], item["pe"]
        rating = ai.get('ai_rating', '')
        if not item.get("gsheet_done"):
            if rating in ('🔴 強烈買進', '🟠 建議買進'):
                try:
                    sync_gsheet(ann, ai, pe, item["price"], item["volume_lots"])
                    item["gsheet_done"] = True
                    print(f"  ✅ Google Sheet 同步 {ann['公司代號']}")
                except Exception as e:
                    print(f"  Google Sheet 失敗 {ann['公司代號']}：{e}")
            else:
                item["gsheet_done"] = True
                print(f"  略過 Google Sheet {ann['公司代號']}（{rating}）")
            _save_cache(cache)
        if not item.get("history_done"):
            if rating == '🔴 強烈買進':
                try:
                    sync_history(ann, item["price"])
                    item["history_done"] = True
                    print(f"  ✅ 歷史紀錄同步 {ann['公司代號']}")
                except Exception as e:
                    print(f"  歷史紀錄失敗 {ann['公司代號']}：{e}")
            else:
                item["history_done"] = True
            _save_cache(cache)

def _send_items(cache):
    pending = [i for i in cache.get("items", []) if not i.get("tg_sent")]
    if not pending:
        return
    # 依「整個公司區塊」分批送出（不可用 send_telegram 內建的逐字切段，
    # 那個切法不管 HTML tag 有沒有被切斷，長訊息會讓 Telegram 回 400）
    SEP, LIMIT = "\n\n━━━━━━━━━━\n\n", 3800
    batches, cur, cur_len = [], [], 0
    for item in pending:
        b = _render_block(item)
        add_len = len(b) + (len(SEP) if cur else 0)
        if cur and cur_len + add_len > LIMIT:
            batches.append(cur)
            cur, cur_len = [], 0
        cur.append((item, b))
        cur_len += len(b) + (len(SEP) if len(cur) > 1 else 0)
    if cur:
        batches.append(cur)

    carried = sum(1 for i in pending if i.get("carried"))
    title = (f"📊 今日符合條件公告（{len(pending)} 筆"
             + (f"，含前次沒送出的 {carried} 筆" if carried else "") + "）")
    backup = [i["ai"]["model_used"] for i in pending if i["ai"].get("model_used") not in (None, AI_MODEL)]
    if backup:
        # 主模型連續被跳過多半是下架了，要讓人看到才會去改 AI_MODELS
        title += (f"\nℹ️ 其中 {len(backup)} 筆由備援模型 {html_escape(', '.join(sorted(set(backup))))} 分析"
                  f"（主模型 {html_escape(AI_MODEL)} 暫時或永久無法使用，詳見 scan log）")
    for n, batch in enumerate(batches, 1):
        prefix = title + (f"（{n}/{len(batches)}）" if len(batches) > 1 else "") + "\n\n"
        send_telegram(prefix + SEP.join(b for _, b in batch))
        for item, _ in batch:
            item["tg_sent"] = True
        _save_cache(cache)  # 一則送出就記下來，後面那則失敗重跑不會重送前面的
    print(f"  ✅ Telegram 送出（{len(pending)} 筆，分 {len(batches)} 則）")

def send_results():
    """回傳 False 代表有 item 沒做完（cache 保留），呼叫端要用非 0 結束讓 Hermes 看得到"""
    cache = _load_cache()
    today = datetime.now(TZ).date().isoformat()
    if cache is None:
        sent_today = LAST_SENT_FILE.exists() and LAST_SENT_FILE.read_text(encoding="utf-8").strip() == today
        if sent_today:
            print("無待送結果（今天已經送過）")
        else:
            send_telegram("⚠️ 飆股雷達：找不到今天的掃描結果（00:30 的 scan 可能沒跑或當掉），今天沒有訊號可送。"
                          "\n請看 Hermes 上的 ~/mops-radar-run.log")
            print("無待送結果，已送 Telegram 告警（scan 可能沒跑）")
        return True

    if cache.get("error"):
        # 錯誤訊息常帶 <urlopen error ...> 這種角括號，不跳脫的話 Telegram HTML 解析直接 400
        send_telegram(f"⚠️ 飆股雷達：{cache.get('failed_at', '')} 掃描失敗，今天沒有新訊號。\n"
                      f"原因：{html_escape(cache['error'])}\n請看 Hermes 上的 ~/mops-radar-run.log")
        print(f"  ✅ Telegram 送出 scan 失敗通知：{cache['error']}")
        cache["error"] = None
        _save_cache(cache)

    if cache.get("empty"):
        send_telegram(cache["empty"])
        print("  ✅ Telegram 送出（無符合公告）")
        cache["empty"] = None
        _save_cache(cache)

    _sync_sheets(cache)
    _send_items(cache)

    left = [i for i in cache.get("items", []) if not _item_done(i)]
    if left:
        cache["items"] = left
        _save_cache(cache)
        print(f"\n⚠️ 還有 {len(left)} 筆 Sheet 沒同步成功，cache 保留：重跑 send 或等明天 scan 併入會只補 Sheet、不重送 Telegram")
        LAST_SENT_FILE.write_text(today, encoding="utf-8")
        return False
    print(f"\n完成！共處理 {len(cache.get('items', []))} 筆")
    CACHE_FILE.unlink()
    LAST_SENT_FILE.write_text(today, encoding="utf-8")
    return True

if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "scan"
    if mode == "scan":
        scan()
    elif mode == "send":
        if not send_results():
            sys.exit(1)
    else:
        sys.exit(f"未知模式：{mode}（用 scan 或 send）")
