#!/bin/bash
export HOME=/Users/iroman
cd "$HOME/mops_radar"
LOG=~/mops-radar-send-run.log
{ echo "===== $(date '+%Y-%m-%d %H:%M:%S') ====="; /Users/iroman/.hermes/hermes-agent/venv/bin/python3 mops_radar.py send; } >> "$LOG" 2>&1
