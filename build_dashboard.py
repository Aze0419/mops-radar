#!/usr/bin/env python3
"""重建飆股雷達試算表的「績效儀表板」分頁（版面照因子選股試算表的績效儀表板）。

儀表板全部是公式，直接讀「歷史紀錄」（現價來自 TW_PRICE），平常不用跑這支；
只有要改版面、改計算方式，或分頁被弄壞時才執行：

    python build_dashboard.py

會整頁重寫「績效儀表板」與隱藏分頁「儀表板計算」（不存在就新增），其他分頁不動。
"""
import os
import pathlib

_env = pathlib.Path(__file__).parent / ".env"
if _env.exists():
    for _line in _env.read_text(encoding="utf-8").splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#"):
            _line = _line.replace("export ", "", 1)
            if "=" in _line:
                _k, _v = _line.split("=", 1)
                os.environ.setdefault(_k.strip().strip(chr(34)).strip(chr(39)), _v.strip().strip(chr(34)).strip(chr(39)))

RADAR_SHEET_ID = os.environ.get("RADAR_SHEET_ID", "1UulUtCjGbBUk_36xCK7TuRSrEvBFCCr7okKWBlJBvuc")
SA_KEY_FILE = os.environ.get("SA_KEY_FILE", os.path.join(os.path.dirname(os.path.abspath(__file__)), "google-sa.json"))

DASH, CALC, HIST = "績效儀表板", "儀表板計算", "歷史紀錄"
H = f"'{HIST}'!"
C = f"'{CALC}'!"


def _rgb(hex_color):
    return {k: int(hex_color[i:i + 2], 16) / 255 for k, i in (("red", 1), ("green", 3), ("blue", 5))}


NAVY, SOFT, GRAY = _rgb("#1a3a8a"), _rgb("#e8edf8"), _rgb("#5f6368")
UP, DN = _rgb("#d93025"), _rgb("#188038")
BAR, WIN = _rgb("#4a5b94"), _rgb("#c77c1e")
WHITE, BENCH = _rgb("#ffffff"), _rgb("#fff4e0")
FMT = dict(RET="+0.0%;-0.0%;0.0%", PNL="+#,##0.00;-#,##0.00;0.00", RATE="0.0%", RATIO="0.00", INT="0")

# 儀表板計算的列配置：因子 3..32、基準 33、因子合計 35；疊加／月份 3..22、合計 24
NF, BENCH_ROW, FTOTAL = 30, 33, 35
NG, GTOTAL = 20, 24
DASH_ROWS = 200

FACTOR_HEAD = ["策略因子", "標的數", "獲利數", "勝率", "平均報酬率", "累積損益", "盈虧比", "獲利因子"]
TOTAL_LABELS = ("全體標的合計", "全部進場紀錄合計", "全期累計")
NOTES = [
    "說明：因子＝「歷史紀錄」H 欄的每個策略因子（飆股雷達強烈買進訊號＋因子選股「選股結果」的入選條件）；董事長增持的各種寫法合併成「董事長增持」。",
    "進場價＝「歷史紀錄」F 欄期初價、進場日＝G 欄日期；進場日當天不統計損益（顯示「待收盤」），隔天起才計入。",
    "策略因子表與長條圖依平均報酬率由大到小排序。0050 基準列＝0050 從第一筆進場日收盤持有到現在的報酬，當作大盤比較值，不計入合計。",
    "報酬率＝現價(TW_PRICE)/進場價−1；累積損益＝各筆報酬率(%)加總；勝率＝報酬率>0 的比例；盈虧比＝平均獲利÷平均虧損；獲利因子＝獲利加總÷虧損加總（>1 整體有賺）。",
    "「歷史紀錄」每一列算一筆進場紀錄（同一檔不同日期再入選各算一筆）；一筆被多個因子選中時因子表各計一次，KPI、因子疊加與月份表每筆只算一次。全部是公式即時計算，計算過程在隱藏分頁「儀表板計算」。",
]


def col_index(col):
    """'A' → 0、'AA' → 26。"""
    n = 0
    for ch in col:
        n = n * 26 + ord(ch) - 64
    return n - 1


def col_letter(i):
    """0 → 'A'、26 → 'AA'。"""
    s = ""
    i += 1
    while i:
        i, rem = divmod(i - 1, 26)
        s = chr(65 + rem) + s
    return s


def col_range(start, n):
    i = col_index(start)
    return [col_letter(i + k) for k in range(n)]


def _lit(vals):
    return "{" + ",".join(f'"{v}"' for v in vals) + "}"


def calc_formulas():
    """隱藏分頁「儀表板計算」：A1 起的儲存格 → 值或公式。

    A:D 跟「歷史紀錄」第 3 列起逐列對齊：
      A 正規化因子：前後包「、」方便精確比對；句點也當分隔（H 欄有「飆股雷達09/16. 量比」這種寫法），
        「董事長202607」「董事長增持(張)」併成「董事長增持」，「飆股雷達MM/DD」併成「飆股雷達」
      B 已統計報酬率：進場日（G 欄）在今天之前才有值，當天的留空＝待收盤
      C 含因子數（0＝H 欄空白）  D 進場月份
    F:T 每個因子一列的統計，V:AC 依平均報酬率排序（長條圖資料來源），AE:AM 因子疊加，AO:AW 月份，AX:AY 0050 基準。
    """
    calc = {
        "A1": "此分頁是「績效儀表板」的計算區（已隱藏），請勿修改；要改請改 repo 的 build_dashboard.py 再重跑。資料來源：歷史紀錄。",
        "A2": "因子(正規化)", "B2": "已統計報酬率", "C2": "含因子數", "D2": "進場月份",
        "A3": (f'=ARRAYFORMULA(IF({H}A3:A="",,"、"&REGEXREPLACE(REGEXREPLACE(REGEXREPLACE(TRIM({H}H3:H&""),'
               f'"\\s*[、.．]\\s*","、"),"董事長[^、]*","董事長增持"),"飆股雷達[^、]*","飆股雷達")&"、"))'),
        "B3": (f'=ARRAYFORMULA(IF(({H}A3:A<>"")*ISNUMBER({H}D3:D)*ISNUMBER({H}G3:G)*({H}G3:G<TODAY()),'
               f'{H}D3:D,))'),
        "C3": '=ARRAYFORMULA(IF(A3:A="",,IF(LEN(A3:A)<=2,0,LEN(A3:A)-LEN(SUBSTITUTE(A3:A,"、",""))-1)))',
        "D3": f'=ARRAYFORMULA(IF(A3:A="",,TEXT({H}G3:G,"yyyy-mm")))',
    }

    # ── 因子：F 欄列出所有因子，G:T 各自統計 ──
    for c, t in zip("FGHIJKLMNOPQRST", ["因子", "標的數", "已統計數", "獲利數", "勝率", "平均報酬率", "累積損益",
                                         "盈虧比", "獲利因子", "獲利加總", "獲利筆數", "虧損加總", "虧損筆數",
                                         "排序鍵", "獲利數顯示"]):
        calc[f"{c}2"] = t
    calc["F3"] = '=IFERROR(ARRAYFORMULA(UNIQUE(TOCOL(SPLIT(FILTER(MID(A3:A,2,LEN(A3:A)-2),LEN(A3:A)>2),"、"),3))),)'
    A, B = "$A$3:$A", "$B$3:$B"
    for r in range(3, 3 + NF):
        f = f"$F{r}"
        pat = f'"*、"&{f}&"、*"'
        blank = f'IF({f}="","",'
        calc[f"G{r}"] = f"={blank}COUNTIF({A},{pat}))"
        calc[f"H{r}"] = f'={blank}COUNTIFS({A},{pat},{B},"<>"))'
        calc[f"I{r}"] = f'={blank}COUNTIFS({A},{pat},{B},">0"))'
        calc[f"J{r}"] = f'={blank}IF(H{r}=0,"待收盤",I{r}/H{r}))'
        calc[f"K{r}"] = f'={blank}IF(H{r}=0,"待收盤",SUMIFS({B},{A},{pat})/H{r}))'
        calc[f"L{r}"] = f'={blank}IF(H{r}=0,"待收盤",SUMIFS({B},{A},{pat})*100))'
        calc[f"M{r}"] = f'={blank}IF(H{r}=0,"待收盤",IF(R{r}=0,"無虧損",IF(P{r}=0,0,(O{r}/P{r})/(Q{r}/R{r})))))'
        calc[f"N{r}"] = f'={blank}IF(H{r}=0,"待收盤",IF(R{r}=0,"無虧損",O{r}/Q{r})))'
        calc[f"O{r}"] = f'={blank}SUMIFS({B},{A},{pat},{B},">0")*100)'
        calc[f"P{r}"] = f"={blank}I{r})"
        calc[f"Q{r}"] = f'={blank}-SUMIFS({B},{A},{pat},{B},"<0")*100)'
        calc[f"R{r}"] = f'={blank}COUNTIFS({A},{pat},{B},"<0"))'
        calc[f"S{r}"] = f'={blank}IF(H{r}=0,-1E9,K{r}))'
        calc[f"T{r}"] = f'={blank}IF(H{r}=0,"待收盤",I{r}))'

    # ── 0050 基準：「歷史紀錄」第一筆進場日收盤持有到現在。代號要保留成文字，不然 0050 會變 50 ──
    calc.update({
        "AX2": "基準代號", "AY2": "'0050",
        "AX3": "起始日", "AY3": f"=MIN({H}G3:G)",
        "AX4": "起始收盤", "AY4": '=IFERROR(INDEX(GOOGLEFINANCE("TPE:"&AY2,"close",AY3,AY3+10),2,2),)',
        "AX5": "現價", "AY5": "=TW_PRICE(AY2)",
    })
    b = BENCH_ROW
    calc[f"F{b}"] = '=AY2&" 基準（"&TEXT(AY3,"mm/dd")&" 起）"'
    for c in "GJLMNT":
        calc[f"{c}{b}"] = "—"
    calc[f"K{b}"] = '=IF(OR(AY4="",NOT(ISNUMBER(AY5))),"待收盤",IF(TODAY()>AY3,AY5/AY4-1,"待收盤"))'
    calc[f"S{b}"] = f"=IF(ISNUMBER(K{b}),K{b},-1E9)"

    # ── 因子合計：每個因子各計一次（同一筆被兩個因子選中算兩筆，跟因子選股儀表板一致） ──
    t = FTOTAL
    calc[f"F{t}"] = TOTAL_LABELS[0]
    for c in "GHIOPQR":
        calc[f"{c}{t}"] = f"=SUM({c}3:{c}{2 + NF})"
    calc[f"J{t}"] = f'=IF(H{t}=0,"待收盤",I{t}/H{t})'
    calc[f"K{t}"] = f'=IF(H{t}=0,"待收盤",SUM(L3:L{2 + NF})/100/H{t})'
    calc[f"L{t}"] = f'=IF(H{t}=0,"待收盤",SUM(L3:L{2 + NF}))'
    calc[f"M{t}"] = f'=IF(H{t}=0,"待收盤",IF(R{t}=0,"無虧損",IF(P{t}=0,0,(O{t}/P{t})/(Q{t}/R{t}))))'
    calc[f"N{t}"] = f'=IF(H{t}=0,"待收盤",IF(R{t}=0,"無虧損",O{t}/Q{t}))'
    calc[f"T{t}"] = f'=IF(H{t}=0,"待收盤",I{t})'

    # ── 依平均報酬率排序（排序鍵在 F:T 第 14 欄），全部待收盤的排最後 ──
    for c, v in zip(col_range("V", 8), FACTOR_HEAD):
        calc[f"{c}2"] = v
    calc["V3"] = (f'=IFERROR(LET(r,FILTER(F3:T{BENCH_ROW},F3:F{BENCH_ROW}<>""),'
                  f"CHOOSECOLS(SORT(r,14,FALSE),1,2,15,5,6,7,8,9)),)")

    n_rows = f"=COUNTA({H}A3:A)"
    _group_block(calc, "AE", "AF", "$C$3:$C", '=IFERROR(SORT(UNIQUE(FILTER(C3:C,C3:C<>"")),1,FALSE),)',
                 'IF({k}=0,"未標註因子",{k}&" 個因子")', TOTAL_LABELS[1], n_rows)
    _group_block(calc, "AO", "AP", "$D$3:$D", '=IFERROR(SORT(UNIQUE(FILTER(D3:D,D3:D<>""))),)',
                 'LEFT({k},4)&" 年 "&VALUE(RIGHT({k},2))&" 月"', TOTAL_LABELS[2], n_rows)
    return calc


def _group_block(calc, key_col, label_col, key_range, list_formula, label_expr, total_label, total_n):
    """疊加／月份共用：key_col 列出分組值，label_col 起 8 欄依序為
    標籤、標的數、已統計數、獲利數顯示、勝率、平均報酬率、累積損益、獲利數(數值)。"""
    B = "$B$3:$B"
    cols = col_range(label_col, 8)
    lab, n, done, winsd, rate, avg, tot, wins = cols
    calc[f"{key_col}2"] = "分組值"
    for c, v in zip(cols, ["標籤", "標的數", "已統計數", "獲利數", "勝率", "平均報酬率", "累積損益", "獲利數(數值)"]):
        calc[f"{c}2"] = v
    calc[f"{key_col}3"] = list_formula
    for r in range(3, 3 + NG):
        k = f"${key_col}{r}"
        blank = f'IF({k}="","",'
        calc[f"{lab}{r}"] = f"={blank}{label_expr.format(k=k)})"
        calc[f"{n}{r}"] = f"={blank}COUNTIF({key_range},{k}))"
        calc[f"{done}{r}"] = f'={blank}COUNTIFS({key_range},{k},{B},"<>"))'
        calc[f"{wins}{r}"] = f'={blank}COUNTIFS({key_range},{k},{B},">0"))'
        calc[f"{winsd}{r}"] = f'={blank}IF({done}{r}=0,"待收盤",{wins}{r}))'
        calc[f"{rate}{r}"] = f'={blank}IF({done}{r}=0,"待收盤",{wins}{r}/{done}{r}))'
        calc[f"{avg}{r}"] = f'={blank}IF({done}{r}=0,"待收盤",SUMIFS({B},{key_range},{k})/{done}{r}))'
        calc[f"{tot}{r}"] = f'={blank}IF({done}{r}=0,"待收盤",SUMIFS({B},{key_range},{k})*100))'
    g = GTOTAL
    calc[f"{lab}{g}"] = total_label
    calc[f"{n}{g}"] = total_n
    calc[f"{done}{g}"] = f"=COUNT({B})"
    calc[f"{wins}{g}"] = f'=COUNTIF({B},">0")'
    calc[f"{winsd}{g}"] = f'=IF({done}{g}=0,"待收盤",{wins}{g})'
    calc[f"{rate}{g}"] = f'=IF({done}{g}=0,"待收盤",{wins}{g}/{done}{g})'
    calc[f"{avg}{g}"] = f'=IF({done}{g}=0,"待收盤",AVERAGE({B}))'
    calc[f"{tot}{g}"] = f'=IF({done}{g}=0,"待收盤",SUM({B})*100)'


def dash_formulas():
    """「績效儀表板」：標題、KPI，A6 一個 VSTACK 依序疊出三張表與說明（表長隨資料變動）。"""
    st, mo = col_range("AF", 8), col_range("AP", 8)
    stack = ",".join([
        _lit(["📊 策略因子成效分析"]),
        _lit(FACTOR_HEAD),
        f'FILTER({C}V3:AC{BENCH_ROW},{C}V3:V{BENCH_ROW}<>"")',
        f"CHOOSECOLS({C}F{FTOTAL}:T{FTOTAL},1,2,15,5,6,7,8,9)",
        _lit([""]),
        _lit(["🧩 因子疊加成效（同一筆進場紀錄含幾個因子）"]),
        _lit(["含因子數", "標的數", "獲利數", "勝率", "平均報酬率", "累積損益"]),
        f'FILTER(CHOOSECOLS({C}{st[0]}3:{st[7]}{2 + NG},1,2,4,5,6,7),{C}{st[0]}3:{st[0]}{2 + NG}<>"")',
        f"CHOOSECOLS({C}{st[0]}{GTOTAL}:{st[7]}{GTOTAL},1,2,4,5,6,7)",
        _lit([""]),
        _lit(["📅 月份進場績效趨勢比較"]),
        _lit(["月份", "標的數", "獲利數", "勝率", "平均報酬率"]),
        f'FILTER(CHOOSECOLS({C}{mo[0]}3:{mo[7]}{2 + NG},1,2,4,5,6),{C}{mo[0]}3:{mo[0]}{2 + NG}<>"")',
        f"CHOOSECOLS({C}{mo[0]}{GTOTAL}:{mo[7]}{GTOTAL},1,2,4,5,6)",
        _lit([""]),
    ] + [_lit([n]) for n in NOTES])
    done = f"COUNT({C}B3:B)"
    return {
        "A1": "飆股雷達 策略績效與因子分析儀表板",
        "A2": (f'="資料範圍：歷史紀錄（共 "&COUNTA({H}A3:A)&" 筆進場紀錄、"&COUNTUNIQUE({H}A3:A)&" 檔不重複標的）｜ 分析週期："'
               f'&TEXT(MIN({H}G3:G),"yyyy-mm-dd")&" ~ "&TEXT(MAX({H}G3:G),"yyyy-mm-dd")'
               f'&" ｜ 計算時間："&TEXT(NOW(),"yyyy-mm-dd HH:mm")&"（公式即時更新）"'),
        "A3": "總追蹤標的數", "B3": "累積總損益", "C3": "平均報酬率", "D3": "策略勝率",
        "A4": f"=COUNTA({H}A3:A)",
        "B4": f'=IF({done}=0,"待收盤",SUM({C}B3:B)*100)',
        "C4": f'=IF({done}=0,"待收盤",AVERAGE({C}B3:B))',
        "D4": f'=IF({done}=0,"待收盤",COUNTIF({C}B3:B,">0")/{done})',
        # IFNA：VSTACK 會把較窄的列補 #N/A
        "A6": f'=IFNA(VSTACK({stack}),"")',
    }


# ── Sheets API request 組裝 ───────────────────────────────────

def _grid(sid, r0, r1, c0, c1):
    return {"sheetId": sid, "startRowIndex": r0, "endRowIndex": r1, "startColumnIndex": c0, "endColumnIndex": c1}


def _repeat(rng, fmt, fields):
    return {"repeatCell": {"range": rng, "cell": {"userEnteredFormat": fmt}, "fields": fields}}


def _numfmt(rng, pattern):
    return _repeat(rng, {"numberFormat": {"type": "NUMBER", "pattern": pattern}}, "userEnteredFormat.numberFormat")


def _size(sid, dim, start, end, px):
    return {"updateDimensionProperties": {"range": {"sheetId": sid, "dimension": dim, "startIndex": start, "endIndex": end},
                                          "properties": {"pixelSize": px}, "fields": "pixelSize"}}


def _rule(ranges, formula, fmt):
    return {"ranges": ranges, "booleanRule": {"condition": {"type": "CUSTOM_FORMULA",
                                                          "values": [{"userEnteredValue": formula}]}, "format": fmt}}


def format_requests(dash_id, calc_id):
    """儀表板的欄寬列高、數字格式、條件格式與兩張長條圖；計算頁的圖表欄位格式並隱藏計算頁。

    表格位置會隨資料長度變動，所以表頭／合計列／基準列的底色都用條件格式依 A 欄文字判斷。
    Sheets 每格只套第一條命中的規則，合計列、基準列的紅綠字要各自另寫一條合併規則排在前面。
    """
    d = lambda r0, r1, c0, c1: _grid(dash_id, r0, r1, c0, c1)
    reqs = [_size(dash_id, "COLUMNS", i, i + 1, px) for i, px in enumerate([300, 90, 90, 90, 110, 110, 80, 80])]
    reqs += [_size(dash_id, "ROWS", a, b, px) for a, b, px in [(1, DASH_ROWS, 21), (0, 1, 34), (3, 4, 30)]]
    reqs += [
        # 三張表欄位意義一致（標的數、獲利數、勝率、平均報酬率、累積損益、盈虧比、獲利因子），整欄套格式
        _numfmt(d(5, DASH_ROWS, 1, 3), FMT["INT"]),
        _numfmt(d(5, DASH_ROWS, 3, 4), FMT["RATE"]),
        _numfmt(d(5, DASH_ROWS, 4, 5), FMT["RET"]),
        _numfmt(d(5, DASH_ROWS, 5, 6), FMT["PNL"]),
        _numfmt(d(5, DASH_ROWS, 6, 8), FMT["RATIO"]),
        _repeat(d(5, DASH_ROWS, 1, 8), {"horizontalAlignment": "RIGHT"}, "userEnteredFormat.horizontalAlignment"),
        _repeat(d(0, 1, 0, 1), {"textFormat": {"fontSize": 18, "bold": True, "foregroundColor": NAVY}}, "userEnteredFormat.textFormat"),
        _repeat(d(1, 2, 0, 1), {"textFormat": {"foregroundColor": GRAY}}, "userEnteredFormat.textFormat"),
        _repeat(d(2, 3, 0, 4), {"backgroundColor": SOFT, "horizontalAlignment": "CENTER",
                                "textFormat": {"bold": True, "foregroundColor": GRAY}},
                "userEnteredFormat(backgroundColor,horizontalAlignment,textFormat)"),
        _repeat(d(3, 4, 0, 4), {"backgroundColor": SOFT, "horizontalAlignment": "CENTER",
                                "textFormat": {"bold": True, "fontSize": 16}},
                "userEnteredFormat(backgroundColor,horizontalAlignment,textFormat)"),
        _numfmt(d(3, 4, 0, 1), FMT["INT"]),
        _numfmt(d(3, 4, 1, 2), FMT["PNL"]),
        _numfmt(d(3, 4, 2, 3), FMT["RET"]),
        _numfmt(d(3, 4, 3, 4), FMT["RATE"]),
        {"updateBorders": {"range": d(2, 4, 0, 4), "innerVertical": {"style": "SOLID", "color": WHITE}}},
    ]

    rows = lambda c0, c1: [d(5, DASH_ROWS, c0, c1)]
    hdr = 'OR($A6="策略因子",$A6="含因子數",$A6="月份")'
    tot = "OR(" + ",".join(f'$A6="{t}"' for t in TOTAL_LABELS) + ")"
    ben = 'REGEXMATCH($A6,"^\\S+ 基準（")'
    rules = [
        _rule(rows(0, 8), f'=AND({hdr},A6<>"")', {"backgroundColor": NAVY, "textFormat": {"bold": True, "foregroundColor": WHITE}}),
        _rule(rows(4, 6), f"=AND({tot},ISNUMBER(E6),E6>0)", {"backgroundColor": SOFT, "textFormat": {"bold": True, "foregroundColor": UP}}),
        _rule(rows(4, 6), f"=AND({tot},ISNUMBER(E6),E6<0)", {"backgroundColor": SOFT, "textFormat": {"bold": True, "foregroundColor": DN}}),
        _rule(rows(0, 8), f'=AND({tot},A6<>"")', {"backgroundColor": SOFT, "textFormat": {"bold": True}}),
        _rule(rows(0, 8), f"=AND({ben},ISNUMBER($E6),$E6>0,COLUMN()=5)", {"backgroundColor": BENCH, "textFormat": {"italic": True, "foregroundColor": UP}}),
        _rule(rows(0, 8), f"=AND({ben},ISNUMBER($E6),$E6<0,COLUMN()=5)", {"backgroundColor": BENCH, "textFormat": {"italic": True, "foregroundColor": DN}}),
        _rule(rows(0, 8), f"={ben}", {"backgroundColor": BENCH, "textFormat": {"italic": True}}),
        _rule(rows(0, 1), '=REGEXMATCH($A6,"^(📊|🧩|📅)")', {"textFormat": {"bold": True, "foregroundColor": NAVY}}),
        _rule(rows(0, 1), "=LEN($A6)>45", {"textFormat": {"foregroundColor": GRAY}}),
        _rule(rows(4, 6), "=AND(ISNUMBER(E6),E6>0)", {"textFormat": {"foregroundColor": UP}}),
        _rule(rows(4, 6), "=AND(ISNUMBER(E6),E6<0)", {"textFormat": {"foregroundColor": DN}}),
        _rule([d(3, 4, 1, 3)], "=AND(ISNUMBER(B4),B4>0)", {"textFormat": {"foregroundColor": UP}}),
        _rule([d(3, 4, 1, 3)], "=AND(ISNUMBER(B4),B4<0)", {"textFormat": {"foregroundColor": DN}}),
    ]
    # index 一定要給：不給是插到最前面，優先順序會整個反過來
    reqs += [{"addConditionalFormatRule": {"index": i, "rule": r}} for i, r in enumerate(rules)]

    # 長條圖讀計算頁排序好的因子表；範圍多留空列，因子變多也畫得到（尾端空列不會畫出來）
    def chart(title, col, color, axis_title, row, vmax=None):
        src = lambda c: {"sourceRange": {"sources": [_grid(calc_id, 1, BENCH_ROW, col_index(c), col_index(c) + 1)]}}
        left = {"position": "LEFT_AXIS", "title": axis_title}
        if vmax is not None:
            left["viewWindowOptions"] = {"viewWindowMin": 0, "viewWindowMax": vmax, "viewWindowMode": "EXPLICIT"}
        return {"addChart": {"chart": {
            "spec": {"title": title, "hiddenDimensionStrategy": "SHOW_ALL", "basicChart": {
                "chartType": "COLUMN", "legendPosition": "NO_LEGEND", "headerCount": 1,
                "axis": [{"position": "BOTTOM_AXIS", "title": "策略因子"}, left],
                "domains": [{"domain": src("V")}],
                "series": [{"series": src(col), "targetAxis": "LEFT_AXIS", "colorStyle": {"rgbColor": color}}]}},
            "position": {"overlayPosition": {"anchorCell": {"sheetId": dash_id, "rowIndex": row, "columnIndex": 9},
                                             "widthPixels": 720, "heightPixels": 440}}}}}

    reqs += [chart("各策略因子平均報酬率比較", "Z", BAR, "平均報酬率", 2),
             chart("各策略因子勝率比較", "Y", WIN, "勝率", 25, 1)]
    # 圖表座標軸的刻度格式跟資料來源欄位走
    reqs += [_numfmt(_grid(calc_id, 2, BENCH_ROW, col_index("Y"), col_index("Y") + 1), "0%"),
             _numfmt(_grid(calc_id, 2, BENCH_ROW, col_index("Z"), col_index("Z") + 1), "+0.0%;-0.0%;0%"),
             _numfmt(_grid(calc_id, 2, 3, col_index("AY"), col_index("AY") + 1), "yyyy-mm-dd")]
    reqs.append({"updateSheetProperties": {"properties": {"sheetId": calc_id, "hidden": True}, "fields": "hidden"}})
    return reqs


def _cells(sheet, cells):
    return [{"range": f"'{sheet}'!{a1}", "values": [[v]]} for a1, v in cells.items()]


def main():
    import gspread
    ss = gspread.service_account(filename=SA_KEY_FILE).open_by_key(RADAR_SHEET_ID)
    fields = "sheets(properties(sheetId,title),charts.chartId,conditionalFormats.ranges.sheetId)"
    sheets = {s["properties"]["title"]: s for s in ss.fetch_sheet_metadata({"fields": fields})["sheets"]}
    missing = [t for t in (DASH, CALC) if t not in sheets]
    if missing:
        ss.batch_update({"requests": [{"addSheet": {"properties": {"title": t}}} for t in missing]})
        sheets = {s["properties"]["title"]: s for s in ss.fetch_sheet_metadata({"fields": fields})["sheets"]}
    dash_id = sheets[DASH]["properties"]["sheetId"]
    calc_id = sheets[CALC]["properties"]["sheetId"]

    # 先整頁清空（值、格式、條件格式、舊圖表），再寫公式、套格式
    old = sheets[DASH]
    clear = [{"deleteEmbeddedObject": {"objectId": c["chartId"]}} for c in old.get("charts", [])]
    clear += [{"deleteConditionalFormatRule": {"sheetId": dash_id, "index": 0}} for _ in old.get("conditionalFormats", [])]
    clear += [
        {"updateSheetProperties": {"properties": {"sheetId": calc_id, "gridProperties": {"rowCount": 5000, "columnCount": 52}},
                                   "fields": "gridProperties(rowCount,columnCount)"}},
        {"updateSheetProperties": {"properties": {"sheetId": dash_id,
                                                  "gridProperties": {"rowCount": DASH_ROWS, "columnCount": 26, "hideGridlines": True}},
                                   "fields": "gridProperties(rowCount,columnCount,hideGridlines)"}},
        {"updateCells": {"range": {"sheetId": calc_id}, "fields": "*"}},
        {"updateCells": {"range": {"sheetId": dash_id}, "fields": "*"}},
    ]
    ss.batch_update({"requests": clear})
    ss.values_batch_update({"valueInputOption": "USER_ENTERED",
                            "data": _cells(CALC, calc_formulas()) + _cells(DASH, dash_formulas())})
    ss.batch_update({"requests": format_requests(dash_id, calc_id)})
    print(f"已重建「{DASH}」與「{CALC}」：https://docs.google.com/spreadsheets/d/{RADAR_SHEET_ID}/edit#gid={dash_id}")


if __name__ == "__main__":
    main()
