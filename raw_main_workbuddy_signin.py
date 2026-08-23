# -*- coding: utf-8 -*-
"""
new Env('WorkBuddy 每日签到');
cron: 5 0 * * *

WorkBuddy 每日签到自动领取脚本（青龙面板版）
============================================
WorkBuddy 每日签到自动领取脚本，适配青龙面板 / 拾光坞 N3 NAS。

特性：
  - 零依赖：纯 Python 标准库，无需 pip install
  - 多账号：支持多账号批量签到
  - 幂等安全：先查状态，未签才领，重复运行不会多领
  - Token 过期预警：自动解码 JWT 过期时间，到期前主动提醒
  - 多渠道通知：青龙面板 / PushPlus 微信 / 控制台输出

签到接口（与官方桌面端同一个 endpoint）：
  POST {endpoint}/v2/billing/meter/checkin-activity-status  查询签到状态
  POST {endpoint}/v2/billing/meter/daily-checkin            领取今日积分

【环境变量配置】
  必填：
    WORKBUDDY_TOKEN   WorkBuddy 的 accessToken（从本机桌面端登录态文件获取）
                      多账号用 & 分隔，需与 WORKBUDDY_UID 一一对应
    WORKBUDDY_UID     WorkBuddy 的 uid（从本机桌面端登录态文件获取）
                      多账号用 & 分隔，需与 WORKBUDDY_TOKEN 一一对应
  可选：
    WORKBUDDY_EXTRA   额外字段，格式 enterpriseId#domain#endpoint
                      多账号用 & 分隔，按位置对应；留空则用默认 endpoint
    WORKBUDDY_ENDPOINT 全局默认 endpoint，默认 https://copilot.tencent.com
    WORKBUDDY_TOKEN_WARN_DAYS  Token 过期预警天数，默认 2 天
                      当 Token 剩余有效期 ≤ 此值时，通知中会提示即将过期
    PUSHPLUS_TOKEN    PushPlus 推送 token（https://www.pushplus.plus）
                      配置后可通过微信接收签到通知，无需使用青龙面板通知

【如何获取 accessToken 和 uid】
  1. 在电脑上安装并登录 WorkBuddy 桌面端
  2. 登录后会生成凭据文件：
     - Windows: %LOCALAPPDATA%\\CodeBuddyExtension\\Data\\Public\\auth\\workbuddy-desktop.info
     - macOS  : ~/Library/Application Support/CodeBuddyExtension/Data/Public/auth/workbuddy-desktop.info
     - Linux  : ~/.config/CodeBuddyExtension/Data/Public/auth/workbuddy-desktop.info
  3. 用文本编辑器打开该文件，取 auth.accessToken 填入 WORKBUDDY_TOKEN，
     取 account.uid 填入 WORKBUDDY_UID
     （如 account.enterpriseId、auth.domain 存在且需要，填入 WORKBUDDY_EXTRA）

【免责声明】
  本脚本为非官方工具，签到接口系从桌面端逆向得到，仅供个人自动化使用。
  接口可能随时变动且不另行通知，使用风险自负。
"""
import base64
import json
import os
import ssl
import sys
import time
import traceback
import urllib.error
import urllib.request

# ============================================================
# 通知模块（兼容青龙面板 QLAPI / notify.py / PushPlus / 纯输出）
# ============================================================
def _send_pushplus(title, content):
    """通过 PushPlus 推送微信通知。"""
    token = os.environ.get("PUSHPLUS_TOKEN", "").strip()
    if not token:
        return False
    try:
        url = "http://www.pushplus.plus/send"
        data = json.dumps({
            "token": token,
            "title": title,
            "content": content,
            "template": "txt",
        }).encode("utf-8")
        req = urllib.request.Request(url, data=data,
                                     headers={"Content-Type": "application/json"},
                                     method="POST")
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = json.loads(resp.read().decode("utf-8"))
            if body.get("code") == 200:
                return True
            else:
                print("  PushPlus 返回异常：%s" % body.get("msg", body))
                return False
    except Exception as e:
        print("  PushPlus 推送失败：%s" % e)
        return False


def send_notify(title, content):
    """发送通知，兼容青龙面板的 QLAPI 和 notify.py，同时支持 PushPlus。"""
    pushed = False
    # 方式1：青龙面板内置 QLAPI（推荐）
    try:
        if "QLAPI" in globals() and hasattr(QLAPI, "notify"):
            QLAPI.notify(title, content)
            pushed = True
    except Exception:
        pass
    # 方式2：青龙面板 notify.py 模块
    try:
        from notify import send as _send  # noqa
        _send(title, content)
        pushed = True
    except Exception:
        pass
    # 方式3：PushPlus 微信推送
    if _send_pushplus(title, content):
        pushed = True
    # 方式4：仅打印
    if not pushed:
        print("【通知】%s\n%s" % (title, content))


# ============================================================
# Token 过期检测（JWT 解码，零依赖）
# ============================================================
def _b64url_decode(s):
    """Base64url 解码（JWT 使用 url-safe base64，需要补 padding）。"""
    s = s.strip()
    # 补 padding：base64url 用 - 和 _ 替代 + 和 /，且末尾省略 =
    s = s.replace("-", "+").replace("_", "/")
    pad_len = (4 - len(s) % 4) % 4
    s += "=" * pad_len
    return base64.b64decode(s)


def decode_jwt_exp(token):
    """从 JWT token 中解码出 exp（过期时间戳，Unix 秒）。
    返回 int 时间戳，或 None（非 JWT / 无 exp 字段）。"""
    try:
        parts = token.split(".")
        if len(parts) != 3:
            return None
        payload = _b64url_decode(parts[1])
        data = json.loads(payload.decode("utf-8"))
        exp = data.get("exp")
        if exp is not None:
            return int(exp)
    except Exception:
        pass
    return None


def check_token_expiry(token, warn_days=2):
    """检查 token 是否即将过期。
    返回 (days_remaining, warning_message)；若安全则返回 (days_remaining, None)。
    若非 JWT 或无 exp 字段则返回 (None, None)。"""
    exp_ts = decode_jwt_exp(token)
    if exp_ts is None:
        return None, None  # 非 JWT 或无法解析，跳过检查
    now = time.time()
    remaining_secs = exp_ts - now
    days_remaining = int(remaining_secs // 86400)
    if remaining_secs <= 0:
        return 0, "⚠️ Token 已过期！请重新登录 WorkBuddy 桌面端并更新环境变量"
    if days_remaining <= warn_days:
        return days_remaining, "⚠️ Token 仅剩 %d 天即将过期，请尽快更新" % days_remaining
    return days_remaining, None


# ============================================================
# 配置解析
# ============================================================
DEFAULT_ENDPOINT = "https://copilot.tencent.com"


def split_accounts(value):
    """把多账号字符串（用 & 或换行分隔）拆成列表。"""
    if not value:
        return []
    parts = value.replace("\n", "&").split("&")
    return [p.strip() for p in parts if p.strip()]


def load_accounts():
    """从环境变量解析出账号列表，每个账号是一个 headers + endpoint 的配置。"""
    tokens = split_accounts(os.environ.get("WORKBUDDY_TOKEN", ""))
    uids = split_accounts(os.environ.get("WORKBUDDY_UID", ""))
    extras = split_accounts(os.environ.get("WORKBUDDY_EXTRA", ""))
    default_endpoint = (os.environ.get("WORKBUDDY_ENDPOINT") or DEFAULT_ENDPOINT).rstrip("/")

    if not tokens or not uids:
        return [], "未配置 WORKBUDDY_TOKEN / WORKBUDDY_UID，请在青龙面板「环境变量」中添加"
    if len(tokens) != len(uids):
        return [], "WORKBUDDY_TOKEN（%d 个）与 WORKBUDDY_UID（%d 个）数量不一致" % (len(tokens), len(uids))

    accounts = []
    for i in range(len(tokens)):
        token = tokens[i]
        uid = uids[i]
        headers = {
            "Accept": "application/json",
            "Authorization": "Bearer %s" % token,
            "Content-Type": "application/json",
            "X-User-Id": uid,
            "User-Agent": "WorkBuddy",
        }
        endpoint = default_endpoint
        if i < len(extras):
            fields = extras[i].split("#")
            # enterpriseId#domain#endpoint
            if len(fields) >= 1 and fields[0]:
                headers["X-Enterprise-Id"] = fields[0]
                headers["X-Tenant-Id"] = fields[0]
            if len(fields) >= 2 and fields[1]:
                headers["X-Domain"] = fields[1]
            if len(fields) >= 3 and fields[2]:
                endpoint = fields[2].rstrip("/")
        accounts.append({"headers": headers, "endpoint": endpoint, "index": i + 1})
    return accounts, None


# ============================================================
# HTTP 请求（零依赖）
# ============================================================
def post(url, headers, payload=None):
    body = json.dumps(payload or {}).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    ctx = ssl.create_default_context()
    try:
        with urllib.request.urlopen(req, timeout=30, context=ctx) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, {"raw": raw[:500]}


# ============================================================
# 响应解析
# ============================================================
def dig(obj, key):
    """在可能被 data/result 包裹的响应里找字段，兼容信封结构。"""
    if isinstance(obj, dict):
        if key in obj and obj[key] is not None:
            return obj[key]
        for k in ("data", "result", "resp", "response"):
            if k in obj and isinstance(obj[k], dict):
                r = dig(obj[k], key)
                if r is not None:
                    return r
    return None


def fmt_credit(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return v


def _is_already_checked_in(cbody):
    """领取接口返回是否表示「今日已签」（兼容 null 与 400+code10001）。"""
    if cbody is None:
        return True
    if isinstance(cbody, dict):
        msg = cbody.get("msg") or ""
        if cbody.get("code") == 10001 or "已签" in msg:
            return True
    return False


def _already_report(status, via=None):
    """根据状态构造「今日已签」汇报 dict。"""
    today_credit = dig(status, "today_credit") or dig(status, "daily_credit")
    streak_days = dig(status, "streak_days")
    total_credits = dig(status, "total_credits")
    is_streak_day = dig(status, "is_streak_day")
    next_streak_day = dig(status, "next_streak_day")
    inner = []
    if today_credit is not None:
        inner.append("今日 +%s" % fmt_credit(today_credit))
    if streak_days is not None:
        inner.append("连续 %s 天" % streak_days)
    if total_credits is not None:
        inner.append("累计 %s 积分" % fmt_credit(total_credits))
    prefix = via or "今日已签过"
    report = "%s（%s）" % (prefix, "，".join(inner)) if inner else prefix
    return {
        "result": "ALREADY",
        "report": report,
        "today_credit": today_credit,
        "streak_days": streak_days,
        "total_credits": total_credits,
        "is_streak_day": is_streak_day,
        "next_streak_day": next_streak_day,
    }


# ============================================================
# 签到主逻辑
# ============================================================
def run_auto(headers, endpoint):
    """每日自动化主逻辑：查状态 → 未签才领 → 返回汇报 dict。"""
    scode, sbody = post(endpoint + "/v2/billing/meter/checkin-activity-status", headers)
    if scode in (401, 403):
        return 1, {
            "result": "NO_SESSION",
            "report": "登录态已失效（HTTP %s），请重新登录 WorkBuddy 桌面端后更新环境变量" % scode,
            "http": scode,
        }
    if not (200 <= scode < 300):
        return 1, {
            "result": "ERROR",
            "report": "签到接口返回异常（HTTP %s），可能登录态失效，请更新凭据" % scode,
            "http": scode,
            "status_body": sbody,
        }
    status = sbody if isinstance(sbody, dict) else {}
    active = dig(status, "active")
    activity_name = dig(status, "activity_name")
    if active is False:
        report = "签到活动未开启" + ("（%s）" % activity_name if activity_name else "")
        return 0, {"result": "INACTIVE", "report": report, "active": False}
    if dig(status, "today_checked_in") is True:
        return 0, _already_report(status)

    ccode, cbody = post(endpoint + "/v2/billing/meter/daily-checkin", headers)
    if _is_already_checked_in(cbody):
        scode2, sbody2 = post(endpoint + "/v2/billing/meter/checkin-activity-status", headers)
        fresh = sbody2 if (200 <= scode2 < 300 and isinstance(sbody2, dict)) else status
        return 0, _already_report(fresh, via="今日已签过（服务端判定已领取）")
    if ccode in (401, 403):
        return 1, {
            "result": "NO_SESSION",
            "report": "登录态已失效（HTTP %s），请重新登录 WorkBuddy 桌面端后更新环境变量" % ccode,
            "http": ccode,
        }
    credit = dig(cbody, "credit")
    if credit is not None:
        scode2, sbody2 = post(endpoint + "/v2/billing/meter/checkin-activity-status", headers)
        fresh = sbody2 if (200 <= scode2 < 300 and isinstance(sbody2, dict)) else status
        streak_days = dig(fresh, "streak_days") or dig(status, "streak_days")
        total_credits = dig(fresh, "total_credits")
        is_streak_day = dig(fresh, "is_streak_day")
        next_streak_day = dig(fresh, "next_streak_day")
        bonus = "，且为连签奖励日" if is_streak_day else ""
        cum = "，累计 %s 积分" % fmt_credit(total_credits) if total_credits is not None else ""
        report = "成功领取 %s 积分%s（连续 %s 天%s）" % (fmt_credit(credit), bonus, streak_days, cum)
        return 0, {
            "result": "CLAIMED",
            "report": report,
            "credit": credit,
            "streak_days": streak_days,
            "total_credits": total_credits,
            "is_streak_day": is_streak_day,
            "next_streak_day": next_streak_day,
        }
    if isinstance(cbody, dict) and ("code" in cbody or "msg" in cbody):
        msg = cbody.get("msg") or ("code %s" % cbody.get("code"))
        return 1, {
            "result": "ERROR",
            "report": "领取失败：%s（HTTP %s）" % (msg, ccode),
            "http": ccode,
            "claim_body": cbody,
        }
    return 1, {
        "result": "UNKNOWN",
        "report": "未识别的领取返回，请检查接口：%s" % json.dumps(cbody, ensure_ascii=False)[:200],
        "http": ccode,
        "claim_body": cbody,
    }


# ============================================================
# 主入口
# ============================================================
def main():
    print("=" * 50)
    print("WorkBuddy 每日签到（青龙面板版）")
    print("=" * 50)

    accounts, err = load_accounts()
    if err:
        print("❌ %s" % err)
        send_notify("WorkBuddy 签到", "❌ %s" % err)
        return 1

    # 读取过期预警配置
    try:
        warn_days = int(os.environ.get("WORKBUDDY_TOKEN_WARN_DAYS", "2"))
    except ValueError:
        warn_days = 2

    print("共 %d 个账号\n" % len(accounts))

    reports = []
    fail_count = 0
    expiry_warnings = []  # 收集即将过期的 token 预警

    for acc in accounts:
        idx = acc["index"]
        token = acc["headers"]["Authorization"].replace("Bearer ", "")
        print("▶ 账号 %d" % idx)

        # 1. 先检查 token 过期时间
        days_left, warning = check_token_expiry(token, warn_days=warn_days)
        if warning:
            print("  %s" % warning)
            expiry_warnings.append("账号%d：%s" % (idx, warning))
        elif days_left is not None:
            print("  Token 有效期剩余约 %d 天" % days_left)

        # 2. 执行签到
        try:
            code, out = run_auto(acc["headers"], acc["endpoint"])
            report = out.get("report", "未知结果")
            result = out.get("result", "UNKNOWN")
            print("  %s\n" % report)
            reports.append("账号%d：%s" % (idx, report))
            if code != 0:
                fail_count += 1
        except Exception as e:
            msg = "执行异常：%s" % e
            print("  ❌ %s\n" % msg)
            reports.append("账号%d：❌ %s" % (idx, msg))
            fail_count += 1
            traceback.print_exc()

    # 汇总
    summary_parts = list(reports)
    if expiry_warnings:
        summary_parts.append("")
        summary_parts.append("—— Token 过期预警 ——")
        summary_parts.extend(expiry_warnings)

    summary = "\n".join(summary_parts)
    if expiry_warnings:
        title = "⚠️ WorkBuddy 签到（%d 成功 / %d 失败，%d 个 Token 即将过期）" % (
            len(accounts) - fail_count, fail_count, len(expiry_warnings))
    elif fail_count:
        title = "WorkBuddy 签到（%d 失败）" % fail_count
    else:
        title = "WorkBuddy 签到（%d 成功 / %d 失败）" % (len(accounts) - fail_count, fail_count)

    print("=" * 50)
    print(title)
    print(summary)
    print("=" * 50)
    send_notify(title, summary)

    return 0 if fail_count == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
