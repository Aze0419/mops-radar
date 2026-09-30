"""AI 評級正規化、OpenRouter 重試／熔斷／備援模型"""
import io
import urllib.error

import pytest

P, F1, F2 = "google/gemini-3.1-flash-lite-preview", "google/gemini-2.5-flash-lite", "google/gemini-2.5-flash"


def http_err(code, body='{"error":{"message":"bad"}}'):
    return urllib.error.HTTPError("u", code, "x", {}, io.BytesIO(body.encode()))


def ok(model=P, content='{"ai_rating": "🔴 強烈買進", "display_text": "x"}'):
    return {"model": model, "choices": [{"message": {"content": content}}]}


@pytest.fixture
def openrouter(m, monkeypatch):
    """依序回傳／拋出 steps；記錄每次送出的 models 陣列與 timeout"""
    rec = {"models": [], "timeouts": [], "steps": []}

    def fake(url, payload, headers=None, timeout=60):
        rec["models"].append(payload["models"])
        rec["timeouts"].append(timeout)
        s = rec["steps"].pop(0)
        if isinstance(s, Exception):
            raise s
        return s
    monkeypatch.setattr(m, "http_post_json", fake)
    return rec


@pytest.fixture
def sleeps(m, monkeypatch):
    rec = []
    monkeypatch.setattr(m.time, "sleep", rec.append)
    return rec


@pytest.mark.parametrize("ai, want", [
    ({"ai_rating": "🔴 強烈買進"}, "🔴 強烈買進"),
    ({"ai_rating": "🔴強烈買進"}, "🔴 強烈買進"),
    ({"ai_rating": "🔴 強烈買進（路徑A）"}, "🔴 強烈買進"),
    ({"ai_rating": "強烈買進"}, "🔴 強烈買進"),
    ({"ai_rating": "🟠建議買進"}, "🟠 建議買進"),
    ({"ai_rating": " 🟢 需要小心 "}, "🟢 需要小心"),
    ({"ai_rating": "", "display_text": "<b>🟠 建議買進 - 大量(3167)</b>\n..."}, "🟠 建議買進"),
    ({"display_text": "AI分析失敗"}, "🟡 一般觀望"),
    ({"ai_rating": None}, "🟡 一般觀望"),
    ({"ai_rating": "建議買進", "display_text": "<b>🔴 強烈買進</b>"}, "🟠 建議買進"),  # ai_rating 欄位優先
])
def test_normalize_rating(m, ai, want):
    assert m.normalize_rating(ai) == want


def test_default_model_order(m):
    assert m.AI_MODELS == [P, F1, F2] and m.AI_MODEL == P


def test_retryable_errors_then_success(m, openrouter, sleeps):
    openrouter["steps"] += [http_err(429), http_err(503), ok()]
    assert m.openrouter_chat([])[1] == P
    assert len(openrouter["models"]) == 3 and sleeps == [5, 15] and openrouter["timeouts"][0] == 60
    assert openrouter["models"][0] == [P, F1, F2]


def test_400_not_retried_and_body_kept(m, openrouter):
    openrouter["steps"] += [http_err(400, '{"error":{"message":"context too long"}}')]
    with pytest.raises(RuntimeError) as e:
        m.openrouter_chat([])
    assert not isinstance(e.value, m.AIUnavailable)
    assert "HTTP 400" in str(e.value) and "context too long" in str(e.value)
    assert len(openrouter["models"]) == 1 and not m._dead_models


def test_connection_errors_exhaust_retries(m, openrouter):
    openrouter["steps"] += [urllib.error.URLError("timed out"), TimeoutError("read"), ConnectionResetError("reset")]
    with pytest.raises(m.AIUnavailable, match="連線失敗"):
        m.openrouter_chat([])
    assert len(openrouter["models"]) == 3


def test_200_without_choices_is_retried(m, openrouter):
    openrouter["steps"] += [{"error": {"message": "upstream"}}, ok()]
    assert m.openrouter_chat([])[0]
    assert len(openrouter["models"]) == 2


def test_invalid_model_id_pruned_immediately_and_remembered(m, openrouter, sleeps):
    # 2026-09-30 實測：models 陣列裡有無效 ID，OpenRouter 整個 request 回 400，不會自己跳
    openrouter["steps"] += [http_err(400, f'{{"error":{{"message":"{P} is not a valid model ID","code":400}}}}'), ok(F1)]
    assert m.openrouter_chat([]) == ('{"ai_rating": "🔴 強烈買進", "display_text": "x"}', F1)
    assert openrouter["models"] == [[P, F1, F2], [F1, F2]] and sleeps == []
    openrouter["steps"] += [ok(F1)]
    m.openrouter_chat([])
    assert openrouter["models"][-1] == [F1, F2], "同一次執行後面的公告直接略過失效模型"


def test_no_endpoints_found_also_pruned(m, openrouter):
    openrouter["steps"] += [http_err(404, f'{{"error":{{"message":"No endpoints found for {P}."}}}}'), ok(F1)]
    assert m.openrouter_chat([])[1] == F1 and m._dead_models == {P}


def test_all_models_dead(m, openrouter, monkeypatch):
    monkeypatch.setattr(m, "_dead_models", {P, F1, F2})
    with pytest.raises(RuntimeError, match="全部失效"):
        m.openrouter_chat([])
    assert openrouter["models"] == []


def _scan_five(m, monkeypatch, analyze):
    anns = [{"公司代號": str(1000 + i), "公司名稱": "N", "發言日期": "2026-09-29", "發言時間": "10:00:00",
             "主旨": "s", "符合條款": "第51款", "事實發生日": "", "說明": "每股盈餘 1.00"} for i in range(5)]
    monkeypatch.setattr(m, "fetch_announcements", lambda *a: anns)
    monkeypatch.setattr(m, "fetch_prices", lambda codes: {})
    monkeypatch.setattr(m, "_get_dashboard_row", lambda c: None)
    # 有年增率，才不會被 enforce_growth_data 降級（這裡測的是熔斷與正規化）
    monkeypatch.setattr(m, "regex_financials", lambda d: {"m_eps": 1.0, "m_yoy": 10.0, "q_eps": None, "m_rev": 100.0,
                                                          "r_yoy": 5.0, "c_eps": None, "c_months": None, "source": "regex"})
    monkeypatch.setattr(m, "analyze", analyze)
    m.scan()
    return m._load_cache()["items"]


def test_circuit_breaker_after_two_consecutive_outages(m, monkeypatch):
    n = []

    def down(*a, **k):
        n.append(1)
        raise m.AIUnavailable("down")
    items = _scan_five(m, monkeypatch, down)
    assert len(n) == 2 and len(items) == 5
    assert all(i["ai"]["ai_rating"] == "🟡 一般觀望" for i in items) and "未重試" in items[4]["ai"]["display_text"]


def test_400_does_not_trip_breaker_success_resets_and_rating_normalized(m, monkeypatch):
    seq = [RuntimeError("HTTP 400"), m.AIUnavailable("down"),
           {"ai_rating": "🔴強烈買進", "display_text": "x", "model_used": P},
           m.AIUnavailable("down"), {"ai_rating": "建議買進", "display_text": "y", "model_used": F1}]
    n = []

    def mixed(*a, **k):
        n.append(1)
        s = seq.pop(0)
        if isinstance(s, Exception):
            raise s
        return s
    items = _scan_five(m, monkeypatch, mixed)
    assert len(n) == 5
    assert [i["ai"]["ai_rating"] for i in items] == ["🟡 一般觀望", "🟡 一般觀望", "🔴 強烈買進", "🟡 一般觀望", "🟠 建議買進"]


def test_title_notes_backup_model_only_when_used(m, monkeypatch):
    sent = []
    monkeypatch.setattr(m, "send_telegram", sent.append)

    def it(code, model):
        return {"ann": {"公司代號": code, "公司名稱": "N", "發言日期": "d", "發言時間": "t", "符合條款": "c"},
                "price": 1, "volume_lots": 1, "pe": {},
                "ai": {"ai_rating": "🟡 一般觀望", "display_text": "x", "model_used": model}}
    m._save_cache({"empty": None, "items": [it("1", P), it("2", F1)]})
    m.send_results()
    assert f"其中 1 筆由備援模型 {F1} 分析" in sent[0] and P in sent[0]

    sent.clear()
    m._save_cache({"empty": None, "items": [it("1", P)]})
    m.send_results()
    assert "備援" not in sent[0]


@pytest.mark.parametrize("rating, eps_yoy, rev_yoy, want", [
    ("🔴 強烈買進", None, None, "🟡 一般觀望"),   # 彰銀、豐泰：只有今年累計 EPS
    ("🔴 強烈買進", None, 12.0, "🟠 建議買進"),   # 有營收成長可以留在建議買進
    ("🔴 強烈買進", None, -3.0, "🟡 一般觀望"),
    ("🟠 建議買進", None, None, "🟡 一般觀望"),   # 華南金、遠東銀
    ("🟠 建議買進", None, 8.0, "🟠 建議買進"),    # 營收有成長，建議買進成立
    ("🔴 強烈買進", 285.0, 33.0, "🔴 強烈買進"),  # 資料齊全不動
    ("🔴 強烈買進", -20.0, 5.0, "🔴 強烈買進"),   # 年增率有值但 AI 判斷不同（例如轉虧為盈）不在這裡改
    ("🟡 一般觀望", None, None, "🟡 一般觀望"),
    ("🟢 需要小心", None, None, "🟢 需要小心"),
])
def test_enforce_growth_data(m, rating, eps_yoy, rev_yoy, want):
    ai = {"ai_rating": rating, "display_text": "<b>分析</b>"}
    note = m.enforce_growth_data(ai, {"pre_monthly_eps_yoy": eps_yoy, "pre_monthly_revenue_yoy": rev_yoy})
    assert ai["ai_rating"] == want
    if want == rating:
        assert note is None and ai["display_text"] == "<b>分析</b>"
    else:
        assert rating in note and want in note and ai["display_text"].endswith(note)


def test_prompt_does_not_ask_for_web_search(m):
    # OpenRouter request 沒開 web search，prompt 要求搜尋只會逼 AI 編新聞（2026-09-30 拿掉）
    p = m.SYSTEM_PROMPT
    assert "必須搜尋" not in p and "搜尋確認" not in p and "最新網路資訊" not in p
    assert "你沒有網路搜尋能力" in p and "公告未說明原因" in p
