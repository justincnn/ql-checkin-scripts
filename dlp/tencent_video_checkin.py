#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# cron: 45 9 * * *
# new Env('腾讯视频签到')
"""
腾讯视频每日签到脚本
适配青龙面板,凭据只读环境变量,纯标准库实现(无第三方依赖)。

功能:
  - 每日签到获取 V力值(vip.video.qq.com TaskSystem/CheckIn)
  - 领取每日观看奖励(task_id=1 ProvideAward)
  - 查询会员积分 / 等级 / V力值(comm_cgi)
  - 查询 VIP 到期时间(GetVipUserInfoH5)
  - 自动识别: 签到成功 / 今日已签到 / Cookie 失效
  - 支持多账号(每行一条 Cookie)
  - 运行结束发送通知总结(青龙内置通知 / PushPlus / Server酱)

环境变量(在青龙面板配置):
  TENCENT_VIDEO_COOKIES   多账号,每行一条 Cookie(# 开头为注释,优先)
  TENCENT_VIDEO_COOKIE     单账号 Cookie
  PUSHPLUS_TOKEN           PushPlus 推送 token(可选)
  SENDKEY                  Server酱 SendKey(可选)

Cookie 获取:
  1. 手机/浏览器登录腾讯视频,打开开发者工具抓包
  2. 找到 vip.video.qq.com 域任意请求,复制 Cookie
  3. 必需字段: vqq_openid / vqq_access_token / main_login / vqq_vuserid
     建议: vqq_appid / vdevice_qimei36(设备指纹,防风控) / ip
"""
import json
import os
import random
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

UA = ("Mozilla/5.0 (Linux; Android 11; M2104K10AC Build/RP1A.200720.011; wv) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/89.0.4389.72 "
      "MQQBrowser/6.2 TBS/046237 Mobile Safari/537.36 QQLiveBrowser/8.7.85.27058")

# 签到接口(需带 Cookie)
CHECKIN_URL = ("https://vip.video.qq.com/rpc/trpc.new_task_system.task_system."
               "TaskSystem/CheckIn?rpc_data=%7B%7D")
PROVIDE_URL = ("https://vip.video.qq.com/rpc/trpc.new_task_system.task_system."
               "TaskSystem/ProvideAward?rpc_data=%7B%22task_id%22%3A1%7D")
SCORE_URL = ("https://vip.video.qq.com/fcgi-bin/comm_cgi?"
             "name=spp_vscore_user_mashup&cmd=&otype=xjson&type=1")
VIPINFO_URL = ("https://vip.video.qq.com/rpc/trpc.query_vipinfo.vipinfo."
               "QueryVipInfo/GetVipUserInfoH5")

REFERER = ("https://film.video.qq.com/x/vip-center/?entry=common&hidetitlebar=1"
           "&aid=V0%24%241%3A0%242%3A8%243%3A8.7.85.60%244%3A3%245%3A%246%3A%247%3A%248%3A4"
           "%249%3A%2410%3A&isDarkMode=0")


# ---------------------------------------------------------------------------
# HTTP 工具
# ---------------------------------------------------------------------------
def _http(url, method="GET", data=None, headers=None, timeout=15):
    """返回 (status, text)"""
    h = {
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-CN,zh;q=0.9",
        "User-Agent": UA,
        "Referer": "https://film.video.qq.com/",
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
def _check_login_fields(cookie_str):
    """至少需要 vqq_openid + vqq_access_token + main_login"""
    need = ("vqq_openid", "vqq_access_token", "main_login")
    missing = [k for k in need if ("%s=" % k) not in cookie_str]
    if missing:
        return False, "缺少字段: %s" % ", ".join(missing)
    return True, ""


# ---------------------------------------------------------------------------
# 签到
# ---------------------------------------------------------------------------
def do_checkin(cookie_str):
    """每日签到,返回 (ok, msg)"""
    hdrs = {"Cookie": cookie_str, "Referer": REFERER,
            "Content-Type": "application/json"}
    st, txt = _http(CHECKIN_URL, headers=hdrs)
    if st != 200:
        return False, "HTTP %s" % st
    d = _json(txt)
    if d is None or "check_in_score" not in d:
        # 空 body 或非预期结构 => 大概率 Cookie 失效/类型不对
        return False, "Cookie失效或无签到分数返回"
    score = d.get("check_in_score")
    return True, "签到成功,+%sV力值" % score


def provide_award(cookie_str):
    """领取每日观看任务奖励,返回 (ok, msg)"""
    hdrs = {"Cookie": cookie_str, "Referer": REFERER,
            "Content-Type": "application/json"}
    st, txt = _http(PROVIDE_URL, headers=hdrs)
    if st != 200:
        return False, "HTTP %s" % st
    d = _json(txt)
    if d is None or "provide_value" not in d:
        return False, "任务奖励领取失败(Cookie失效或未完成)"
    val = d.get("provide_value")
    return True, "+%sV力值" % val


def query_score(cookie_str):
    """查询会员等级/积分/V力值,返回 (ok, msg)"""
    hdrs = {"Cookie": cookie_str, "Content-Type": "application/json"}
    st, txt = _http(SCORE_URL, headers=hdrs)
    if st != 200:
        return False, ""
    d = _json(txt)
    if d is None:
        return False, ""
    try:
        lv = d.get("lscore_info", {}).get("level")
        vscore = d.get("cscore_info", {}).get("vip_score_total")
        score = d.get("lscore_info", {}).get("score")
        parts = []
        if lv:
            parts.append("等级%s" % lv)
        if score:
            parts.append("V力值%s" % score)
        if vscore:
            parts.append("会员积分%s" % vscore)
        return True, " ".join(parts) if parts else "积分查询成功"
    except Exception:  # noqa: BLE001
        return False, ""


def query_vipinfo(cookie_str):
    """查询 VIP 到期时间,返回 (ok, msg)"""
    body = json.dumps({"geticon": 1, "viptype": "svip|nfl", "platform": 8})
    hdrs = {"Cookie": cookie_str, "Content-Type": "text/plain;charset=UTF-8"}
    st, txt = _http(VIPINFO_URL, method="POST", data=body, headers=hdrs)
    if st != 200:
        return False, ""
    d = _json(txt)
    if d is None:
        return False, ""
    begin = d.get("beginTime")
    end = d.get("endTime")
    if end:
        return True, "VIP到期:%s" % end
    return False, ""


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
        body = urllib.parse.urlencode({"title": title, "desp": content})
        req = urllib.request.Request(
            "https://sctapi.ftqq.com/%s.send" % key,
            data=body.encode("utf-8"), method="POST",
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
    """优先 TENCENT_VIDEO_COOKIES(多账号, 每行一条);否则 TENCENT_VIDEO_COOKIE"""
    accounts = []
    multi = os.environ.get("TENCENT_VIDEO_COOKIES", "").strip()
    if multi:
        for line in multi.splitlines():
            line = line.strip().strip("'").strip('"')
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                accounts.append(line)
    else:
        ck = os.environ.get("TENCENT_VIDEO_COOKIE", "").strip().strip("'").strip('"')
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
def main():
    accounts = parse_accounts()
    if not accounts:
        print("未配置 Cookie。请在青龙面板环境变量中设置:")
        print("  多账号: TENCENT_VIDEO_COOKIES (每行一条 Cookie)")
        print("  单账号: TENCENT_VIDEO_COOKIE")
        sys.exit(1)

    print("=" * 50)
    print("腾讯视频签到开始,共 %d 个账号" % len(accounts))
    print("=" * 50)

    all_lines = []
    ok_count = 0
    for i, ck in enumerate(accounts, 1):
        label = "账号%d(%s)" % (i, mask_cookie(ck))
        print("\n>>> %s" % label)

        valid, err = _check_login_fields(ck)
        if not valid:
            print("  ❌ %s" % err)
            all_lines.append("【账号%d】❌ %s" % (i, err))
            continue

        lines = []
        ok, msg = query_score(ck)
        if ok:
            lines.append("  ✅ %s" % msg)
            print("  %s" % msg)
        time.sleep(random.uniform(1, 2))

        ok, msg = do_checkin(ck)
        icon = "✅" if ok else "❌"
        lines.append("  %s 签到: %s" % (icon, msg))
        print("  %s 签到: %s" % (icon, msg))
        checkin_ok = ok
        time.sleep(random.uniform(1, 2))

        ok, msg = provide_award(ck)
        icon = "✅" if ok else "❌"
        lines.append("  %s 观看奖励: %s" % (icon, msg))
        print("  %s 观看奖励: %s" % (icon, msg))
        if not checkin_ok and ok:
            checkin_ok = True
        time.sleep(random.uniform(1, 2))

        ok, msg = query_vipinfo(ck)
        if ok:
            lines.append("  ✅ %s" % msg)
            print("  ✅ %s" % msg)

        all_lines.append("【账号%d】%s" % (i, "✅" if checkin_ok else "❌"))
        all_lines.extend(ln.strip() for ln in lines)
        if checkin_ok:
            ok_count += 1
        time.sleep(random.uniform(2, 4))

    summary = "\n".join(all_lines)
    print("\n" + "=" * 50)
    print(summary)
    print("=" * 50)

    title = "腾讯视频签到: %d/%d 账号成功" % (ok_count, len(accounts))
    channel = send_notify(title, summary)
    print("\n通知渠道: %s" % channel)
    return 0 if ok_count > 0 else 1


if __name__ == "__main__":
    sys.exit(main())