"""send 逐筆記進度、scan 帶著沒做完的 item、scan 失敗與 scan 沒跑的通知"""
import pytest


def item(code, rating, text="x" * 2500):
    return {"ann": {"公司代號": code, "公司名稱": "N" + code, "發言日期": "2026-09-29",
                    "發言時間": "10:00:00", "符合條款": "第51款"},
            "price": 10, "volume_lots": 1, "pe": {},
            "ai": {"ai_rating": rating, "display_text": text, "model_used": "google/gemini-3.1-flash-lite-preview"}}


def ann(code, date="2026-09-30"):
    return {"公司代號": code, "公司名稱": "N" + code, "發言日期": date, "發言時間": "09:00:00",
            "主旨": "s", "符合條款": "第51款", "事實發生日": "", "說明": "每股盈餘 1.00"}


@pytest.fixture
def calls(m, monkeypatch):
    """假的 Sheet／歷史紀錄／Telegram，記錄呼叫；fail 可指定哪一則 Telegram 或哪一檔 Sheet 要失敗"""
    rec = {"gsheet": [], "history": [], "tg": [], "fail_tg_at": None, "fail_gsheet": None}

    def gsheet(a, *rest):
        if a["公司代號"] == rec["fail_gsheet"]:
            raise RuntimeError("sheet boom")
        rec["gsheet"].append(a["公司代號"])

    def tg(text):
        if rec["fail_tg_at"] is not None and len(rec["tg"]) == rec["fail_tg_at"]:
            raise RuntimeError("tg 400")
        rec["tg"].append(text)

    monkeypatch.setattr(m, "sync_gsheet", gsheet)
    monkeypatch.setattr(m, "sync_history", lambda a, price: rec["history"].append(a["公司代號"]))
    monkeypatch.setattr(m, "send_telegram", tg)
    return rec


def reset(rec):
    for k in ("gsheet", "history", "tg"):
        rec[k].clear()


def test_telegram_fails_midway_resend_skips_done_steps(m, calls):
    m._save_cache({"empty": None, "items": [item("1111", "🔴 強烈買進"), item("2222", "🟠 建議買進"),
                                            item("3333", "🟡 一般觀望")]})
    calls["fail_tg_at"] = 1  # 每筆 2500 字 → 3 則，第 2 則失敗
    with pytest.raises(RuntimeError):
        m.send_results()
    assert calls["gsheet"] == ["1111", "2222"] and calls["history"] == ["1111"] and len(calls["tg"]) == 1
    assert [i.get("tg_sent", False) for i in m._load_cache()["items"]] == [True, False, False]

    reset(calls)
    calls["fail_tg_at"] = None
    assert m.send_results() is True
    assert calls["gsheet"] == [] and calls["history"] == [], "重送不該再寫 Sheet"
    assert len(calls["tg"]) == 2 and "2222" in calls["tg"][0] and "3333" in calls["tg"][1]
    assert "1111" not in "".join(calls["tg"])
    assert not m.CACHE_FILE.exists()


def test_sheet_failure_keeps_only_that_item(m, calls):
    calls["fail_gsheet"] = "2222"
    m._save_cache({"empty": None, "items": [item("1111", "🔴 強烈買進"), item("2222", "🟠 建議買進")]})
    assert m.send_results() is False
    left = m._load_cache()["items"]
    assert [i["ann"]["公司代號"] for i in left] == ["2222"] and left[0]["tg_sent"]
    assert len(calls["tg"]) == 2


def test_next_scan_carries_unfinished_item_and_send_only_fixes_sheet(m, calls, monkeypatch):
    left = item("2222", "🟠 建議買進")
    left.update(tg_sent=True, history_done=True)
    m._save_cache({"empty": None, "items": [left]})
    monkeypatch.setattr(m, "fetch_announcements", lambda *a: [])
    m.scan()
    c = m._load_cache()
    assert c["empty"] and [i["ann"]["公司代號"] for i in c["items"]] == ["2222"] and c["items"][0]["carried"]
    assert m.send_results() is True
    assert calls["gsheet"] == ["2222"] and len(calls["tg"]) == 1 and "沒有公告" in calls["tg"][0]
    assert not m.CACHE_FILE.exists()


def test_unsent_items_merge_into_next_send_with_title_note(m, calls, monkeypatch):
    m._save_cache({"empty": None, "items": [item("4444", "🟡 一般觀望")]})
    monkeypatch.setattr(m, "fetch_announcements", lambda *a: [ann("5555")])
    monkeypatch.setattr(m, "fetch_prices", lambda codes: {"5555": {"close": 20, "volume": 1000}})
    monkeypatch.setattr(m, "analyze", lambda *a, **k: {"ai_rating": "🟡 一般觀望", "display_text": "ok",
                                                       "model_used": m.AI_MODEL})
    monkeypatch.setattr(m, "_get_dashboard_row", lambda c: None)
    m.scan()
    assert m.send_results() is True
    assert len(calls["tg"]) == 1
    assert "4444" in calls["tg"][0] and "5555" in calls["tg"][0] and "含前次沒送出的 1 筆" in calls["tg"][0]


def test_scan_failure_is_reported_escaped_and_carried_items_still_sent(m, calls, monkeypatch):
    m._save_cache({"empty": None, "items": [item("6666", "🟡 一般觀望")]})

    def boom(*a, **k):
        raise OSError("<urlopen error [Errno 8] nodename nor servname provided>")
    monkeypatch.setattr(m, "fetch_announcements", boom)
    with pytest.raises(OSError):
        m.scan()
    c = m._load_cache()
    assert c["error"].startswith("OSError") and [i["ann"]["公司代號"] for i in c["items"]] == ["6666"]
    assert m.send_results() is True
    assert "掃描失敗" in calls["tg"][0] and "&lt;urlopen" in calls["tg"][0] and "<urlopen" not in calls["tg"][0]
    assert "6666" in calls["tg"][1] and not m.CACHE_FILE.exists()


def test_fetch_announcements_retry_empty_day_and_blocked_page(m, monkeypatch):
    seq = []

    def flaky(url, data, **k):
        seq.append(1)
        if len(seq) < 3:
            raise OSError("timeout")
        return "<html>查無115/12/25之重大訊息資料</html>"  # 2026-09-30 實測的查無字樣
    monkeypatch.setattr(m, "http_post", flaky)
    assert m.fetch_announcements("115", "12", "25") == [] and len(seq) == 3

    monkeypatch.setattr(m, "http_post", lambda *a, **k: "<html>FOR SECURITY REASONS, THIS PAGE CAN NOT BE ACCESSED!</html>")
    with pytest.raises(RuntimeError, match="可能被擋"):
        m.fetch_announcements("115", "09", "29")


def test_no_cache_alerts_only_if_not_sent_today(m, calls):
    m.LAST_SENT_FILE.write_text("2000-01-01", encoding="utf-8")
    assert m.send_results() is True
    assert len(calls["tg"]) == 1 and "找不到今天的掃描結果" in calls["tg"][0]

    reset(calls)
    m.LAST_SENT_FILE.write_text(m.datetime.now(m.TZ).date().isoformat(), encoding="utf-8")
    assert m.send_results() is True and calls["tg"] == []
