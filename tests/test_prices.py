"""股價過期警告：Supabase 最新一筆比最新交易日舊時，要讓 AI 與 Telegram 都看得到"""
import datetime as dt
import io
import json
import urllib.request

import pytest

from test_telegram_html import telegram_html_error


@pytest.mark.parametrize("today, closed, want", [
    ("2026-10-01", set(), "2026-09-30"),                      # 週四 → 週三
    ("2026-10-05", set(), "2026-10-02"),                      # 週一 → 上週五
    ("2026-10-12", {"2026-10-09"}, "2026-10-08"),             # 週一、上週五休市 → 週四
    ("2026-10-06", {"2026-10-05"}, "2026-10-02"),             # 週二、週一休市 → 上週五
])
def test_last_trading_day(m, today, closed, want):
    assert m.last_trading_day(dt.date.fromisoformat(today), closed).isoformat() == want


def test_twse_closed_days_parses_roc_dates_and_skips_trading_days(m, monkeypatch):
    rows = [{"Date": "1151009", "Name": "國慶日補假"}, {"Date": "1150102", "Name": "開始交易日"},
            {"Date": "1151231", "Name": "最後交易日"}, {"Date": "bad", "Name": "壞資料"}]
    monkeypatch.setattr(urllib.request, "urlopen",
                        lambda *a, **k: io.BytesIO(json.dumps(rows, ensure_ascii=False).encode()))
    assert m._twse_closed_days() == {"2026-10-09"}


def test_twse_closed_days_failure_falls_back_to_weekends_only(m):
    assert m._twse_closed_days() == set()  # conftest 擋掉對外連線 → 查詢失敗


def test_scan_flags_stale_price_for_ai_and_cache(m, monkeypatch):
    anns = [{"公司代號": c, "公司名稱": "N" + c, "發言日期": "2026-09-29", "發言時間": "10:00:00", "主旨": "s",
             "符合條款": "第51款", "事實發生日": "", "說明": "每股盈餘 1.00"} for c in ("1111", "2222", "3333")]
    monkeypatch.setattr(m, "fetch_announcements", lambda *a: anns)
    monkeypatch.setattr(m, "fetch_prices", lambda codes: {
        "1111": {"close": 10, "volume": 1000, "date": "2000-01-03"},   # 很舊 → 過期
        "2222": {"close": 20, "volume": 1000, "date": "2999-01-01"},   # 不可能過期
    })                                                                  # 3333 沒股價
    monkeypatch.setattr(m, "_twse_closed_days", set)
    monkeypatch.setattr(m, "_get_dashboard_row", lambda c: None)
    seen = {}

    def fake_analyze(ann, price, pe, dashboard=None, price_date=None, expected_day=None):
        seen[ann["公司代號"]] = (price_date, expected_day)
        return {"ai_rating": "🟡 一般觀望", "display_text": "x", "model_used": m.AI_MODEL}
    monkeypatch.setattr(m, "analyze", fake_analyze)
    m.scan()

    expected = m.last_trading_day(m.datetime.now(m.TZ).date(), set()).isoformat()
    assert seen == {"1111": ("2000-01-03", expected), "2222": ("2999-01-01", None), "3333": (None, None)}
    items = {i["ann"]["公司代號"]: i for i in m._load_cache()["items"]}
    assert items["1111"]["price_expected"] == expected and items["1111"]["price_date"] == "2000-01-03"
    assert items["2222"]["price_expected"] is None and items["3333"]["price_expected"] is None


def _item(**extra):
    return {"ann": {"公司代號": "1111", "公司名稱": "N", "發言日期": "2026-09-29", "發言時間": "10:00:00",
                    "符合條款": "第51款"}, "price": 10, "volume_lots": 1,
            "ai": {"display_text": "x"}, **extra}


def test_render_block_shows_stale_warning(m):
    blk = m._render_block(_item(price_date="2026-09-26", price_expected="2026-09-29"))
    assert "⚠️ 這是 09/26 的收盤價，最新交易日應為 09/29" in blk and telegram_html_error(blk) is None


@pytest.mark.parametrize("extra", [{}, {"price_date": "2026-09-29", "price_expected": None}])
def test_render_block_no_warning_when_fresh_or_old_cache(m, extra):
    assert "⚠️" not in m._render_block(_item(**extra))


def test_analyze_prompt_mentions_price_date(m, monkeypatch):
    sent = {}

    def fake_chat(messages):
        sent["user"] = messages[1]["content"]
        return '{"ai_rating": "🟡 一般觀望", "display_text": "x"}', m.AI_MODEL
    monkeypatch.setattr(m, "openrouter_chat", fake_chat)
    ann = {"公司名稱": "N", "公司代號": "1111", "說明": "每股盈餘 1.00"}
    fin = {"m_eps": 1.0, "q_eps": None, "m_yoy": 5.0, "m_rev": None, "r_yoy": None, "source": "jev"}
    pe = m.calc_pe(fin, 10)
    m.analyze(ann, 10, pe, None, price_date="2026-09-26", expected_day="2026-09-29")
    assert "2026-09-26 收盤，不是最新交易日 2026-09-29" in sent["user"]
    m.analyze(ann, 10, pe, None, price_date="2026-09-29")
    assert "股價：10元（2026-09-29 收盤）" in sent["user"] and "不是最新交易日" not in sent["user"]
    assert sent["user"].count("【系統預算値】") == 1
