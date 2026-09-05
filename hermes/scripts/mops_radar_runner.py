#!/usr/bin/env python3
"""
包一層執行 mops_radar.py，處理 Google Drive（CloudStorage FileProvider 掛載）
目錄偶發卡住的兩種症狀：

1. 行程 cwd 落在該掛載目錄時，Python import 機制偶發 InterruptedError
   （[Errno 4] EINTR，出現在 importlib._bootstrap_external._path_importer_cache），
   導致腳本一啟動就炸掉，且跟目錄內檔案多寡、要 import 哪些套件無關
   ——純粹是「cwd 在該掛載點」這件事本身造成的。
   對策：worker 子行程全程不 chdir 進 Google Drive 目錄、也不讓它出現在
   sys.path，只用 open() 以絕對路徑讀出 mops_radar.py 原始碼後 exec()。
   mops_radar.py 內部所有檔案存取（.env、pending_results.json、SA_KEY_FILE）
   本來就是用 __file__ 或絕對路徑解析，不依賴 cwd，這樣執行不影響行為。

2. 就算避開了 cwd，FileProviderExtension 在同步不順時（尤其系統剛睡醒、
   Drive 剛啟動還在追進度那段時間）仍可能讓一般的 open()/read() 直接卡住
   不回應，不會拋例外、就是卡著不動。
   對策：外層用逾時 + 重試包住 worker（macOS 內建 zsh 沒有 timeout 指令，
   改用 subprocess.run(timeout=...) 實作）。

注意：「送出」模式如果卡在寄出 Telegram 訊息「之後」、寫回 pending_results.json
「之前」才逾時，重試會重複寄送/重複寫入 Google Sheet；但目前觀察到的卡住
位置都在 send_results() 一開始讀 pending_results.json 那幾行（送出動作尚未
開始），加上「完全沒送出」對這個每日盯盤用途來說比「偶爾重複一次」更糟，
所以還是選擇重試。
"""
import sys
import os
import subprocess
import time

RADAR_DIR = "/Users/iroman/Library/CloudStorage/GoogleDrive-shih.sa@gmail.com/我的雲端硬碟/01_WORK/MOPS_RADAR"
SCRIPT = os.path.join(RADAR_DIR, "mops_radar.py")

mode = sys.argv[1] if len(sys.argv) > 1 else "scan"

if os.environ.get("_MOPS_RADAR_WORKER") == "1":
    sys.argv = [SCRIPT, mode]
    with open(SCRIPT, encoding="utf-8") as f:
        src = f.read()
    exec(compile(src, SCRIPT, "exec"), {"__name__": "__main__", "__file__": SCRIPT})
    sys.exit(0)

MAX_ATTEMPTS = 4
TIMEOUT_SEC = 240
BACKOFF = [15, 30, 60]

env = dict(os.environ)
env["_MOPS_RADAR_WORKER"] = "1"
home = os.path.expanduser("~")

for attempt in range(1, MAX_ATTEMPTS + 1):
    print(f"--- 第 {attempt}/{MAX_ATTEMPTS} 次嘗試（mode={mode}）---", flush=True)
    try:
        r = subprocess.run(
            [sys.executable, os.path.abspath(__file__), mode],
            cwd=home, env=env, timeout=TIMEOUT_SEC,
        )
        if r.returncode == 0:
            sys.exit(0)
        print(f"失敗，exit code {r.returncode}", flush=True)
    except subprocess.TimeoutExpired:
        print(f"逾時（{TIMEOUT_SEC}s 未完成，可能是 Google Drive 掛載卡住）", flush=True)
    if attempt < MAX_ATTEMPTS:
        wait = BACKOFF[min(attempt - 1, len(BACKOFF) - 1)]
        print(f"{wait}s 後重試...", flush=True)
        time.sleep(wait)

print("已達最大重試次數，放棄。", flush=True)
sys.exit(1)
