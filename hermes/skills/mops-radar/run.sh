#!/bin/bash
# 一定要明講 scan 或 send：mops_radar.py 沒帶參數預設是 scan，
# 歷史上 send 腳本掉了參數，結果每天靜靜重跑 scan、從沒送出過
cd "$HOME/mops_radar"
/Users/iroman/.hermes/hermes-agent/venv/bin/python3 mops_radar.py "${1:?用法：run.sh scan|send}"
