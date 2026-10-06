#!/usr/bin/env python3
"""从青龙 DB 读 TG 配置, 把任务日志尾部发到 Telegram.
用法: tg_send.py <标题> <日志文件>
也支持环境变量覆盖 (TG_BOT_TOKEN/TG_USER_ID/TG_PROXY_HOST/TG_PROXY_PORT)。
"""
import json
import os
import sqlite3
import sys
import urllib.request

DB = "/ql/data/db/database.sqlite"


def env_or_db(name):
    v = os.environ.get(name)
    if v:
        return v
    try:
        c = sqlite3.connect(DB)
        row = c.execute("SELECT value FROM Envs WHERE name=?", (name,)).fetchone()
        c.close()
        return row[0] if row else ""
    except Exception:
        return ""


def main():
    if len(sys.argv) < 3:
        print("usage: tg_send.py <标题> <日志文件>")
        sys.exit(1)
    name, logfile = sys.argv[1], sys.argv[2]

    token = env_or_db("TG_BOT_TOKEN")
    user_id = env_or_db("TG_USER_ID")
    if not token or not user_id:
        print("[tg_send] 缺 TG_BOT_TOKEN/TG_USER_ID, 跳过")
        return

    proxy = None
    host, port = env_or_db("TG_PROXY_HOST"), env_or_db("TG_PROXY_PORT")
    if host and port:
        proxy = f"http://{host}:{port}"

    with open(logfile, encoding="utf-8", errors="ignore") as f:
        lines = f.read().strip().splitlines()
    tail = "\n".join(lines[-25:]).strip() or "(无输出)"
    text = f"✅ {name}\n\n{tail[:3000]}"  # TG 单消息上限 ~4096

    handlers = []
    if proxy:
        handlers.append(urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
    handlers.append(urllib.request.ProxyHandler({}))
    opener = urllib.request.build_opener(*handlers)

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    body = json.dumps({"chat_id": user_id, "text": text, "disable_web_page_preview": True}).encode()
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
    try:
        with opener.open(req, timeout=30) as resp:
            print(f"[tg_send] 推送成功 HTTP {resp.status}")
    except Exception as e:
        print(f"[tg_send] 推送失败: {e}")


if __name__ == "__main__":
    main()