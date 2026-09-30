"""測試共用設定。

mops_radar.py import 時會讀 repo 目錄下的 .env（os.environ.setdefault），在 Hermes 上那份是真的
Telegram／OpenRouter／TypeSafe 金鑰。所以這裡一定要在 import 之前先把環境變數蓋成假值，
而且 autouse fixture 會擋掉所有對外連線——沒 mock 到的路徑寧可測試失敗，也不能真的送出訊息。
"""
import os
import pathlib
import sys
import urllib.request

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent


def _read_env_file(key):
    env = REPO / ".env"
    if not env.exists():
        return ""
    for line in env.read_text(encoding="utf-8").splitlines():
        line = line.strip().replace("export ", "", 1)
        if line.startswith(key + "="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    return ""


# live 測試（真打 Jev）要用的金鑰，先存起來再把環境變數蓋掉
LIVE_TYPESAFE_KEY = os.environ.get("TYPESAFE_API_KEY") or _read_env_file("TYPESAFE_API_KEY")

os.environ.update(OPENROUTER_KEY="test-openrouter", TELEGRAM_TOKEN="test-telegram",
                  TELEGRAM_CHAT_ID="0", TYPESAFE_API_KEY="")
os.environ.pop("AI_MODELS", None)
sys.path.insert(0, str(REPO))

import mops_radar  # noqa: E402


def pytest_addoption(parser):
    parser.addoption("--live", action="store_true", help="跑會真的呼叫 TypeSafe Jev 的測試")


def pytest_collection_modifyitems(config, items):
    if config.getoption("--live"):
        return
    skip = pytest.mark.skip(reason="需要 --live（會真的呼叫 TypeSafe Jev）")
    for item in items:
        if "live" in item.keywords:
            item.add_marker(skip)


@pytest.fixture(autouse=True)
def isolated(request, monkeypatch, tmp_path):
    """每個測試：cache／.last_sent 寫到暫存目錄、不真的睡、清掉失效模型紀錄、擋掉對外連線"""
    m = mops_radar
    monkeypatch.setattr(m, "CACHE_FILE", tmp_path / "pending_results.json")
    monkeypatch.setattr(m, "LAST_SENT_FILE", tmp_path / ".last_sent")
    monkeypatch.setattr(m.time, "sleep", lambda s: None)
    monkeypatch.setattr(m, "_dead_models", set())
    if "live" not in request.keywords:
        def no_network(*a, **k):
            raise AssertionError("測試不該對外連線，請 mock 這條路徑")
        monkeypatch.setattr(urllib.request, "urlopen", no_network)
    return m


@pytest.fixture
def m(isolated):
    return isolated
