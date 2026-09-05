---
name: mops-radar-monitor
description: 管理與優化 MOPS 重大公告監控與通知系統的相關流程。
---
# MOPS Radar Monitoring System

管理與優化 MOPS 重大公告監控與通知系統的相關流程。

## 核心流程
- 監控重大公告：監控 TWSE 公告，篩選特定交易款項。
- AI 分析與通知：針對篩選出的公告進行語意分析並發送格式化通知至 Telegram。
- 資料落盤：寫入 Google Sheet 公告紀錄。

## 推薦通知格式 (Telegram)
為了保持訊息高效，統一使用以下結構發送即時通知：

📢【公司名稱｜公司代號】
📅 YYYY-MM-DD HH:MM:SS
💰 收盤價: X | 成交量: Y

<b>關鍵數據：</b>
{關鍵數據摘要}

<b>評分理由與成長動能分析：</b>
{分析內容}

<b>產業熱度評估與風險提醒：</b>
{風險風險摘要}

## 排除原則 (Pitfalls)
- 避免通知「交易資訊」或「面額變更」等無關重大經營分析的公告。
- 嚴格控制輸出訊息長度，避免過於瑣碎。

## 維運與部署
- 若遇到系統異常（如資料來源端點變更導致抓取失敗），務必先檢查 `~/.hermes/scripts/` 下的腳本，因為 cron 任務直接調用這些檔案。
- 修改腳本後，確保對應的 git repo（如 `~/mops_radar`）已 commit 並 push，保持同步。
- **重要環境變數**：若系統涉及 API 呼叫（如 OpenRouter 或 Supabase），請確認 `~/.hermes/.env` 是否已載入，避免 ConnectionRefusedError。
- **Git 整合**：所有排程腳本均需納入 git 版本控制，確保 Hermes 跑的版本是最新且經測試的。
