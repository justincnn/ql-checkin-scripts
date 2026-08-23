#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# cron: 5 10 * * *
# new Env('爱奇艺签到')
"""
爱奇艺每日签到脚本
适配青龙面板,凭据只读环境变量,纯标准库实现(无第三方依赖)。

功能:
  - 自然月签到(community.iqiyi.com/openApi/task/execute,md5 签名)
  - 网页积分签到(community.iqiyi.com/openApi/score/add,md5 签名)
  - 会员抽奖(iface2.iqiyi.com/aggregate/3.0/lottery_activity)
  - 摇一摇抽奖(act.vip.iqiyi.com/shake-api/lottery)
  - VIP 信息查询(serv.vip.iqiyi.com/vipgrowth/query.action)
  - 自动识别: 签到成功 / 今日已签到 / Cookie 失效
  - 支持多账号(每行一条 Cookie)
  - 运行结束发送通知总结(青龙内置通知 / PushPlus / Server酱)

环境变量(在青龙面板配置):
  IQIYI_COOKIES           多账号,每行一条 Cookie(# 开头为注释,优先)
  IQIYI_COOKIE            单账号 Cookie
  PUSHPLUS_TOKEN          PushPlus 推送 token(可选)
  SENDKEY                 Server酱 SendKey(可选)

Cookie 获取:
  1. 浏览器登录 https://www.iqiyi.com
  2. F12 → Application → Cookies → https://www.iqiyi.com
  3. 必填: P00001(登录态) / P00003(用户ID,抽奖需要)
     建议: __dfp(设备指纹,取 @ 前段,部分接口需要)
"""
import hashlib
import json
import os
import random
import string
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

# 社区接口固定签名密钥(爱奇艺客户端公开)
TASK_SIGN_KEY = "UKobMjDMsDoScuWOfp6F"   # 自然月签到
SCORE_SIGN_KEY = "DO58SzN6ip9nbJ4QkM8H"  # 网页积分签到

TASK_URL = "https://community.iqiyi.com/openApi/task/execute"
SCORE_URL = "https://community.iqiyi.com/openApi/score/add"
LOTTERY_URL = "https://iface2.iqiyi.com/aggregate/3.0/lottery_activity"
SHAKE_URL = "https://act.vip.iqiyi.com/shake-api/lottery"
VIP_URL = "http://serv.vip.iqiyi.com/vipgrowth/query.action"


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------
def _md5(s):
    return hashlib.md5(s.encode("utf-8")).hexdigest()


def _rand16():
    return "".join(random.choices(string.ascii_letters + string.digits, k=16))


def _ts13():
    return str(int(time.time() * 1000))


def _sign(params, key):
    """爱奇艺社区接口签名: 参数按 key 排序, | 拼接, 尾加密钥, md5"""
    s = "|".join("%s=%s" % (k, params[k]) for k in sorted(params))
    return _md5(s + "|" + key)


# ---------------------------------------------------------------------------
# HTTP 工具
# ---------------------------------------------------------------------------
def _http(url, method="GET", data=None, headers=None, timeout=15):
    """返回 (status, text)"""
    h = {
        "Accept": "*/*",
        "Accept-Language": "zh-CN,zh;q=0.9",
        "User-Agent": UA,
        "Referer": "https://www.iqiyi.com/",
        "Origin": "https://www.iqiyi.com",
    }
    if headers:
        h.update(headers)
    body = None
    if data is not None:
        if isinstance(data, dict):
            body = urllib.parse.urlencode(data).encode("utf-8")
        elif isinstance(data, bytes):
            body = data
        else:
            body = data.encode("utf-8")
    req = urllib.request.Request(url, data=body, headers=h, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except Exception as e:  # noqa: BLE001
        return -1, str(e)


def _json(txt):
    try:
        return json.loads(txt)
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Cookie 解析
# ---------------------------------------------------------------------------
def parse_cookie(cookie_str):
    """提取 P00001 / P00003 / __dfp"""
    p00001 = p00003 = dfp = ""
    for part in cookie_str.split(";"):
        part = part.strip()
        if "=" not in part:
            continue
        k, v = part.split("=", 1)
        k, v = k.strip(), v.strip()
        if k == "P00001" and not p00001:
            p00001 = v
        elif k == "P00003" and not p00003:
            p00003 = v
        elif k == "__dfp" and not dfp:
            dfp = v.split("@")[0] if "@" in v else v
    return p00001, p00003, dfp


# ---------------------------------------------------------------------------
# 签到
# ---------------------------------------------------------------------------
def task_sign(p00001, p00003):
    """自然月签到,返回 (ok, msg)"""
    qyid = _md5(_rand16())
    ts = _ts13()
    params = {
        "agentType": "1",
        "agentversion": "1.0",
        "appKey": "basic_pcw",
        "authCookie": p00001,
        "qyid": qyid,
        "task_code": "natural_month_sign",
        "timestamp": ts,
        "typeCode": "point",
        "userId": p00003,
    }
    sign = _sign(params, TASK_SIGN_KEY)
    url = TASK_URL + "?" + "&".join(
        "%s=%s" % (k, urllib.parse.quote(str(v), safe=""))
        for k, v in params.items()
    ) + "&sign=" + sign
    body = {
        "natural_month_sign": {
            "agentType": "1",
            "agentversion": "1",
            "authCookie": p00001,
            "qyid": qyid,
            "taskCode": "iQIYI_mofhr",
            "verticalCode": "iQIYI",
        }
    }
    hdrs = {"Content-Type": "application/json"}
    st, txt = _http(url, method="POST", data=body, headers=hdrs)
    if st != 200:
        return False, "HTTP %s: %s" % (st, txt[:200])
    d = _json(txt)
    if d is None:
        return False, "返回非JSON: %s" % txt[:200]
    code = d.get("code")
    if code == "A00000":
        sub = (d.get("data") or {})
        scode = sub.get("code")
        if scode == "A0000":
            days = sub.get("signDays") or sub.get("signDays")
            return True, "签到成功%s" % ((",连签%d天" % days) if days else "")
        msg = sub.get("msg") or ""
        if any(k in str(msg) for k in ("已签", "上限", "重复")):
            return True, "今日已签到"
        return False, "签到失败: %s" % (msg or sub.get("code") or "")
    if code == "A00401":
        return False, "Cookie失效(A00401),请重新登录获取"
    return False, "code=%s %s" % (code, d.get("message") or "")


def score_sign(p00001, p00003, dfp):
    """网页积分签到,返回 (ok, msg)"""
    params = {
        "agenttype": "1",
        "agentversion": "0",
        "appKey": "basic_pca",
        "appver": "0",
        "authCookie": p00001,
        "channelCode": "sign_pcw",
        "dfp": dfp,
        "scoreType": "1",
        "srcplatform": "1",
        "typeCode": "point",
        "userId": p00003,
        "user_agent": UA,
        "verticalCode": "iQIYI",
    }
    sign = _sign(params, SCORE_SIGN_KEY)
    url = SCORE_URL + "?" + "&".join(
        "%s=%s" % (k, urllib.parse.quote(str(v), safe=""))
        for k, v in params.items()
    ) + "&sign=" + sign
    st, txt = _http(url)
    if st != 200:
        return False, "HTTP %s: %s" % (st, txt[:200])
    d = _json(txt)
    if d is None:
        return False, "返回非JSON: %s" % txt[:200]
    code = d.get("code")
    if code == "A00000":
        data = d.get("data") or []
        if isinstance(data, list) and data:
            item = data[0]
            if item.get("code") == "A0000":
                return True, "积分签到成功"
            msg = item.get("message") or ""
            if "已签" in msg:
                return True, "今日已签到"
            return False, "积分签到失败: %s" % msg
        return True, "积分签到成功"
    if code == "A00401":
        return False, "Cookie失效(A00401),请重新登录获取"
    return False, "code=%s %s" % (code, d.get("message") or "")


# ---------------------------------------------------------------------------
# 抽奖 / VIP 信息
# ---------------------------------------------------------------------------
def lottery_draw(p00001, p00003):
    """会员抽奖,返回 (ok, msg)"""
    params = {
        "app_k": "b398b8ccbaeacca840073a7ee9b7e7e6",
        "app_v": "11.6.5",
        "platform_id": "10",
        "dev_os": "8.0.0",
        "dev_ua": "FRD-AL10",
        "net_sts": "1",
        "qyid": _md5(_rand16()),
        "psp_uid": p00003,
        "psp_cki": p00001,
        "psp_status": "3",
        "secure_v": "1",
        "secure_p": "GPhone",
        "req_sn": _ts13(),
    }
    url = LOTTERY_URL + "?" + urllib.parse.urlencode(params)
    st, txt = _http(url)
    if st != 200:
        return False, "HTTP %s" % st
    d = _json(txt)
    if d is None:
        return False, "返回非JSON"
    code = d.get("code")
    if code == 0 or code == "0":
        data = d.get("data") or {}
        prize = data.get("prizeName") or data.get("prize_name") or ""
        if prize:
            return True, "抽奖获得: %s" % prize
        return True, "抽奖成功"
    if "登录" in str(d.get("errorReason") or d.get("kv") or ""):
        return False, "Cookie失效,请重新登录"
    if "次数" in str(d.get("errorReason") or ""):
        return True, "抽奖次数已用完"
    return False, "抽奖失败: %s" % (d.get("errorReason") or d.get("msg") or code)


def shake_lottery(p00001):
    """摇一摇抽奖,返回 (ok, msg)"""
    params = {
        "P00001": p00001,
        "deviceID": _md5(_rand16()),
        "version": "15.3.0",
        "platform": _rand16()[:16],
        "lotteryType": "0",
        "actCode": "0k9GkUcjqqj4tne8",
        "extendParams": json.dumps({
            "appIds": "iqiyi_pt_vip_iphone_video_autorenew_12m_348yuan_v2",
            "supportSk2Identity": True,
            "testMode": "0",
            "iosSystemVersion": "17.4",
            "bundleId": "com.qiyi.iphone",
        }),
    }
    url = SHAKE_URL + "?" + urllib.parse.urlencode(params)
    st, txt = _http(url)
    if st != 200:
        return False, "HTTP %s" % st
    d = _json(txt)
    if d is None:
        return False, "返回非JSON"
    code = d.get("code")
    if code == "A00000":
        data = d.get("data") or {}
        prize = data.get("prizeName") or data.get("prize_name") or ""
        if prize:
            return True, "摇一摇获得: %s" % prize
        return True, "摇一摇成功"
    msg = str(d.get("msg") or "")
    if "次数" in msg or "用完" in msg:
        return True, "摇一摇次数已用完"
    if "登录" in msg or "过期" in msg:
        return False, "Cookie失效,请重新登录"
    return False, "摇一摇失败: %s" % (msg or code)


def vip_info(p00001):
    """查询 VIP 信息,返回 (ok, msg)"""
    url = VIP_URL + "?" + urllib.parse.urlencode({"P00001": p00001})
    st, txt = _http(url)
    if st != 200:
        return False, "HTTP %s" % st
    d = _json(txt)
    if d is None:
        return False, "返回非JSON"
    if d.get("code") == "A00000":
        data = d.get("data") or {}
        level = data.get("level") or data.get("vipLevel") or ""
        name = data.get("nickname") or ""
        expire = data.get("vipDeadline") or data.get("expireTime") or ""
        parts = []
        if level:
            parts.append("VIP%s" % level)
        if expire:
            parts.append("到期:%s" % expire)
        if name:
            parts.append("昵称:%s" % name)
        return True, " ".join(parts) if parts else "VIP信息查询成功"
    if "登录" in str(d.get("msg") or d.get("message") or ""):
        return False, "Cookie失效,请重新登录"
    return False, "VIP查询失败: %s" % (d.get("msg") or d.get("message") or d.get("code") or "")


# ---------------------------------------------------------------------------
# 通知
# ---------------------------------------------------------------------------
def ql_notify(title, content):
    """青龙面板内置通知"""
    try:
        sys.path.insert(0, "/ql/scripts")
        from notify import send  # noqa: PLC0415
        send(title, content)
        return True
    except Exception:  # noqa: BLE001
        try:
            subprocess.run(
                ["ql", "notify", "%s\n%s" % (title, content)],
                capture_output=True, timeout=30,
            )
            return True
        except Exception:  # noqa: BLE001
            return False


def pushplus_notify(title, content):
    tok = os.environ.get("PUSHPLUS_TOKEN", "").strip()
    if not tok:
        return False
    try:
        st, txt = _http(
            "http://www.pushplus.plus/send",
            method="POST",
            data={"token": tok, "title": title, "content": content},
            headers={"Content-Type": "application/json"},
        )
        return st == 200
    except Exception:  # noqa: BLE001
        return False


def serverchan_notify(title, content):
    key = os.environ.get("SENDKEY", "").strip()
    if not key:
        return False
    try:
        url = "https://sctapi.ftqq.com/%s.send" % key
        body = urllib.parse.urlencode({"title": title, "desp": content})
        req = urllib.request.Request(
            url, data=body.encode("utf-8"),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.status == 200
    except Exception:  # noqa: BLE001
        return False


def send_notify(title, content):
    """按优先级通知: 青龙 -> PushPlus -> Server酱"""
    if ql_notify(title, content):
        return "青龙通知"
    if pushplus_notify(title, content):
        return "PushPlus"
    if serverchan_notify(title, content):
        return "Server酱"
    return "无可用通知渠道"


# ---------------------------------------------------------------------------
# 账号解析
# ---------------------------------------------------------------------------
def parse_accounts():
    """优先 IQIYI_COOKIES(多账号, 每行一条);否则 IQIYI_COOKIE(单账号)"""
    accounts = []
    multi = os.environ.get("IQIYI_COOKIES", "").strip()
    if multi:
        for line in multi.splitlines():
            line = line.strip().strip("'").strip('"')
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                accounts.append(line)
    else:
        ck = os.environ.get("IQIYI_COOKIE", "").strip().strip("'").strip('"')
        if ck:
            accounts.append(ck)
    return accounts


def mask_cookie(cookie_str):
    """脱敏展示 Cookie,仅显示字段名与值首尾"""
    parts = []
    for part in cookie_str.split(";"):
        part = part.strip()
        if "=" in part:
            k, v = part.split("=", 1)
            v = v.strip()
            if len(v) > 10:
                v = v[:6] + "***" + v[-4:]
            parts.append("%s=%s" % (k, v))
    return "; ".join(parts)


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
def run_account(cookie_str):
    """单账号完整流程,返回 (ok, lines)"""
    p00001, p00003, dfp = parse_cookie(cookie_str)
    if not p00001:
        return False, ["Cookie 缺少 P00001,请重新登录获取"]
    if not p00003:
        return False, ["Cookie 缺少 P00003(抽奖需要),请重新登录获取"]

    lines = []

    # VIP 信息
    ok, msg = vip_info(p00001)
    lines.append("  %s %s" % ("✅" if ok else "❌", "VIP: " + msg if ok else msg))
    time.sleep(random.uniform(1, 2))

    # 自然月签到
    ok, msg = task_sign(p00001, p00003)
    lines.append("  %s %s" % ("✅" if ok else "❌", "月签: " + msg if ok else msg))
    time.sleep(random.uniform(1, 2))

    # 网页积分签到
    ok, msg = score_sign(p00001, p00003, dfp)
    lines.append("  %s %s" % ("✅" if ok else "❌", "积分: " + msg if ok else msg))
    time.sleep(random.uniform(1, 2))

    # 会员抽奖
    ok, msg = lottery_draw(p00001, p00003)
    lines.append("  %s %s" % ("✅" if ok else "❌", "抽奖: " + msg if ok else msg))
    time.sleep(random.uniform(1, 2))

    # 摇一摇抽奖
    ok, msg = shake_lottery(p00001)
    lines.append("  %s %s" % ("✅" if ok else "❌", "摇一摇: " + msg if ok else msg))

    any_ok = any("✅" in ln for ln in lines)
    return any_ok, lines


def main():
    accounts = parse_accounts()
    if not accounts:
        print("未配置 Cookie。请在青龙面板环境变量中设置:")
        print("  多账号: IQIYI_COOKIES (每行一条 Cookie)")
        print("  单账号: IQIYI_COOKIE")
        sys.exit(1)

    print("=" * 50)
    print("爱奇艺签到开始,共 %d 个账号" % len(accounts))
    print("=" * 50)

    all_lines = []
    ok_count = 0
    for i, ck in enumerate(accounts, 1):
        label = "账号%d(%s)" % (i, mask_cookie(ck))
        print("\n>>> %s" % label)
        ok, lines = run_account(ck)
        for ln in lines:
            print(ln)
        all_lines.append("【账号%d】%s" % (i, "✅" if ok else "❌"))
        all_lines.extend("  " + ln.strip() for ln in lines)
        if ok:
            ok_count += 1
        time.sleep(random.uniform(2, 4))

    summary = "\n".join(all_lines)
    print("\n" + "=" * 50)
    print(summary)
    print("=" * 50)

    title = "爱奇艺签到: %d/%d 账号成功" % (ok_count, len(accounts))
    channel = send_notify(title, summary)
    print("\n通知渠道: %s" % channel)
    return 0 if ok_count > 0 else 1


if __name__ == "__main__":
    sys.exit(main())
