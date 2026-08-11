#!/bin/bash
export HOME=/Users/iroman
export PYTHONPATH="$HOME/Library/Python/3.9/lib/python/site-packages:$PYTHONPATH"
cd "/Users/iroman/Library/CloudStorage/GoogleDrive-shih.sa@gmail.com/我的雲端硬碟/01_WORK/MOPS_RADAR"
LOG=~/mops-radar-send-run.log
{ echo "===== $(date '+%Y-%m-%d %H:%M:%S') ====="; /usr/bin/python3 mops_radar.py send; } >> "$LOG" 2>&1
