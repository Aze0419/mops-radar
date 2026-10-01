"""EPS／營收候選數字與欄位標題（離線），以及 --live 時真打 Jev 對答案"""
import pytest

import jev_live_check
from conftest import LIVE_TYPESAFE_KEY


def announcement(code):
    return next(a for a in jev_live_check.load_fixture() if a["公司代號"] == code)


def headers(m, code):
    """{數字字串: 欄位標題}（同一個數字出現多次時取第一次）"""
    out = {}
    for _, tok, _, header in m.number_candidates(announcement(code)["說明"][:m.JEV_DESC_LIMIT]):
        out.setdefault(tok, header)
    return out


def test_fixture_matches_truth_table():
    codes = {a["公司代號"] for a in jev_live_check.load_fixture()}
    assert codes == set(jev_live_check.TRUTH)


@pytest.mark.parametrize("code, tok, must_contain", [
    ("2836", "0.54", "累計"),              # 高雄銀：本月份欄空白、EPS 只填在累計欄
    ("2836", "0.65", "累計"),
    ("3026", "1.33", "最近一月"),           # 禾伸堂：多行表頭
    ("3026", "3.87", "最近一季"),
    ("6488", "7.90", "115年第2季"),         # 環球晶：表頭就在「(二)單季」那一行
    ("2237", "2.39", "累計"),              # 華德動能
    ("6620", "0.02", "115年7月"),
    ("5465", "0.59", "最近一季單季"),
])
def test_column_header_found(m, code, tok, must_contain):
    assert must_contain in headers(m, code)[tok]


def test_dates_and_item_numbers_are_not_candidates(m):
    toks = [tok for _, tok, _, _ in m.number_candidates(announcement("2845")["說明"])]
    assert "0.08" in toks and "462,717" in toks
    assert "115" not in toks and "09" not in toks, "事實發生日 115/09/09 不該變候選"
    assert "1" not in toks and "5" not in toks, "「1.事實發生日」「5.發生緣由」的項次不該變候選"


def candidate_toks(m, code):
    """(百分比候選, 金額候選) 的數字字串集合"""
    questions, _ = m.jev_questions(announcement(code)["說明"][:m.JEV_DESC_LIMIT])
    toks = lambda q: {label.split(" ", 1)[1] for label in questions[q]["criteria"] if label != m.JEV_NONE}
    return toks("m_yoy"), toks("m_eps")


def test_yoy_column_without_percent_sign_is_percentage_candidate(m):
    # 高力 2026-09-30：表頭寫「與去年同期增減 (%)」，數字本身沒有 %（112.84、44.27），以前兩個年增率都抓成 None
    pcts, amounts = candidate_toks(m, "8996")
    assert {"112.84", "44.27", "54.92", "61.19"} <= pcts
    assert not {"112.84", "44.27"} & amounts, "年增率不該混進 EPS／營收金額的候選"
    assert {"1.35", "2.16", "1,204"} <= amounts and not {"1.35", "1,204"} & pcts


def test_percent_sign_candidates_unchanged(m):
    pcts, amounts = candidate_toks(m, "7792")
    assert {"184.18%", "67.72%"} <= pcts
    assert {"1.22", "0.43", "678.72", "404.68"} <= amounts, "「去年同月」金額欄不是百分比"


def test_cumulative_column_rejected_by_code(m):
    tokens = {"#1 0.54": ("0.54", "累計"), "#2 0.16": ("0.16", "08月(單位仟元)"),
              "#3 20.60": ("20.60", "114年第3季至115年第2季 最近四季累計")}
    assert m._answer_num({"choice": "#1 0.54", "confidence": 1.0}, tokens, reject_header=m.NOT_PERIOD_EPS) is None
    assert m._answer_num({"choice": "#1 0.54", "confidence": 1.0}, tokens) == 0.54
    assert m._answer_num({"choice": "#2 0.16", "confidence": 1.0}, tokens, reject_header=m.NOT_PERIOD_EPS) == 0.16
    # 年初至今累計：累計欄可以，「最近四季累計」不行
    assert m._answer_num({"choice": "#1 0.54", "confidence": 1.0}, tokens, reject_header=m.NOT_YTD_EPS) == 0.54
    assert m._answer_num({"choice": "#3 20.60", "confidence": 1.0}, tokens, reject_header=m.NOT_YTD_EPS) is None
    assert m._answer_num({"choice": "#2 0.16", "confidence": 0.79}, tokens) is None, "信心低於 JEV_MIN_CONF 當沒資料"
    assert m._answer_num({"choice": m.JEV_NONE, "confidence": 1.0}, tokens) is None


def test_same_value_probabilities_are_summed(m):
    # 華南金實測：1.71 在內文、表格、附註各出現一次，機率 0.54／0.42／0.03，confidence 只有 0.52
    tokens = {"#5 1.71": ("1.71", ""), "#18 1.71": ("1.71", "累計合併 每股稅後 盈餘"),
              "#39 1.71": ("1.71", ""), "#3 286.32": ("286.32", "")}
    ans = {"choice": "#5 1.71", "confidence": 0.52,
           "probabilities": {"#5 1.71": 0.54, "#18 1.71": 0.42, "#39 1.71": 0.03, "#3 286.32": 0.01, m.JEV_NONE: 0.0}}
    assert m._answer_num(ans, tokens, reject_header=m.NOT_YTD_EPS) == 1.71
    # 同樣的分佈、但表格那格被欄位規則擋掉：剩 0.57 不到門檻
    assert m._answer_num(ans, tokens, reject_header=m.NOT_PERIOD_EPS) is None


def test_rejected_header_mass_does_not_count(m):
    # 高雄銀單月 EPS：Jev 以高機率挑累計欄的 0.54，被擋掉後其他值都不到門檻
    tokens = {"#6 0.54": ("0.54", "累計"), "#5 0.65": ("0.65", "累計")}
    ans = {"choice": "#6 0.54", "confidence": 1.0,
           "probabilities": {"#6 0.54": 0.95, "#5 0.65": 0.03, m.JEV_NONE: 0.02}}
    assert m._answer_num(ans, tokens, reject_header=m.NOT_PERIOD_EPS) is None
    assert m._answer_num(ans, tokens) == 0.54


def test_calc_pe(m):
    fin = {"m_eps": 1.33, "q_eps": 3.87, "m_yoy": None, "m_rev": None, "r_yoy": None, "source": "jev"}
    pe = m.calc_pe(fin, 791.0)
    assert pe["pre_annual_eps"] == 15.96 and pe["pre_pe"] == 49.56 and pe["pre_eps_source"] == "月"
    pe = m.calc_pe({**fin, "m_eps": None}, 100.0)
    assert pe["pre_annual_eps"] == 15.48 and pe["pre_eps_source"] == "季"
    assert m.calc_pe({**fin, "m_eps": None, "q_eps": None}, 100.0)["pre_pe_note"] == "無EPS資料"


def test_calc_pe_annualizes_ytd_only_as_last_resort(m):
    base = {"m_eps": None, "q_eps": None, "m_yoy": None, "m_rev": None, "r_yoy": None, "source": "jev"}
    # 華南金 115/08：1~8 月累計稅後 EPS 1.71 → 1.71 × 12/8 = 2.565
    pe = m.calc_pe({**base, "c_eps": 1.71, "c_months": 8}, 30.0)
    assert pe["pre_annual_eps"] == 2.56
    assert pe["pre_eps_source"] == "累計" and "12/8" in pe["pre_eps_basis"] and pe["pre_pe"] is not None
    # 有單月就不用累計
    pe = m.calc_pe({**base, "m_eps": 0.2, "c_eps": 1.71, "c_months": 8}, 30.0)
    assert pe["pre_annual_eps"] == 2.4 and pe["pre_eps_source"] == "月"
    # 缺月份就不能年化
    assert m.calc_pe({**base, "c_eps": 1.71, "c_months": None}, 30.0)["pre_pe_note"] == "無EPS資料"
    # 夏都 1~8 月累計虧損 → 虧損，不算本益比
    assert m.calc_pe({**base, "c_eps": -0.18, "c_months": 8}, 30.0)["pre_pe_note"] == "虧損"
    # regex 備援沒有累計欄位也要能算
    assert m.calc_pe({**base, "source": "regex"}, 30.0)["pre_pe_note"] == "無EPS資料"


@pytest.mark.live
def test_jev_live_against_truth(m, monkeypatch):
    if not LIVE_TYPESAFE_KEY:
        pytest.skip("沒有 TYPESAFE_API_KEY")
    monkeypatch.setattr(m, "TYPESAFE_KEY", LIVE_TYPESAFE_KEY)
    errors = jev_live_check.run(m)
    assert not errors, "\n".join(errors)
