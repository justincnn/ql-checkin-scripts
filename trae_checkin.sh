#!/bin/bash
# Trae 每日签到 + TG 通知（青龙容器）
# 依赖青龙已配置的 TG_* env（env 表 id 3-6）与 mihomo docker 网桥代理
cd /ql/data/scripts || exit 1
OUT=$(python3 checkin.py 2>&1)
echo "$OUT"

TB="${TG_BOT_TOKEN:-}"
TU="${TG_USER_ID:-}"
PH="${TG_PROXY_HOST:-172.17.0.1}"
PP="${TG_PROXY_PORT:-7890}"
# 取结果后段（去掉调试冗长行），最多 ~12 行
BRIEF=$(printf '%s\n' "$OUT" | grep -E 'Sign|签|限流|积分|状态|领取|成功|Trae' | tail -n 8)
MSG="【Trae 每日签到】$(date '+%Y-%m-%d %H:%M')
$BRIEF"

if [ -n "$TB" ] && [ -n "$TU" ]; then
  curl -s --proxy "http://${PH}:${PP}" --data-urlencode "chat_id=${TU}" \
    --data-urlencode "text=${MSG}" \
    "https://api.telegram.org/bot${TB}/sendMessage" >/dev/null 2>&1
fi
exit 0