#!/bin/bash
# 青龙任务外包装: 跑完任务,把日志尾部推送到 TG (TG 配置直接从青龙 DB 读, 不依赖面板 env)
# cron command 用法: bash /ql/data/scripts/notify_wrap.sh "任务名" task 脚本文件名
set -o pipefail
# 关键修复: cron 的 no_tee=true 会让 task 输出被青龙重定向到自己的日志文件, wrap 的 tee 管道读到空 → TG 推"(无输出)"
unset no_tee real_time
NAME="$1"
shift
LOG=$(mktemp /tmp/wrap.XXXXXX.log)
"$@" 2>&1 | tee "$LOG"
RC=${PIPESTATUS[0]}
python3 /ql/data/scripts/tg_send.py "$NAME" "$LOG"
tail -5 "$LOG" > /dev/null  # 保持日志落盘
exit "$RC"