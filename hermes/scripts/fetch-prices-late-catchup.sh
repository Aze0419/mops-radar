#!/bin/bash
# 15:30 那次 OTC 常常因為 TPEx tpex_mainboard_quotes 端點還沒發布資料而抓到 0 筆，
# 這是晚一點的補跑，重新跑一次抓價 + OTC 技術指標。
set -e
~/.hermes/scripts/fetch-prices.sh
~/.hermes/scripts/module1-otc-catchup.sh
