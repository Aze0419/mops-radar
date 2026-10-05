"""build_dashboard.py：只檢查產出的公式與 request 結構，不連 Google。"""
import re

import build_dashboard as bd


def _balanced(formula):
    """括號與大括號成對（忽略字串常值裡的字）。"""
    depth = {"(": 0, "{": 0}
    pairs = {")": "(", "}": "{"}
    for ch in re.sub(r'"[^"]*"', '""', formula):
        if ch in depth:
            depth[ch] += 1
        elif ch in pairs:
            depth[pairs[ch]] -= 1
            if depth[pairs[ch]] < 0:
                return False
    return all(v == 0 for v in depth.values())


def test_col_helpers():
    assert [bd.col_letter(i) for i in (0, 25, 26, 51)] == ["A", "Z", "AA", "AZ"]
    assert [bd.col_index(c) for c in ("A", "Z", "AA", "AY")] == [0, 25, 26, 50]
    assert bd.col_range("AF", 3) == ["AF", "AG", "AH"]


def test_all_formulas_balanced():
    cells = {**bd.calc_formulas(), **bd.dash_formulas()}
    bad = [a1 for a1, v in cells.items() if isinstance(v, str) and v.startswith("=") and not _balanced(v)]
    assert not bad, f"括號不成對：{bad}"


def test_tag_normalization_splits_on_dots():
    # H 欄實際出現過「飆股雷達09/16. 量比」：句點也要當分隔，否則量比會被併進飆股雷達
    a3 = bd.calc_formulas()["A3"]
    assert '"\\s*[、.．]\\s*","、"' in a3
    assert '"董事長[^、]*","董事長增持"' in a3
    assert '"飆股雷達[^、]*","飆股雷達"' in a3


def test_sorted_view_picks_display_columns():
    # F:T 第 1,2,15,5..9 欄＝因子、標的數、獲利數顯示、勝率、平均報酬率、累積損益、盈虧比、獲利因子；排序鍵是第 14 欄
    calc = bd.calc_formulas()
    assert "SORT(r,14,FALSE),1,2,15,5,6,7,8,9" in calc["V3"]
    assert calc["S2"] == "排序鍵" and calc["T2"] == "獲利數顯示"
    assert [calc[f"{c}2"] for c in bd.col_range("V", 8)] == bd.FACTOR_HEAD


def test_dashboard_stack_reads_group_blocks():
    calc = bd.calc_formulas()
    a6 = bd.dash_formulas()["A6"]
    # 疊加／月份的標籤欄要對到 _group_block 寫的位置
    assert calc["AF2"] == "標籤" and calc["AP2"] == "標籤"
    assert "'儀表板計算'!AF3:AM22" in a6 and "'儀表板計算'!AP3:AW22" in a6
    for label in bd.TOTAL_LABELS:
        assert any(v == label for v in calc.values()), label
    assert all(note in a6 for note in bd.NOTES)


def test_benchmark_code_kept_as_text():
    assert bd.calc_formulas()["AY2"] == "'0050"


def test_conditional_rules_have_increasing_index():
    reqs = bd.format_requests(1, 2)
    idx = [r["addConditionalFormatRule"]["index"] for r in reqs if "addConditionalFormatRule" in r]
    assert idx == list(range(len(idx)))
    charts = [r for r in reqs if "addChart" in r]
    assert [c["addChart"]["chart"]["spec"]["title"] for c in charts] == ["各策略因子平均報酬率比較", "各策略因子勝率比較"]
