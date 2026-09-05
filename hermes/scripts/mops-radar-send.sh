#!/bin/bash
export HOME=/Users/iroman
export PYTHONPATH="$HOME/Library/Python/3.9/lib/python/site-packages:$PYTHONPATH"
cd "$HOME"
LOG=~/mops-radar-send-run.log
{ echo "===== $(date '+%Y-%m-%d %H:%M:%S') ====="; /Users/iroman/.hermes/hermes-agent/venv/bin/python3 "$HOME/.hermes/scripts/mops_radar_runner.py" send; } >> "$LOG" 2>&1
