#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""有道云笔记签到 — 青龙单文件版(源自 arcturus-script/youdao 精简)
env: YOUDAO_COOKIE = note.youdao.com 网页登录 Cookie
输出纯文本, 由 notify_wrap.sh 推 TG。
"""
import json
import os
import re
import sys

import requests as req

LOGIN_URL = "http://note.youdao.com/login/acc/pe/getsess?product=YNOTE"
SYNC_URL = "https://note.youdao.com/yws/api/daupromotion?method=sync"
CHECKIN_URL = "https://note.youdao.com/yws/mapi/user?method=checkin"
AD_URL = "https://note.youdao.com/yws/mapi/user?method=adRandomPrompt"
USER_INFO = "https://note.youdao.com/yws/api/self"


def div_mb(v):
    try:
        return round(v / (1024 * 1024), 2)
    except (TypeError, ValueError):
        return None


class Youdao:
    def __init__(self, cookie):
        self.cookies = cookie
        self.headers = {"Cookie": self.cookies}

    def login(self):
        cookie = ""
        res = req.get(LOGIN_URL, headers=self.headers, timeout=15, allow_redirects=False)
        for key, value in res.cookies.items():
            cookie += f"{key}={value};"
        if cookie:
            self.headers = {"Cookie": cookie}
            print(f"[login] 刷新区间 cookie: {cookie}")

    def checkin(self):
        res = req.post(SYNC_URL, headers=self.headers, timeout=15, allow_redirects=True).json()
        print(f"[sync] {res}")
        out = {"status": False, "msg": "登录奖励获取失败"}
        if "error" not in res:
            result = req.post(CHECKIN_URL, headers=self.headers, timeout=15).json()
            print(f"[checkin] {result}")
            out = {
                "status": True,
                "reward": div_mb(res.get("rewardSpace")),
                "continuous_days": res.get("continuousDays"),
                "total": div_mb(res.get("totalRewardSpace")),
                "checkin": div_mb(result.get("space")),
                "checkin_total": div_mb(result.get("total")),
                "raw_checkin": result,
            }
        return out

    def ad(self):
        ad = 0
        last = {}
        for _ in range(3):
            resp = req.post(AD_URL, headers=self.headers, timeout=15).json()
            print(f"[ad] {resp}")
            last = resp
            ad += resp.get("space") or 0
        return {
            "status": True,
            "ad_space_total": div_mb(last.get("adSpaceTotal")),
            "ad": div_mb(ad),
        }

    def start(self):
        self.login()
        name, account = "Unknown", "Unknown"
        try:
            res = req.get(USER_INFO, headers=self.headers, params={"method": "get"}, timeout=15).json()
            name = res.get("name", name)
            account = res.get("userId", account)
        except Exception as ex:
            print(f"[warn] 用户信息获取失败: {ex}")
        print(f"[user] {account} - {name}")
        r = self.checkin()
        print(f"[msg] 签到: {'成功' if r['status'] else r['msg']} | 登录奖励 {r.get('reward')}M | 连续 {r.get('continuous_days')} 天 | 签到奖励 {r.get('checkin')}M")
        if r["status"]:
            a = self.ad()
            print(f"[msg] 广告奖励 {a.get('ad')}M (总计 {a.get('ad_space_total')}M)")
        else:
            print("[msg] 签到失败, 跳过看广告")


def main():
    cookie = os.environ.get("YOUDAO_COOKIE", "").strip()
    if not cookie:
        print("❌ 缺 YOUDAO_COOKIE 环境变量")
        sys.exit(1)
    Youdao(cookie).start()


if __name__ == "__main__":
    main()