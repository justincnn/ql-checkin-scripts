#!/usr/bin/python3
# -*- coding: utf-8 -*-
"""
立创开源硬件平台（oshwhub.com）自动签到 - 青龙版
Cookie 方式：手动抓取登录态 Cookie，每日签到获取积分并展示当前总积分。

cron: 10 8 * * *
new Env('立创开源硬件平台自动签到')
"""

import gzip
import json
import os
import random
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib
from datetime import datetime

# ---------------- 统一通知模块加载 ----------------
hadsend = False
send = None
try:
    from notify import send
    hadsend = True
    print("✅ 已加载 notify.py 通知模块")
except ImportError:
    print("⚠️ 未加载通知模块，跳过通知功能")

# ---------------- 配置项 ----------------
random_signin = os.getenv("RANDOM_SIGNIN", "true").lower() == "true"        # 是否启用签到前随机延迟
max_random_delay = int(os.getenv("MAX_RANDOM_DELAY", "1800"))               # 每个账号签到前最大随机延迟（秒）
x_forwarded_for = os.getenv("X_FORWARDED_FOR", "").strip()                  # 可选：伪装来源 IP

# ---------------- 接口地址 ----------------
GET_PROFILE_URL = "https://oshwhub.com/api/users/getSignInProfile"          # 查询签到状态与积分
SIGN_IN_URL = "https://oshwhub.com/api/users/signIn"                        # 执行签到
SIGN_PAGE = "https://oshwhub.com/sign_in"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
REQUEST_TIMEOUT = 20
RETRY_COUNT = 3


# ---------------- 工具函数 ----------------
def mask_cookie(cookie):
    """Cookie 脱敏显示（Cookie 为敏感凭证，始终脱敏）"""
    if not cookie:
        return "未知"
    if len(cookie) <= 20:
        return "*" * len(cookie)
    return f"{cookie[:12]}...{cookie[-8:]}"


def extract_customer_code(cookie):
    """从 Cookie 中提取 jlc_customer_code 的值，作为账号名"""
    if not cookie:
        return None
    for part in cookie.split(";"):
        part = part.strip()
        if part.startswith("jlc_customer_code="):
            val = part.split("=", 1)[1].strip()
            return val or None
    return None


def account_name(cookie):
    """账号显示名：优先取 jlc_customer_code，否则脱敏 Cookie"""
    code = extract_customer_code(cookie)
    return code if code else mask_cookie(cookie)


def parse_cookies(raw):
    """解析 Cookie 配置，每行一个完整 Cookie 字符串，返回 [cookie, ...]"""
    cookies = []
    for line in (raw or "").replace("\r\n", "\n").split("\n"):
        line = line.strip()
        if line and not line.startswith("#"):
            cookies.append(line)
    return cookies


def notify_user(title, content):
    """统一通知函数"""
    if hadsend:
        try:
            send(title, content)
            print(f"✅ 通知发送完成: {title}")
            return True
        except Exception as e:
            print(f"❌ 通知发送失败: {e}")
            return False
    else:
        print(f"📢 {title}\n📄 {content}")
        return True


# ---------------- HTTP 客户端 ----------------
class HttpClient:
    """基于标准库 urllib 的客户端，携带 Cookie 访问接口。"""

    def __init__(self, cookie):
        self.cookie = cookie
        self.opener = urllib.request.build_opener()

    def _request(self, url, method="GET", data=None, referer=None):
        headers = {
            "User-Agent": USER_AGENT,
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "zh-CN,zh;q=0.9",
            "Connection": "keep-alive",
            "Cookie": self.cookie,
        }
        if referer:
            headers["Referer"] = referer
        if x_forwarded_for:
            headers["X-Forwarded-For"] = x_forwarded_for

        body = None
        if data is not None:
            if isinstance(data, dict):
                body = urllib.parse.urlencode(data).encode("utf-8")
            else:
                body = data
            headers["Content-Type"] = "application/x-www-form-urlencoded"

        req = urllib.request.Request(url, data=body, headers=headers, method=method)
        try:
            resp = self.opener.open(req, timeout=REQUEST_TIMEOUT)
        except urllib.error.HTTPError as e:
            resp = e
        except urllib.error.URLError as e:
            raise RuntimeError(f"网络错误: {e.reason}")

        raw = resp.read()
        encoding = (resp.headers.get("Content-Encoding") or "").lower()
        try:
            if "gzip" in encoding:
                raw = gzip.decompress(raw)
            elif "deflate" in encoding:
                try:
                    raw = zlib.decompress(raw)
                except zlib.error:
                    raw = zlib.decompress(raw, -zlib.MAX_WBITS)
        except Exception:
            pass
        return resp.getcode(), raw

    def get(self, url, referer=None):
        return self._request(url, "GET", referer=referer)

    def post(self, url, data, referer=None):
        return self._request(url, "POST", data=data, referer=referer)


# ---------------- 签到主体 ----------------
class Oshwhub:
    name = "立创开源硬件平台"

    def __init__(self, cookie, index=1):
        self.cookie = cookie
        self.index = index
        self.http = HttpClient(cookie)

    @staticmethod
    def _parse_json(raw, what):
        """解析接口 JSON，处理 WAF 拦截（HTML）、401 未登录等异常"""
        text = raw.decode("utf-8", errors="ignore")
        try:
            data = json.loads(text)
        except (json.JSONDecodeError, ValueError):
            # 返回 HTML 通常是 CloudWAF 拦截
            if "访问被拦截" in text or "CloudWAF" in text:
                raise ValueError(f"{what}被 WAF 拦截，Cookie 可能不完整")
            raise ValueError(f"{what}返回非 JSON")
        if not isinstance(data, dict):
            raise ValueError(f"{what}返回数据异常")
        if data.get("code") == 401:
            raise PermissionError("Cookie 已失效（未登录或已过期）")
        return data

    def get_profile(self):
        """查询签到状态与积分"""
        url = f"{GET_PROFILE_URL}?_t={int(time.time() * 1000)}"
        _, raw = self.http.get(url, referer=SIGN_PAGE)
        data = self._parse_json(raw, "查询签到状态")
        result = data.get("result")
        if not isinstance(result, dict):
            raise ValueError(f"返回数据异常: {data.get('message', data)}")
        return result

    def sign_in(self):
        """执行签到"""
        _, raw = self.http.post(
            SIGN_IN_URL, {"_t": int(time.time() * 1000)}, referer=SIGN_PAGE
        )
        return self._parse_json(raw, "签到")

    def run(self):
        print(f"\n==== 账号{self.index}（{account_name(self.cookie)}）开始签到 ====")

        for attempt in range(1, RETRY_COUNT + 1):
            try:
                return self._do_run()
            except (RuntimeError, ValueError, PermissionError) as error:
                print(f"⚠️ 第 {attempt}/{RETRY_COUNT} 次失败：{error}")
                if attempt < RETRY_COUNT:
                    wait = attempt * 10
                    print(f"⏳ {wait} 秒后重试")
                    time.sleep(wait)
                    continue
                msg = f"失败：{error}"
                print(f"❌ {msg}")
                return self._build_message(msg, False), False

        return self._build_message("未知错误", False), False

    def _do_run(self):
        before = self.get_profile()
        total_before = before.get("total_point")
        is_signed = before.get("isTodaySignIn")

        if is_signed:
            msg = "今日已签到"
            if total_before is not None:
                msg += f"，当前总积分：{total_before}"
            if before.get("week_signIn_days") is not None:
                msg += f"，本周签到 {before['week_signIn_days']} 天"
            if before.get("month_signIn_days") is not None:
                msg += f"，本月连续签到 {before['month_signIn_days']} 天"
            print(f"✅ {msg}")
            return self._build_message(msg, True), True

        # 执行签到
        sign_result = self.sign_in()

        # 重新查询，计算本次获得与总积分
        after = self.get_profile()
        total_after = after.get("total_point")

        # 签到接口明确失败，且重查后仍未签到，判定失败
        if sign_result.get("success") is False and not after.get("isTodaySignIn"):
            msg = str(sign_result.get("message") or "签到失败")
            print(f"❌ {msg}")
            return self._build_message(msg, False), False

        gained = 0
        if total_before is not None and total_after is not None:
            gained = total_after - total_before

        msg = "签到成功"
        if gained > 0:
            msg += f"，本次获得 {gained} 积分"
        if total_after is not None:
            msg += f"，当前总积分：{total_after}"
        if after.get("week_signIn_days") is not None:
            msg += f"，本周签到 {after['week_signIn_days']} 天"
        if after.get("month_signIn_days") is not None:
            msg += f"，本月连续签到 {after['month_signIn_days']} 天"
        print(f"✅ {msg}")
        return self._build_message(msg, True), True

    def _build_message(self, result_msg, is_success):
        msg = f"🌟 立创开源硬件平台签到结果\n\n👤 账号: {account_name(self.cookie)}"
        msg += f"\n📝 结果: {result_msg}"
        msg += f"\n⏰ 时间: {datetime.now().strftime('%m-%d %H:%M')}"
        return msg


# ---------------- 主程序 ----------------
def main():
    print(f"==== 立创开源硬件平台自动签到开始 - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ====")

    raw = os.getenv("OSHW_COOKIE", "")
    if not raw:
        error_msg = """❌ 未找到 OSHW_COOKIE 环境变量

🔧 配置方法：
1. 用浏览器登录 https://oshwhub.com
2. 打开开发者工具（F12）→ Network
3. 刷新签到页 https://oshwhub.com/sign_in
4. 找到 getSignInProfile 请求
5. 复制 Request Headers 中的完整 Cookie 值
6. 在青龙面板添加环境变量 OSHW_COOKIE，粘贴该 Cookie
7. 多账号使用换行符分隔，每行一个 Cookie"""
        print(error_msg)
        notify_user("立创开源硬件平台签到失败", error_msg)
        return

    cookies = parse_cookies(raw)
    if not cookies:
        print("❌ Cookie 解析失败，请检查 OSHW_COOKIE 的格式")
        notify_user("立创开源硬件平台签到失败", "Cookie 解析失败，请检查 OSHW_COOKIE 的格式")
        return

    print(f"📝 共发现 {len(cookies)} 个账号")

    total = len(cookies)
    results = []
    lock = threading.Lock()
    state = {"success_count": 0, "has_failure": False}

    def worker(idx, cookie):
        is_success = False
        result_msg = ""
        try:
            if random_signin:
                delay = random.randint(0, max_random_delay)
                print(f"⏳ 账号{idx + 1} 随机延迟 {delay} 秒后签到...")
                time.sleep(delay)

            obj = Oshwhub(cookie, idx + 1)
            result_msg, is_success = obj.run()

            status = "成功" if is_success else "失败"
            notify_ok = notify_user(
                f"立创开源账号{idx + 1}签到{status}", result_msg
            )

            with lock:
                if is_success:
                    state["success_count"] += 1
                if not is_success or not notify_ok:
                    state["has_failure"] = True
                results.append({"index": idx + 1, "success": is_success})
        except Exception as e:
            err = f"账号{idx + 1}: 执行异常 - {e}"
            print(f"❌ {err}")
            notify_user(f"立创开源账号{idx + 1}签到失败", err)
            with lock:
                state["has_failure"] = True

    threads = [
        threading.Thread(target=worker, args=(i, c))
        for i, c in enumerate(cookies)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    success_count = state["success_count"]
    has_failure = state["has_failure"]

    if total > 1:
        summary = f"""📊 立创开源硬件平台签到汇总

📈 总计: {total} 个账号
✅ 成功: {success_count} 个
❌ 失败: {total - success_count} 个
📊 成功率: {success_count / total * 100:.1f}%
⏰ 完成时间: {datetime.now().strftime('%m-%d %H:%M')}"""
        notify_user("立创开源硬件平台签到汇总", summary)

    print(
        f"\n==== 立创开源硬件平台自动签到完成 - "
        f"成功{success_count}/{total} - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ===="
    )

    if has_failure:
        sys.exit(1)


if __name__ == "__main__":
    main()
