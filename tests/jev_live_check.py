"""真打 TypeSafe Jev，拿 22 則人工核對過的 MOPS 公告驗 EPS／營收抽取與獲利判斷。

不依賴 pytest，Hermes 上（金鑰在 ~/mops_radar/.env、venv 沒裝 pytest）直接跑：
    cd ~/mops_radar && /Users/iroman/.hermes/hermes-agent/venv/bin/python3 tests/jev_live_check.py
本機有金鑰時也可以 `python -m pytest --live`。改 jev_questions()、number_candidates()、門檻之前先跑一次當基準。
"""
import json
import os
import pathlib
import sys

FIXTURE = pathlib.Path(__file__).resolve().parent / "fixtures" / "mops_eps_announcements.json"

# 人工逐則對原文核對過的正確答案：(單月EPS, 單季EPS, 單月營收 百萬)。2026-09-30 建立
# 金融股（華南金、彰銀、高雄銀）與豐泰只公布累計 EPS，正確答案是「沒有單月 EPS」
TRUTH = {
    "3026": (1.33, 3.87, 1439.76), "2030": (0.26, 1.05, 1175.0), "4770": (1.41, 3.11, 492.0),
    "3653": (7.35, 15.8, 3225.89), "8150": (0.62, 1.28, 2786.0), "5465": (0.15, 0.59, 140.0),
    "4542": (0.61, 1.07, 124.0), "6488": (1.47, 7.9, 4764.0), "2444": (-0.13, -0.84, 122.0),
    "9910": (None, None, None), "4904": (0.34, None, 9547.0), "2722": (0.05, None, 68.16),
    "2880": (None, None, None), "2412": (0.57, None, 23310.0), "2237": (0.16, None, 292.6),
    "6872": (-0.16, -0.62, 0.8), "2801": (None, None, None), "2845": (0.08, None, None),
    "2836": (None, None, None), "6620": (0.02, 0.19, 72.0),
    "6949": (None, None, None), "5904": (None, None, None),
}
NOT_EARNINGS = {"6949", "5904"}  # 面額變更公告，Jev 獲利判斷要低於門檻


def load_fixture():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def run(m):
    """回傳錯誤清單（空 = 全對），並印出每則結果"""
    errors = []
    for a in load_fixture():
        code = a["公司代號"]
        p, fin = m.jev_judge(a)
        if fin is None:
            errors.append(f"{code} Jev 呼叫失敗")
            continue
        got = (fin["m_eps"], fin["q_eps"], fin["m_rev"])
        is_earn = p >= m.JEV_THRESHOLD
        mark = "OK " if got == TRUTH[code] and is_earn == (code not in NOT_EARNINGS) else "ERR"
        print(f"{mark} {a['日期']} {code} {a['公司名稱'][:6]:6} p={p:.2f} 抽到 {got} 應為 {TRUTH[code]}")
        if got != TRUTH[code]:
            errors.append(f"{code} 抽到 {got}，應為 {TRUTH[code]}（信心 {fin.get('confidence')}）")
        if is_earn != (code not in NOT_EARNINGS):
            errors.append(f"{code} 獲利判斷 p={p:.2f} 門檻 {m.JEV_THRESHOLD} 判錯")
    return errors


if __name__ == "__main__":
    # 只真打 Jev：其他金鑰先蓋成假值（mops_radar 讀 .env 用 setdefault，先設的會贏），避免誤送 Telegram
    os.environ.update(OPENROUTER_KEY="unused", TELEGRAM_TOKEN="unused", TELEGRAM_CHAT_ID="0")
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
    import mops_radar
    if not mops_radar.TYPESAFE_KEY:
        sys.exit("沒有 TYPESAFE_API_KEY，無法驗證")
    errs = run(mops_radar)
    print(f"\n{len(load_fixture())} 則，錯 {len(errs)} 則")
    for e in errs:
        print("  ", e)
    sys.exit(1 if errs else 0)
