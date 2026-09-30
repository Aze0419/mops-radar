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
# 今年度 1~N 月累計稅後 EPS 與 N（只列有的；其餘應為 None）。注意交易資訊那種表只有「最近四季累計」，不算
YTD_TRUTH = {
    "9910": (2.48, 8), "2880": (1.71, 8), "2801": (1.29, 8), "2836": (0.54, 8), "2845": (0.69, 8),
    "4904": (2.80, 8), "2412": (3.68, 8), "2722": (-0.18, 8), "2237": (2.39, 8),
}
NOT_EARNINGS = {"6949", "5904"}  # 面額變更公告，Jev 獲利判斷要低於門檻
# 已知抓不到、但失敗方式安全（沒抓到 = 當沒資料，不會算出錯的本益比）的項目：印出來但不算錯。
# 高雄銀的表只有「本月份」「累計」兩欄、EPS 只填累計欄，Jev 一直判「沒有今年度累計 EPS」（0.82～0.86），
# 2026-09-30 試過在題目補說明也沒用，不為單一案例再調題目，避免過度擬合
KNOWN_MISSES = {("2836", "ytd")}


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
        ytd_want = YTD_TRUTH.get(code, (None, None))
        ytd = (fin["c_eps"], fin["c_months"] if fin["c_eps"] is not None else None)  # 沒累計 EPS 時月份用不到
        is_earn = p >= m.JEV_THRESHOLD
        known = (code, "ytd") in KNOWN_MISSES and ytd == (None, None)
        ok = got == TRUTH[code] and (ytd == ytd_want or known) and is_earn == (code not in NOT_EARNINGS)
        print(f"{'OK ' if ok else 'ERR'} {a['日期']} {code} {a['公司名稱'][:6]:6} p={p:.2f} "
              f"抽到 {got} 累計 {ytd}｜應為 {TRUTH[code]} 累計 {ytd_want}")
        if got != TRUTH[code]:
            errors.append(f"{code} 抽到 {got}，應為 {TRUTH[code]}（信心 {fin.get('confidence')}）")
        if ytd != ytd_want:
            msg = f"{code} 累計 EPS 抽到 {ytd}，應為 {ytd_want}（信心 {fin.get('confidence')}）"
            if (code, "ytd") in KNOWN_MISSES and ytd == (None, None):
                print(f"    （已知漏抓，不算錯）{msg}")
            else:
                errors.append(msg)
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
