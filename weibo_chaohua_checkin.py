#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""微博超话自动签到 - 青龙版。依赖 requests。env: WEIBO_CHAOHUA_COOKIE (需含 XSRF-TOKEN)
自动拉取关注的所有超话并逐个签到。"""
import json, os, random, re, sys, time, requests

COOKIE = os.getenv("WEIBO_CHAOHUA_COOKIE", "").strip()
SPA_URL = "https://weibo.com/ajax/getSpaConfig"
LIST_URL = "https://weibo.com/ajax/profile/topicContent"
SIGN_URL = "https://weibo.com/p/aj/general/button"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/125.0.0.0 Safari/537.36")


def sess():
    s = requests.Session()
    s.headers.update({"User-Agent": UA, "Accept": "application/json, text/plain, */*",
                      "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8", "Referer": "https://weibo.com/",
                      "X-Requested-With": "XMLHttpRequest"})
    s.headers["Cookie"] = COOKIE
    m = re.search(r"XSRF-TOKEN=([^;]+)", COOKIE)
    if m:
        s.headers["X-XSRF-TOKEN"] = m.group(1)
    return s


def get_uid(s):
    r = s.get(SPA_URL, timeout=15)
    d = r.json()
    if d.get("ok") != 1:
        raise Exception("SPA not ok, cookie 可能失效")
    uid = str(d.get("data", {}).get("uid") or "")
    if not uid.isdigit() or not 5 <= len(uid) <= 20:
        raise Exception("未识别到登录 UID")
    return uid


def get_list(s, uid):
    out, page = [], 1
    while True:
        r = s.get(LIST_URL, params={"tabid": "231093_-_chaohua", "page": page, "uid": uid},
                  headers={"Referer": f"https://weibo.com/u/page/follow/{uid}/231093_-_chaohua"}, timeout=15)
        d = r.json()
        if d.get("ok") != 1:
            raise Exception("超话列表失败: " + str(d.get("msg")))
        data = d.get("data", {})
        items = data.get("list", []) or []
        for it in items:
            if isinstance(it, dict) and str(it.get("oid", "")).startswith("1022:"):
                cid = it["oid"][5:]; name = it.get("topic_name", "")
                if cid and name and cid not in {x["id"] for x in out}:
                    out.append({"id": cid, "name": name})
        if page >= int(data.get("max_page") or 1):
            break
        page += 1
        time.sleep(0.8)
    return out


def sign_one(s, cid, name):
    r = s.get(SIGN_URL, params={"api": "http://i.huati.weibo.com/aj/super/checkin",
                                "id": cid, "location": "page_100808_super_index",
                                "__rnd": int(time.time() * 1000)},
              headers={"Referer": f"https://weibo.com/p/{cid}/super_index"}, timeout=15)
    d = r.json()
    code = str(d.get("code", ""))
    return code in ("100000", "382004", "382010"), code, d.get("msg", ""), code == "382004"


def main():
    if not COOKIE:
        print("❌ 未配置 WEIBO_CHAOHUA_COOKIE (需含 XSRF-TOKEN)")
        return 1
    s = sess()
    try:
        uid = get_uid(s)
    except Exception as e:
        print("❌", e)
        return 1
    print(f"账号 UID {uid[:2]}***{uid[-2:]}")
    lst = get_list(s, uid)
    if not lst:
        print("✅ 无超话需要签到")
        return 0
    ok = already = fail = 0
    for i, ch in enumerate(lst, 1):
        succ, code, msg, dup = sign_one(s, ch["id"], ch["name"])
        tag = "已签" if dup else ("✅" if succ else "❌")
        print(f"  [{i}/{len(lst)}] {ch['name']}: {tag} {msg}")
        if dup: already += 1
        elif succ: ok += 1
        else: fail += 1
        if i < len(lst):
            time.sleep(3 + random.uniform(0.5, 1.5))
    print(f"📊 微博超话: 成功{ok} 已签{already} 失败{fail} 总计{len(lst)}")
    return 0 if fail == 0 else 2


if __name__ == "__main__":
    import re  # noqa
    sys.exit(main())