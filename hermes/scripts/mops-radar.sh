#!/bin/bash
export HOME=/Users/iroman
export PYTHONPATH="$HOME/Library/Python/3.9/lib/python/site-packages:$PYTHONPATH"
cd "$HOME"
LOG=~/mops-radar-run.log
{ echo "===== $(date '+%Y-%m-%d %H:%M:%S') ====="; /usr/bin/python3 "$HOME/.hermes/scripts/mops_radar_runner.py" scan; } >> "$LOG" 2>&1
