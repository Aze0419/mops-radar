#!/bin/bash
# OTC 收盤價持續重試保險網（Hermes cron 8238761e0204，*/20 16-23 * * 1-5）
# - 當天已補過（標記檔存在）→ 直接結束
# - fetch_prices.py 本身掛掉（exit ≠ 0）→ 立刻告警
# - TPEx 還沒發布（上櫃 0 筆）→ 靜默結束等下一格，只有最後一格（23:40 後）還沒補到才告警
MARKER="/Users/iroman/mops_radar/.otc_synced_$(date +%Y%m%d)"
if [ -f "$MARKER" ]; then
    exit 0
fi
cd /Users/iroman/mops_radar
OUTPUT=$(/Users/iroman/.hermes/hermes-agent/venv/bin/python3 fetch_prices.py 2>&1)
STATUS=$?
OTC_COUNT=$(echo "$OUTPUT" | awk '/上櫃（TPEX）\.\.\./{found=1} found && /→ [0-9]+ 筆/{print $2; exit}')
if [ "$STATUS" -eq 0 ] && [ -n "$OTC_COUNT" ] && [ "$OTC_COUNT" -gt 0 ]; then
    touch "$MARKER"
    exit 0
fi
if [ "$STATUS" -ne 0 ]; then
    echo "fetch_prices.py 異常結束（exit $STATUS）"
    echo "$OUTPUT"
    exit 1
fi
NOW=$(date +%H%M)
if [ "$NOW" -ge 2340 ]; then
    echo "到 23:40 最後一格仍沒抓到上櫃收盤價（今天整晚 TPEx 都沒發布或一直失敗）"
    echo "$OUTPUT"
    exit 1
fi
exit 0
