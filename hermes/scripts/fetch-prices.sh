#!/bin/bash
export HOME=/Users/iroman
cd "$HOME/mops_radar"
/Users/iroman/.hermes/hermes-agent/venv/bin/python3 fetch_prices.py > /dev/null 2>&1
