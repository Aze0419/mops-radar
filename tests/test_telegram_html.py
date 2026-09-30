"""Telegram HTML 跳脫：送出去的字串一定要過得了 Telegram parse_mode=HTML"""
import io
import re
import urllib.error

import pytest


def telegram_html_error(t):
    """照 Telegram HTML 規則檢查：& 只能是實體、< 只能是 <b>／</b>、標籤成對不交錯；合格回 None"""
    if re.search(r'&(?!(lt|gt|amp|quot);|#\d+;|#x[0-9a-fA-F]+;)', t):
        return "裸 &"
    depth = 0
    for mt in re.finditer(r'<[^>]*>?', t):
        tag = mt.group()
        if tag not in ("<b>", "</b>"):
            return f"不允許的 {tag!r}"
        depth += -1 if tag == "</b>" else 1
        if depth not in (0, 1):
            return "巢狀或多閉合"
    return None if depth == 0 else "沒閉合"


@pytest.mark.parametrize("src, want", [
    ("<b>🔴 強烈買進 - 大量(3167)</b>\n• EPS：1.68元", "<b>🔴 強烈買進 - 大量(3167)</b>\n• EPS：1.68元"),
    ("營收&獲利雙成長", "營收&amp;獲利雙成長"),
    ("本益比<20、EPS>0", "本益比&lt;20、EPS&gt;0"),
    ("<b>重點：營收大增", "重點：營收大增"),
    ("第一段<br>第二段<br/>第三段", "第一段\n第二段\n第三段"),
    ("<p>段落</p><ul><li>項目</li></ul>", "段落項目"),
    ("本益比&lt;20 &amp; 題材", "本益比&lt;20 &amp; 題材"),  # AI 自己寫的實體不會跳脫兩次
    ("<b>外<b>內</b></b>", "外內"),
    ("<B>大寫</B>", "<b>大寫</b>"),
    ("</b>先關<b>後開", "先關後開"),
    ("R&D 投入 <b>AI</b> 伺服器 & PCB", "R&amp;D 投入 <b>AI</b> 伺服器 &amp; PCB"),
    ("", ""),
    (None, ""),
])
def test_ai_html_to_telegram(m, src, want):
    got = m.ai_html_to_telegram(src)
    assert got == want
    assert telegram_html_error(got) is None


def test_render_block_escapes_every_field(m):
    blk = m._render_block({
        "ann": {"公司代號": "1234", "公司名稱": "A&B<科技>", "發言日期": "2026-09-29",
                "發言時間": "10:00:00", "符合條款": "第51款"},
        "price": 10.5, "volume_lots": None, "ai": {"display_text": "營收&獲利 EPS<0 <b>小心</b>"}})
    assert telegram_html_error(blk) is None
    assert "A&amp;B&lt;科技&gt;" in blk and "無資料" in blk


def _err(code, body):
    return urllib.error.HTTPError("u", code, "x", {}, io.BytesIO(body.encode()))


@pytest.fixture
def telegram(m, monkeypatch):
    rec = {"sent": [], "steps": []}

    def fake(url, payload):
        rec["sent"].append(payload)
        s = rec["steps"].pop(0) if rec["steps"] else None
        if s:
            raise s
    monkeypatch.setattr(m, "_telegram_post", fake)
    return rec


def test_parse_error_falls_back_to_plain_text(m, telegram):
    telegram["steps"] += [_err(400, '{"ok":false,"description":"Bad Request: can\'t parse entities: Unclosed start tag"}')]
    m.send_telegram("<b>粗體 &amp; 文字")
    first, second = telegram["sent"]
    assert first["parse_mode"] == "HTML"
    assert "parse_mode" not in second and second["text"] == "粗體 & 文字"


@pytest.mark.parametrize("code, body", [(400, '{"description":"Bad Request: chat not found"}'), (500, "oops")])
def test_other_errors_still_raise(m, telegram, code, body):
    telegram["steps"] += [_err(code, body)]
    with pytest.raises(urllib.error.HTTPError) as e:
        m.send_telegram("x")
    assert e.value.code == code and len(telegram["sent"]) == 1
