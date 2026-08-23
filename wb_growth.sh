#!/bin/bash
# WorkBuddy 成长中心（Buddy 旅行/盲盒/任务奖）每日自动执行 + TG 通知
# 依赖青龙 env: WORKBUDDY_AUTH_FILE（auth 文件路径）、TG_*（推送）
cd /ql/data/scripts || exit 1
OUT=$(python3 wb_growth_signin.py growth 2>&1)
echo "$OUT"

TB="${TG_BOT_TOKEN:-}"; TU="${TG_USER_ID:-}"
PH="${TG_PROXY_HOST:-172.17.0.1}"; PP="${TG_PROXY_PORT:-7890}"
BRIEF=$(printf '%s\n' "$OUT" | grep -oE '"report":"[^"]*"' | head -1 | sed 's/"report":"//; s/"$//')
if [ -z "$BRIEF" ]; then BRIEF=$(printf '%s\n' "$OUT" | tail -n 1); fi
MSG="【WorkBuddy 成长中心】$(date '+%m-%d %H:%M')
$BRIEF"
if [ -n "$TB" ] && [ -n "$TU" ]; then
  curl -s --proxy "http://${PH}:${PP}" --data-urlencode "chat_id=${TU}" \
    --data-urlencode "text=${MSG}" \
    "https://api.telegram.org/bot${TB}/sendMessage" >/dev/null 2>&1
fi
exit 0