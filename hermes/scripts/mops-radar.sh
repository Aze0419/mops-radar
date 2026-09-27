#!/bin/bash
export HOME=/Users/iroman
cd "$HOME/mops_radar"
LOG=~/mops-radar-run.log
{ echo "===== $(date '+%Y-%m-%d %H:%M:%S') ====="; /Users/iroman/.hermes/hermes-agent/venv/bin/python3 mops_radar.py scan; } >> "$LOG" 2>&1
