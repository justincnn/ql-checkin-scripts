#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AgentRouter 自动签到脚本 (青龙面板 / 任意 Python3 环境)
站点: https://agentrouter.org

===== 原理 (已对线上接口逐项实测确认) =====
本站"签到"= 每日完成一次登录。仅支持账号密码登录:

  向 POST /api/user/login 发送 {username: 邮箱, password: 密码}
  -> 服务端下发 session cookie, 并在 data.checked_in=true 时发放当日额度
  -> 登录响应 data 里直接带 quota(余额), 无需额外查询。
  密码是固定的, 不像第三方会话 cookie 会过期, 基本一劳永逸。

登录成功后还会做一次端到端核验(见下), 确认 /console/log 里确实落了
"签到成功"日志, 才会报"日志已确认"。

===== 配置方式 =====
单账号:
  AGENTROUTER_ACCOUNT  必填. 格式: 邮箱#密码  (用 # 隔开)
    例: zj773075692@gmail.com#你的密码

多账号(可选):
  AGENTROUTER_ACCOUNTS 选填. JSON 数组, 每项 {"name":"备注","account":"邮箱#密码"}
    例: [{"name":"甲","account":"a@x.com#pwdA"},{"name":"乙","account":"b@x.com#pwdB"}]
  设置了此项会自动忽略上面的单账号变量。
  (兼容旧格式: 每项也可写 {"name":"...","email":"...","password":"..."})

===== 青龙定时 =====
  新建任务 -> 命令: task agentrouter_checkin.py
  Cron: 0 9 * * *   (每天上午 9 点; 重复跑不会重复发额度, 服务端按天去重)
  依赖: requests (青龙面板自带; 本地缺则 pip install requests)

===== 注意事项 =====
  * 账号密码方式无需担心 cookie 过期, 最省心。
  * 签到后默认做一次端到端核验: 读取 /api/log/self 个人日志, 确认存在 type=4、
    内容含"签到成功"的当日记录, 才会报"日志已确认", 避免"登录成功但签到未真正触发"。
  * 备用域名 ps.air-outer.com 与本域名功能一致, 如需可改 AGENTROUTER_BASE_URL。
  * 若青龙环境无法直连(常见于需翻墙/容器 IPv6 问题):
    - 设 AGENTROUTER_FORCE_IPV4=1 强制走 IPv4 (海外服务器直连常见修复)
    - 或设 AGENTROUTER_PROXY 指向可达代理, 例 http://127.0.0.1:10808
      (Docker 同机用 http://host.docker.internal:10808; http 不通试 socks5://)
"""

import os
import sys
import json
import time
import random
import traceback

try:
    import requests
except ImportError:
    print("缺少依赖 requests, 请先执行: pip install requests")
    sys.exit(1)

# ---------- 基础配置 ----------
BASE_URL = os.environ.get("AGENTROUTER_BASE_URL", "https://agentrouter.org").rstrip("/")
LOGIN_PATH = "/api/user/login"
# 用户个人日志(控制台"使用日志"页), 需带 New-API-User: <数字 uid> 请求头
SELF_LOG_PATH = "/api/log/self/"
SELF_LOG_HEADER = "New-API-User"
CHECKIN_LOG_TYPE = 4  # 每日签到日志的 type 字段值
TIMEOUT = 20

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36")

# ---------- 代理(可选) ----------
PROXY = os.environ.get("AGENTROUTER_PROXY", "").strip()
PROXIES = {"http": PROXY, "https": PROXY} if PROXY else None

# ---------- 强制 IPv4(可选) ----------
# 部分容器有 IPv6 地址但无 IPv6 默认路由, 解析到站点 IPv6 地址后连接直接报
# [Errno 101] Network unreachable 且不回退 IPv4。设 AGENTROUTER_FORCE_IPV4=1
# 可强制所有连接只走 IPv4。海外服务器能直连时一般无需代理。
if os.environ.get("AGENTROUTER_FORCE_IPV4", "").strip() in ("1", "true", "yes", "on"):
    import socket as _socket
    _orig_getaddrinfo = _socket.getaddrinfo
    def _getaddrinfo_ipv4(host, port, family=0, type=0, proto=0, flags=0):
        return _orig_getaddrinfo(host, port, _socket.AF_INET, type, proto, flags)
    _socket.getaddrinfo = _getaddrinfo_ipv4

# 通知: 青龙自带 notify 模块, 没有则降级为仅打印
send = None
try:
    from notify import send  # 青龙面板内置
except Exception:
    send = None


def log(msg):
    ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
    print(f"[{ts}] {msg}")


def safe_notify(title, content):
    if send:
        try:
            send(title, content)
        except Exception as e:
            log(f"通知发送失败(不影响签到): {e}")
    else:
        log(f"[通知] {title}\n{content}")


def parse_account(raw):
    """把 '邮箱#密码' 拆成 (email, password)。邮箱不含 #, 故按首个 # 切分。"""
    raw = (raw or "").strip()
    if "#" in raw:
        email, password = raw.split("#", 1)
        return email.strip(), password.strip()
    return raw.strip(), ""


def extract_quota(payload):
    """从登录响应的 data 中提取余额字段。"""
    if isinstance(payload, dict):
        for k in ("quota", "remainder_quota", "balance"):
            if k in payload:
                return payload[k]
    return None


# ===================== 账号密码登录 =====================
def password_login(account):
    name = account.get("name", "默认账号")
    email = (account.get("email") or "").strip()
    password = (account.get("password") or "").strip()
    if not email or not password:
        return _result(name, "fail", "未配置 email/password, 跳过", None, None)

    log(f"====== 开始处理账号(账号密码登录): {name} ======")
    site = requests.Session()
    site.headers.update({
        "User-Agent": UA,
        "Content-Type": "application/json",
        "Accept": "application/json, text/plain, */*",
        "Referer": f"{BASE_URL}/login",
        "Origin": BASE_URL,
    })
    site.proxies = PROXIES

    try:
        r = site.post(f"{BASE_URL}{LOGIN_PATH}",
                      json={"username": email, "password": password},
                      timeout=TIMEOUT)
    except Exception as e:
        return _result(name, "fail", f"登录请求异常: {e}", None, None)

    if "text/html" in r.headers.get("Content-Type", ""):
        return _result(name, "fail", "登录接口返回 HTML(可能被 WAF 拦截或路径变化)", None, None)

    try:
        j = r.json()
    except Exception:
        return _result(name, "fail", f"登录响应非 JSON: {r.text[:120]}", None, None)

    if not j.get("success"):
        return _result(name, "fail",
                       f"登录失败: {j.get('message') or r.text[:120]}", None, None)

    data = j.get("data") or {}
    checked_in = bool(data.get("checked_in"))
    username = data.get("username") or data.get("display_name") or email
    quota = extract_quota(data)
    uid = data.get("id")

    if checked_in:
        level, vdetail, _, _ = verify_checkin(site, uid)
        if level in ("new", "today"):
            status = "success"
            msg = f"签到成功，日志已确认（{vdetail}）"
        else:
            status = "success"
            msg = f"登录成功且服务端返回已签到，但日志未确认: {vdetail}"
    else:
        status = "success"
        msg = "登录成功，但 checked_in=false(可能今日额度已发或接口变化)"

    return _result(name, status, msg, username, quota)


# ===================== 签到日志核验 =====================
def verify_checkin(session, uid, slack_new=300, window_days=1):
    """登录成功后调用: 查询 /api/log/self 确认是否真的产生了"签到成功"日志。

    端到端验证: 服务端登录返回 checked_in=true 只说明"当日签到已计入",
    但 /console/log 里会落一条 type=4、内容含"签到成功"的日志。比对这条日志
    可以排除"登录成功但签到未真正触发"的边界情况。

    返回 (level, detail, ts, content):
      level:
        "new"   本次运行刚生成了签到日志(created_at 在 slack_new 秒内)
        "today" 近 window_days 天内有签到日志(多半是今日更早时已完成签到)
        "none"  找不到任何签到日志 / 日志过旧
        "error" 日志接口异常(此时不应影响登录结论)
    """
    if not uid:
        return "error", "缺少 uid, 跳过日志核验", None, None
    try:
        r = session.get(f"{BASE_URL}{SELF_LOG_PATH}",
                        params={"p": 1, "page_size": 20},
                        headers={SELF_LOG_HEADER: str(uid)},
                        timeout=TIMEOUT)
        if r.status_code != 200 or "text/html" in r.headers.get("Content-Type", ""):
            return "error", f"日志接口返回 HTTP {r.status_code}", None, None
        items = (r.json().get("data") or {}).get("items") or []
    except Exception as e:
        return "error", f"日志查询异常: {e}", None, None

    now = int(time.time())
    newest_ts, newest_content = None, None
    for it in items:
        content = it.get("content") or ""
        if ("签到成功" in content) or (it.get("type") == CHECKIN_LOG_TYPE):
            ts = it.get("created_at")
            if isinstance(ts, (int, float)) and (newest_ts is None or ts > newest_ts):
                newest_ts, newest_content = ts, content

    if newest_ts is None:
        return "none", "日志中未找到任何签到记录", None, None

    ago = now - newest_ts
    if ago < 60:
        ago_str = f"{ago} 秒前"
    elif ago < 3600:
        ago_str = f"{int(ago / 60)} 分钟前"
    elif ago < 86400:
        ago_str = f"{int(ago / 3600)} 小时前"
    else:
        ago_str = f"{int(ago / 86400)} 天前"

    if newest_ts >= now - slack_new:
        return "new", f"本次运行已生成签到日志（{ago_str}）", newest_ts, newest_content
    if newest_ts >= now - window_days * 86400:
        return "today", f"近 {window_days} 天内有签到记录（{ago_str}），本次未新增", newest_ts, newest_content
    return "none", f"最近一条签到日志较旧（{ago_str}）", newest_ts, newest_content


# ===================== 调度 =====================
def do_checkin(account):
    email = (account.get("email") or "").strip()
    password = (account.get("password") or "").strip()
    if email and password:
        return password_login(account)
    return _result(account.get("name", "默认账号"), "fail",
                   "账号未配置 email/password, 跳过", None, None)


def _result(name, status, message, username, quota):
    res = {
        "name": name,
        "status": status,
        "message": message,
        "username": username or "",
        "quota": quota,
        "time": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
    }
    tag = {"success": "✅ 成功", "already": "🟡 已签到", "fail": "❌ 失败"}[status]
    quota_str = f"{quota}" if quota is not None else "未知"
    log(f"[{name}] {tag} | {message} | 额度: {quota_str}")
    return res


def collect_accounts():
    accounts = []
    multi = os.environ.get("AGENTROUTER_ACCOUNTS", "").strip()
    if multi:
        try:
            arr = json.loads(multi)
            if isinstance(arr, list):
                for i, a in enumerate(arr):
                    acct = a.get("account") or ""
                    email, password = parse_account(acct)
                    # 兼容旧格式 {"email":..,"password":..}
                    if (not email or not password) and a.get("email") and a.get("password"):
                        email, password = a["email"], a["password"]
                    if email and password:
                        accounts.append({
                            "name": a.get("name", f"账号{i + 1}"),
                            "email": email,
                            "password": password,
                        })
                if accounts:
                    log(f"已读取多账号配置, 共 {len(accounts)} 个")
                    return accounts
        except Exception as e:
            log(f"AGENTROUTER_ACCOUNTS 解析失败: {e}, 回退到单账号")

    single = os.environ.get("AGENTROUTER_ACCOUNT", "").strip()
    if single:
        email, password = parse_account(single)
        if email and password:
            accounts.append({"name": "默认账号", "email": email, "password": password})
            log("已读取单账号配置(AGENTROUTER_ACCOUNT = 邮箱#密码)")
            return accounts

    log("未检测到任何配置: 请设置 AGENTROUTER_ACCOUNT=邮箱#密码 或 AGENTROUTER_ACCOUNTS")
    return accounts


def main():
    log("AgentRouter 自动签到启动 (账号密码登录即签到)")

    accounts = collect_accounts()
    if not accounts:
        safe_notify("[AgentRouter] 签到失败", "未检测到账号配置, 请检查环境变量")
        return

    results = []
    for acc in accounts:
        try:
            res = do_checkin(acc)
            if res:
                results.append(res)
        except Exception:
            log(f"[{acc.get('name', '?')}] 处理异常:\n{traceback.format_exc()}")
        if len(accounts) > 1:
            time.sleep(random.uniform(2, 5))

    if not results:
        safe_notify("[AgentRouter] 签到失败", "所有账号均未成功执行")
        return

    lines = []
    for r in results:
        tag = {"success": "✅", "already": "🟡", "fail": "❌"}[r["status"]]
        quota_str = f"{r['quota']}" if r["quota"] is not None else "未知"
        who = r["username"] or r["name"]
        lines.append(f"{tag} {r['name']}({who})：{r['message']} | 额度 {quota_str}")
    safe_notify("[AgentRouter] 签到汇总", "\n".join(lines))
    log("全部账号处理完毕")


if __name__ == "__main__":
    main()
