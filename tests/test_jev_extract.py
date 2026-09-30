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


def test_cumulative_column_rejected_by_code(m):
    tokens = {"#1 0.54": ("0.54", "累計"), "#2 0.16": ("0.16", "08月(單位仟元)")}
    assert m._answer_num({"choice": "#1 0.54", "confidence": 1.0}, tokens, reject_cumulative=True) is None
    assert m._answer_num({"choice": "#1 0.54", "confidence": 1.0}, tokens) == 0.54
    assert m._answer_num({"choice": "#2 0.16", "confidence": 1.0}, tokens, reject_cumulative=True) == 0.16
    assert m._answer_num({"choice": "#2 0.16", "confidence": 0.79}, tokens) is None, "信心低於 JEV_MIN_CONF 當沒資料"
    assert m._answer_num({"choice": m.JEV_NONE, "confidence": 1.0}, tokens) is None


def test_calc_pe(m):
    fin = {"m_eps": 1.33, "q_eps": 3.87, "m_yoy": None, "m_rev": None, "r_yoy": None, "source": "jev"}
    pe = m.calc_pe(fin, 791.0)
    assert pe["pre_annual_eps"] == 15.96 and pe["pre_pe"] == 49.56 and pe["pre_eps_source"] == "月"
    pe = m.calc_pe({**fin, "m_eps": None}, 100.0)
    assert pe["pre_annual_eps"] == 15.48 and pe["pre_eps_source"] == "季"
    assert m.calc_pe({**fin, "m_eps": None, "q_eps": None}, 100.0)["pre_pe_note"] == "無EPS資料"


@pytest.mark.live
def test_jev_live_against_truth(m, monkeypatch):
    if not LIVE_TYPESAFE_KEY:
        pytest.skip("沒有 TYPESAFE_API_KEY")
    monkeypatch.setattr(m, "TYPESAFE_KEY", LIVE_TYPESAFE_KEY)
    errors = jev_live_check.run(m)
    assert not errors, "\n".join(errors)
