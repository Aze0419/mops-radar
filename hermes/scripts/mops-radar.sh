#!/bin/bash
export HOME=/Users/iroman
cd "$HOME"
LOG=~/mops-radar-run.log
{ echo "===== $(date '+%Y-%m-%d %H:%M:%S') ====="; /Users/iroman/.hermes/hermes-agent/venv/bin/python3 "$HOME/.hermes/scripts/mops_radar_runner.py" scan; } >> "$LOG" 2>&1
