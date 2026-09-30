# Skill: fetch-prices
description: 每個交易日抓 TWSE+TPEX 收盤價寫回 Supabase stock_prices（飆股雷達、因子選股共用），並存一份 ~/mops_radar/prices.json。14:15 只抓上市（--tse-only），上櫃由 15:45／18:00 補跑與 16~23 點每 20 分鐘的持續重試抓；休市日自動跳過
tags: [stock, price, twse, tpex, supabase]
