#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""值得买每日抽奖(SMZDM) - 青龙版。依赖 requests。env: SMZDM_COOKIE
从转盘活动页动态解析当前有效活动 hashId 再抽 (活动 ID 会过期, 不能硬编码)"""
import os, re, sys, time, requests

COOKIE = os.getenv("SMZDM_COOKIE", "").strip()
ACTIVITY_PAGES = [
    "https://m.smzdm.com/topic/bwrzf5/516lft",       # 值会员转盘
    "https://m.smzdm.com/topic/zhyzhuanpan/cjzp/",   # 生活转盘
]
UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 15_6 like Mac OS X) AppleWebKit/605.1.15 "
      "(KHTML, like Gecko) Mobile/15E148/smzdm 10.4.6 rv:130.1")


def web_headers():
    return {"User-Agent": UA, "Cookie": COOKIE, "Accept": "*/*",
            "x-requested-with": "com.smzdm.client.android", "Referer": "https://m.smzdm.com/"}


def get_active_id(url):
    r = requests.get(url, headers=web_headers(), timeout=15)
    m = re.search(r'hashId\\?"\s*:\s*\\?"([a-zA-Z0-9_-]+)', r.text)
    return m.group(1) if m else ""


def draw(aid):
    r = requests.get(f"https://zhiyou.smzdm.com/user/lottery/jsonp_draw",
                     params={"active_id": aid, "callback": f"jQuery34107538452897131465_{int(time.time()*1000)}"},
                     headers=web_headers(), timeout=15)
    text = r.text
    m = re.search(r'"error_msg"\s*:\s*"([^"]*)"', text)
    if m:
        return m.group(1)
    m2 = re.search(r'"prizeName"\s*:\s*"([^"]*)"', text)
    if m2:
        return "🎉 " + m2.group(1)
    m3 = re.search(r'"name"\s*:\s*"([^"]*)"', text)
    if m3:
        return "🎉 " + m3.group(1)
    return text[:80]


def main():
    if not COOKIE:
        print("❌ 未配置 SMZDM_COOKIE")
        return 1
    msgs = []
    for url in ACTIVITY_PAGES:
        try:
            aid = get_active_id(url)
            label = url.rstrip("/").split("/")[-1]
            if not aid:
                msgs.append(f"{label}: 未找到活动")
                continue
            time.sleep(1 + len(msgs))
            msgs.append(f"{label}: {draw(aid)}")
        except Exception as e:
            msgs.append(f"{type(e).__name__}: {e}")
    print("🎁 值得买抽奖: " + " | ".join(msgs))
    return 0


if __name__ == "__main__":
    sys.exit(main())