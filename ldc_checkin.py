#!/usr/bin/env python3
"""
ChatGPT LDC商店 每日签到脚本
支持多账号签到,日志详细直观
"""

import os
import sys
import json
import time
import random
import traceback
import requests
from datetime import datetime, timezone, timedelta

# ──────────────────────────────────────────────
# 签到接口配置
# ──────────────────────────────────────────────
SHOP_URL = "https://shop.chatgpt.org.uk/profile"
SIGN_IN_ACTION_ID = "00e41629d536d72d1b351df6ca611a57fefa45acb6"
CHECK_STATUS_ACTION_ID = "00de206e502421f6ce6b4b61785f6ce7927172a95b"

# 关键 Cookie 名称列表
REQUIRED_COOKIES = [
    "__Secure-authjs.session-token",
    "__Host-authjs.csrf-token",
    "__Secure-authjs.callback-url",
    "ldc-locale",
    "__gads",
    "__eoi",
]

CST = timezone(timedelta(hours=8))


# ═══════════════════════════════════════════════
# 工具函数
# ═══════════════════════════════════════════════

def log(msg, level="INFO"):
    now = datetime.now(CST).strftime("%H:%M:%S.%f")[:-3]
    # 使用不同前缀区分日志级别
    prefix_map = {
        "PHASE":  "┌",
        "REQ":    "│ ▶",
        "RESP":   "│ ◀",
        "OK":     "│ ✔",
        "SKIP":   "│ ⚠",
        "ERR":    "│ ✖",
        "WARN":   "│ ⚡",
        "INFO":   "│  ",
        "STAT":   "│   ",
    }
    prefix = prefix_map.get(level, "│  ")
    print(f"[{now}] {prefix} {msg}", flush=True)


def log_phase(title):
    """阶段分隔标题"""
    log("─" * 55, "PHASE")
    log(title, "PHASE")


def log_json(obj, level="STAT"):
    """格式化打印 JSON 对象"""
    for line in json.dumps(obj, ensure_ascii=False, indent=2).split("\n"):
        log(f"    {line}", level)


def random_delay(lo, hi, purpose=""):
    delay = random.uniform(lo, hi)
    desc = f" ── {purpose}" if purpose else ""
    log(f"等待 {delay:.1f}s{desc}")


def mask_cookie(cookie_str: str) -> str:
    """脱敏 Cookie 字符串用于日志输出"""
    if len(cookie_str) <= 80:
        return cookie_str
    return cookie_str[:40] + "..." + cookie_str[-40:]


def analyze_cookie(cookie_str: str) -> dict:
    """分析 Cookie 内容,返回各关键项的摘要"""
    info = {}
    pairs = {}
    for part in cookie_str.split(";"):
        part = part.strip()
        if "=" not in part:
            continue
        key, _, val = part.partition("=")
        key = key.strip()
        pairs[key] = val.strip()

    info["total_pairs"] = len(pairs)
    info["present"] = []
    info["missing"] = []

    for name in REQUIRED_COOKIES:
        if name in pairs:
            val = pairs[name]
            if len(val) > 40:
                val = val[:20] + "..." + val[-15:]
            info["present"].append({"name": name, "value_preview": val})
        else:
            info["missing"].append(name)
    return info


def dump_request(method, url, headers, body):
    """打印请求详情"""
    log(f"请求: {method} {url}", "REQ")
    log(f"Content-Type: {headers.get('content-type','?')}", "REQ")
    log(f"next-action : {headers.get('next-action','?')}", "REQ")
    log(f"Referer     : {headers.get('referer','?')}", "REQ")
    # 脱敏 Cookie
    cookie_raw = headers.get("cookie", "")
    safe = mask_cookie(cookie_raw)
    log(f"Cookie({len(cookie_raw)}B): {safe}", "REQ")
    if body:
        log(f"Body({len(body)}B): {body[:200]}", "REQ")


def dump_response(resp, elapsed_ms):
    """打印响应详情"""
    log(f"响应: HTTP {resp.status_code}  耗时: {elapsed_ms}ms  大小: {len(resp.content)}B", "RESP")
    # 关键响应头
    key_headers = ["content-type", "x-opennext", "x-action-revalidated", "cf-ray", "vary"]
    for h in key_headers:
        if h in resp.headers:
            log(f"  {h}: {resp.headers[h]}", "STAT")

    # 尝试格式化解包响应中的 JSON
    text = resp.text
    json_parts = {}
    for line in text.split("\n"):
        if not line.strip():
            continue
        idx = line.find("{")
        if idx != -1:
            try:
                parsed = json.loads(line[idx:])
                prefix = line[:idx].rstrip(":0123456789").strip()
                key = line[:idx].strip()
                json_parts[key] = parsed
            except json.JSONDecodeError:
                continue

    if json_parts:
        for key, val in json_parts.items():
            log(f"解析 [{key}] → {json.dumps(val, ensure_ascii=False)}", "RESP")
    else:
        log(f"原始响应(前500字符): {text[:500]}", "RESP")


# ═══════════════════════════════════════════════
# 账号解析
# ═══════════════════════════════════════════════

def parse_accounts():
    accounts = []

    cookies_json = os.environ.get("CHECKIN_COOKIES", "")
    if cookies_json:
        try:
            data = json.loads(cookies_json)
            if isinstance(data, list):
                for item in data:
                    accounts.append({
                        "name": item.get("name", f"账号{len(accounts) + 1}"),
                        "cookie": item.get("cookie", ""),
                    })
            elif isinstance(data, dict):
                accounts.append({
                    "name": data.get("name", "账号1"),
                    "cookie": data.get("cookie", ""),
                })
        except json.JSONDecodeError as e:
            log(f"CHECKIN_COOKIES JSON 解析失败: {e}", "ERR")

    if not accounts:
        single = os.environ.get("CHECKIN_COOKIE", "")
        if single:
            accounts.append({"name": "账号1", "cookie": single})

    return accounts


# ═══════════════════════════════════════════════
# 签到核心逻辑
# ═══════════════════════════════════════════════

def do_checkin(account_name, cookie_str, round_num=1):
    t_start = datetime.now(CST)

    log_phase(f"账号 [{account_name}] 开始处理")

    # ── Cookie 分析 ──
    cookie_info = analyze_cookie(cookie_str)
    log(f"Cookie 解析: 共 {cookie_info['total_pairs']} 个键值对", "INFO")
    for p in cookie_info["present"]:
        log(f"  ✔ {p['name']} = {p['value_preview']}", "OK")
    for m in cookie_info["missing"]:
        log(f"  ✖ 缺少: {m}", "WARN")

    # 关键 cookie 检查
    if not cookie_str or "__Secure-authjs.session-token" not in cookie_str:
        log(f"Cookie 中缺少 session-token,无法继续", "ERR")
        return False, "Cookie 无效: 缺少 session-token"

    # ── 构建请求头 ──
    headers = {
        "next-action": SIGN_IN_ACTION_ID,
        "accept": "text/x-component",
        "content-type": "text/plain;charset=UTF-8",
        "referer": "https://shop.chatgpt.org.uk/profile",
        "origin": "https://shop.chatgpt.org.uk",
        "user-agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/150.0.0.0 Safari/537.36"
        ),
        "cookie": cookie_str,
    }

    # ── 阶段一: 延迟(模拟打开页面) ──
    random_delay(1.0, 3.5, "模拟打开页面")

    # ── 阶段二: 检查签到状态 ──
    log_phase(f"账号 [{account_name}] 阶段一: 检查签到状态")
    check_headers = headers.copy()
    check_headers["next-action"] = CHECK_STATUS_ACTION_ID

    dump_request("POST", SHOP_URL, check_headers, "[]")
    try:
        t0 = time.time()
        resp = requests.post(SHOP_URL, headers=check_headers, data="[]", timeout=30)
        elapsed = int((time.time() - t0) * 1000)
        dump_response(resp, elapsed)

        if resp.status_code == 200:
            for line in resp.text.split("\n"):
                if "checkedIn" in line:
                    idx = line.find("{")
                    if idx == -1:
                        continue
                    try:
                        data = json.loads(line[idx:])
                    except (json.JSONDecodeError, ValueError):
                        continue
                    checked = data.get("checkedIn")

                    if checked is True:
                        log(f"状态: 今日已签到,无需重复操作", "SKIP")
                        t_end = datetime.now(CST)
                        log(f"账号 [{account_name}] 共耗时 {(t_end-t_start).total_seconds():.1f}s", "STAT")
                        return True, "今日已签到(状态检查确认)"
                    elif checked is False:
                        log(f"状态: 今日尚未签到", "OK")
                    else:
                        log(f"状态: checkedIn={checked},继续尝试", "WARN")
        else:
            log(f"状态检查返回非200: {resp.status_code},继续尝试签到", "WARN")
    except requests.exceptions.ConnectionError as e:
        log(f"状态检查连接失败: {e}", "ERR")
        log(f"{traceback.format_exc()}", "ERR")
        return False, f"连接失败: {e}"
    except Exception as e:
        log(f"状态检查异常: {e}", "WARN")
        log(f"{traceback.format_exc()}", "WARN")

    # ── 阶段三: 延迟(模拟点击按钮) ──
    random_delay(1.5, 4.0, "模拟点击签到按钮")

    # ── 阶段四: 执行签到 ──
    log_phase(f"账号 [{account_name}] 阶段二: 执行签到")
    log(f"next-action ID: {SIGN_IN_ACTION_ID}", "STAT")
    dump_request("POST", SHOP_URL, headers, "[]")

    try:
        t0 = time.time()
        resp = requests.post(SHOP_URL, headers=headers, data="[]", timeout=30)
        elapsed = int((time.time() - t0) * 1000)
        dump_response(resp, elapsed)

        if resp.status_code != 200:
            log(f"签到返回异常HTTP状态: {resp.status_code}", "ERR")
            dump_response(resp, elapsed)
            return False, f"HTTP {resp.status_code}"

        # 解析签到结果
        log(f"解析签到响应数据 ...", "STAT")
        for line in resp.text.split("\n"):
            if "success" not in line:
                continue
            idx = line.find("{")
            if idx == -1:
                continue
            try:
                data = json.loads(line[idx:])
            except (json.JSONDecodeError, ValueError):
                continue

            success = data.get("success")
            log(f"success 字段: {success}", "STAT")

            if success:
                points = data.get("points")
                consecutive = data.get("consecutiveDays")
                msg_parts = []
                if points is not None:
                    log(f"  获得积分  : +{points}", "OK")
                    msg_parts.append(f"积分+{points}")
                if consecutive is not None:
                    log(f"  连续签到  : {consecutive} 天", "OK")
                    msg_parts.append(f"连续{consecutive}天")
                # 额外字段展示
                for k, v in data.items():
                    if k not in ("success", "points", "consecutiveDays"):
                        log(f"  额外字段  : {k} = {v}", "STAT")
                t_end = datetime.now(CST)
                log(f"账号 [{account_name}] 共耗时 {(t_end-t_start).total_seconds():.1f}s", "STAT")
                return True, "签到成功," + ",".join(msg_parts)
            else:
                error_msg = data.get("error") or data.get("message") or "(无错误信息)"
                log(f"签到被拒绝, 原因: {error_msg}", "ERR")
                log(f"完整响应数据: {json.dumps(data, ensure_ascii=False)}", "ERR")
                return False, error_msg

        # 未解析到 success 字段
        log(f"未在响应中找到 success 字段", "WARN")
        log(f"完整原始响应({len(resp.text)}字符):", "WARN")
        for line in resp.text.split("\n"):
            if line.strip():
                log(f"  | {line[:150]}", "WARN")
        return False, "响应中无 success 字段"

    except requests.exceptions.Timeout:
        log(f"签到请求超时(>30s)", "ERR")
        return False, "请求超时"
    except requests.exceptions.ConnectionError as e:
        log(f"签到连接失败: {e}", "ERR")
        log(f"{traceback.format_exc()}", "ERR")
        return False, f"连接失败: {e}"
    except Exception as e:
        log(f"签到抛出异常: {type(e).__name__}: {e}", "ERR")
        log(f"{traceback.format_exc()}", "ERR")
        return False, str(e)


# ═══════════════════════════════════════════════
# 主入口
# ═══════════════════════════════════════════════

def main():
    t_global_start = datetime.now(CST)

    # 环境信息
    log("=" * 55)
    log("ChatGPT LDC商店 每日签到 v1.0", "PHASE")
    log(f"执行时间: {t_global_start.strftime('%Y-%m-%d %H:%M:%S %Z')}", "STAT")
    log(f"Python   : {sys.version.split()[0]}", "STAT")
    log(f"requests : {requests.__version__}", "STAT")
    log(f"时区      : UTC+8 (Asia/Shanghai)", "STAT")
    log(f"目标地址  : {SHOP_URL}", "STAT")
    log("=" * 55)

    accounts = parse_accounts()

    if not accounts:
        log("未检测到任何账号配置!", "ERR")
        log("请在 GitHub Secrets 中设置 CHECKIN_COOKIE 或 CHECKIN_COOKIES", "ERR")
        sys.exit(1)

    log(f"账号总数: {len(accounts)}", "INFO")
    for i, acc in enumerate(accounts):
        ci = analyze_cookie(acc["cookie"])
        log(f"  [{i+1}] {acc['name']} (Cookie: {ci['total_pairs']}项, "
            f"session-token: {'✔' if '__Secure-authjs.session-token' not in ci['missing'] else '✖'})", "STAT")

    log("")

    # 启动抖动
    random_delay(0.5, 3.0, "启动抖动(规避规律性)")

    success_count = 0
    fail_count = 0
    results = []

    for i, account in enumerate(accounts):
        if i > 0:
            random_delay(5.0, 15.0, "切换账号间隔")

        ok, msg = do_checkin(account["name"], account["cookie"], round_num=i+1)
        results.append({"name": account["name"], "success": ok, "message": msg})
        if ok:
            success_count += 1
        else:
            fail_count += 1

    # ── 汇总 ──
    t_global_end = datetime.now(CST)
    total_s = (t_global_end - t_global_start).total_seconds()
    log("")
    log("=" * 55)
    log("签到结果汇总", "PHASE")
    for r in results:
        icon = "✅" if r["success"] else "❌"
        log(f"  {icon}  {r['name']}", "STAT")
        log(f"      {r['message']}", "STAT")
    log("")
    log(f"成功: {success_count}  失败: {fail_count}  总计: {len(results)}", "INFO")
    log(f"总耗时: {total_s:.1f}s", "STAT")
    log(f"结束时间: {t_global_end.strftime('%Y-%m-%d %H:%M:%S %Z')}", "STAT")
    log("=" * 55)

    if fail_count > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()