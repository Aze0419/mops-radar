"""fetch_prices.py 寫飆股雷達「收盤價」分頁：依代號合併、代號型別要跟「歷史紀錄」A 欄一致。"""
import datetime as dt

import fetch_prices as fp

D = dt.date(2026, 10, 5)


def test_merge_keeps_codes_not_in_this_run():
    # --tse-only 只有上市：上櫃前一天的收盤價要留著
    existing = [fp.CLOSE_HEAD, [2330, 1400, "2026-10-02"], [8046, 1450, "2026-10-02"]]
    rows = fp.close_sheet_rows(existing, D, {"2330": {"close": 1415.0}})
    assert rows[0] == fp.CLOSE_HEAD
    assert rows[1:] == [[2330, 1415.0, "2026-10-05"], [8046, 1450, "2026-10-02"]]


def test_code_types_match_history_column():
    # 純數字存成數字（VLOOKUP 對「歷史紀錄」A 欄的數字），0 開頭保留文字
    rows = fp.close_sheet_rows([], D, {"0050": {"close": 115.95}, "2330": {"close": 1415.0}})
    assert rows[1:] == [["0050", 115.95, "2026-10-05"], [2330, 1415.0, "2026-10-05"]]


def test_empty_sheet_and_blank_rows():
    rows = fp.close_sheet_rows([fp.CLOSE_HEAD, [], ["", "", ""]], D, {"1101": {"close": 30.5}})
    assert rows == [fp.CLOSE_HEAD, [1101, 30.5, "2026-10-05"]]


def test_sync_close_sheet_failure_does_not_raise(monkeypatch):
    monkeypatch.setattr(fp, "SA_KEY_FILE", "/nonexistent/google-sa.json")
    fp.sync_close_sheet(D, {"2330": {"close": 1415.0}})  # 只印失敗訊息，不能拋出拖垮抓價


def test_sync_close_sheet_skips_when_no_prices(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("沒有價格就不該連 Google")
    import gspread
    monkeypatch.setattr(gspread, "service_account", boom)
    fp.sync_close_sheet(D, {})
