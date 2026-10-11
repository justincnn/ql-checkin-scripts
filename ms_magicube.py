#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ModelScope 魔搭社区每日魔粒 · 多账号（纯标准库，自用）

魔粒体系逆向结论（2026-10-11，参考 cnb.cool/codeLong1024/modelscope-magicube 并实测）:
1. 没有签到接口。daily_active(+200) 与 aliyun_bindlogin(+50) 是**会话副作用**——
   当天首次带登录态访问站点即自动入账（有去重），访问首页+魔粒用量页即可触发。
2. interaction_like「收藏/喜欢」+2/次，每日上限 20 次（+40/天）。
   star/unstar 可反复刷: PUT stars 入账 +2，PUT unstar 不扣分不减次数，
   同一模型 star→unstar 循环即可拿满。
3. interaction_comment「发起高质量讨论」+5×2，需真实评论且服务端审核，不自动刷。
4. 短期魔粒 24h 过期（expire_days=1），定时任务每天跑正好循环利用。
5. 魔粒接口认网站登录 cookie（m_session_id/h_uid/csrf_token），不需要 Bearer Token，
   cookie 有效期约 30~60 天。

接口:
  GET  /openapi/v1/magicubes/earn/rules  任务规则与今日进度（today_used/today_remain/amount）
  GET  /openapi/v1/magicubes/balance     余额
  打卡 GET https://www.modelscope.cn/ 与 /magicube/usage（会话副作用触发发放）
  点赞 PUT /api/v1/models/{owner}/{name}/stars    Data.Stars +1
  取消 PUT /api/v1/models/{owner}/{name}/unstar   Data.Stars -1

配置 ms_config.json（脚本自动维护）:
  {"accounts": [{"name": "h_uid:2220372498861", "cookie": "<整行>"}], "updated": iso}
  ★ 以本文件为权威值; 青龙 env MS_COOKIE 只在**首次(无本文件时)**做全量种子
    （多账号用换行分隔）。

用法:
  python ms_magicube.py init "<cookie整行>"   # 追加/更新一个账号（推荐）
  python ms_magicube.py status                # 全账号查余额+今日进度
  python ms_magicube.py auto                  # 打卡+刷点赞（幂等，定时任务入口）
  python ms_magicube.py rules                 # 只看规则表（首个账号）
"""
import json
import os
import sys
import time
import datetime
import urllib.request
import urllib.error
import ssl

HERE = os.path.dirname(os.path.abspath(__file__))
CFG_PATH = os.path.join(HERE, "ms_config.json")
LOG_PATH = os.path.join(HERE, "ms_magicube.log")

BASE = "https://www.modelscope.cn"
URL_RULES = BASE + "/openapi/v1/magicubes/earn/rules"
URL_BALANCE = BASE + "/openapi/v1/magicubes/balance"
# 刷分用的模型（热门公开模型，star/unstar 循环不改变其净赞数）
FARM_MODEL = "qwen/Qwen2.5-7B-Instruct"
URL_STAR = BASE + "/api/v1/models/" + FARM_MODEL + "/stars"
URL_UNSTAR = BASE + "/api/v1/models/" + FARM_MODEL + "/unstar"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36")
TIMEOUT = 30
CTX = ssl.create_default_context()

# 点赞刷分的保守策略(2026-10-11 实测: 高频连刷 star 会被服务端吊销 m_session_id)
# 保守模式 = 每次最多刷 LIKE_MAX_ROUNDS 轮、间隔 LIKE_DELAY 秒, 宁可少拿也不吊销会话
LIKE_MAX_ROUNDS = 3
LIKE_DELAY = 3.0

# 可自动化的规则 key 及其含义
LIKE_KEY = "interaction_like"
DAILY_KEY = "daily_active"
ALIYUN_KEY = "aliyun_bindlogin"

TG_NAMES = ("TG_BOT_TOKEN", "TG_USER_ID", "TG_CHAT_ID", "TG_PROXY_HOST", "TG_PROXY_PORT")


def log(msg):
    line = "[%s] %s" % (datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), msg)
    print(line)
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def cookie_uid(cookie):
    """从 cookie 里取 h_uid 当账号标识（抓不到就用短哈希）。"""
    for part in cookie.split(";"):
        p = part.strip()
        if p.startswith("h_uid="):
            return "h_uid:" + p[6:].strip()
    import hashlib
    return "cookie:" + hashlib.md5(cookie.encode()).hexdigest()[:8]


def save_cfg(cfg):
    cfg["updated"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(CFG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


def load_cfg():
    """读配置。文件为准; 文件不存在时才用 env MS_COOKIE(换行分隔)做全量种子。"""
    cfg = None
    if os.path.exists(CFG_PATH):
        try:
            with open(CFG_PATH, "r", encoding="utf-8") as f:
                cfg = json.load(f)
        except Exception:
            return {"accounts": []}
    if isinstance(cfg, dict):
        if "accounts" not in cfg and cfg.get("cookie"):
            cfg = {"accounts": [{"name": cookie_uid(cfg["cookie"]), "cookie": cfg["cookie"]}]}
        cfg.setdefault("accounts", [])
        return cfg
    if cfg is not None:
        return {"accounts": []}

    env_ck = (os.environ.get("MS_COOKIE") or "").strip()
    accs = []
    for ck in [x.strip() for x in env_ck.splitlines() if x.strip()]:
        accs.append({"name": cookie_uid(ck), "cookie": ck})
    if accs:
        log("[cfg] 首次初始化: 从 env MS_COOKIE 载入 %d 个账号" % len(accs))
    return {"accounts": accs}


def upsert_account(cfg, cookie):
    name = cookie_uid(cookie)
    for a in cfg["accounts"]:
        if a.get("name") == name:
            a["cookie"] = cookie
            return a, "更新"
    acc = {"name": name, "cookie": cookie}
    cfg["accounts"].append(acc)
    return acc, "新增"


def _req(method, url, cookie, body=None):
    req = urllib.request.Request(url, data=body, method=method)
    req.add_header("User-Agent", UA)
    req.add_header("cookie", cookie)
    req.add_header("x-modelscope-accept-language", "zh_CN")
    req.add_header("Accept", "application/json, text/plain, */*")
    req.add_header("Referer", BASE + "/")
    req.add_header("Origin", BASE)
    if body is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT, context=CTX) as r:
            raw = r.read().decode("utf-8", "replace")
            return r.status, raw
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except Exception as e:
        return 0, str(e)


def _json(raw):
    try:
        return json.loads(raw)
    except Exception:
        return None


# ---------------------------------------------------------------- 规则与余额
def fetch_rules(cookie):
    """返回 {rule_key: 规则dict}"""
    st, raw = _req("GET", URL_RULES, cookie)
    if st != 200:
        return None, st, raw
    data = _json(raw)
    if not data or not data.get("success"):
        return None, st, raw
    rules = {}
    for it in (data.get("data") or []):
        rules[it.get("rule_key")] = it
    return rules, st, raw


def fetch_balance(cookie):
    st, raw = _req("GET", URL_BALANCE, cookie)
    if st != 200:
        return None, st
    data = _json(raw)
    if not data or not data.get("success"):
        return None, st
    d = data.get("data") or {}
    if isinstance(d, dict):
        for k in ("balance", "Balance", "amount", "Amount"):
            if k in d:
                return d[k], st
        return d, st
    return d, st


# ---------------------------------------------------------------- 打卡（会话副作用）
def daily_checkin(cookie):
    """访问首页+魔粒用量页，触发每日登录魔粒发放。返回访问成功数。"""
    ok = 0
    for url in (BASE + "/", BASE + "/magicube/usage"):
        st, _ = _req("GET", url, cookie)
        if st == 200:
            ok += 1
        else:
            log("[打卡] 访问失败 HTTP %s: %s" % (st, url))
    return ok


# ---------------------------------------------------------------- 点赞刷分
def like_once(cookie):
    """PUT stars -> PUT unstar，完成 1 次有效互动（+2 魔粒）。
    返回 (ok, msg)。unstar 失败不致命（净赞数多 1 无所谓）。"""
    st, raw = _req("PUT", URL_STAR, cookie, body=b"{}")
    if st != 200:
        j = _json(raw) or {}
        msg = j.get("Message") or raw[:120]
        if st == 401 or "not logged in" in str(msg):
            # 已知限制: stars 接口比 cookie 端点要求更高(需站点侧完整登录会话),
            # 同一 cookie 能查魔粒余额却过不了 star 鉴权。属服务端鉴权口径差异, 非脚本 bug。
            return False, "star HTTP %s %s —【点赞需更高登录态】" % (st, msg)
        return False, "star HTTP %s %s" % (st, msg)
    st2, raw2 = _req("PUT", URL_UNSTAR, cookie, body=b"{}")
    if st2 != 200:
        j = _json(raw2) or {}
        # unstar 失败只是没回退净赞数，互动已计入，不判失败
        return True, "ok（unstar 失败 HTTP %s，净赞数未回退）" % st2
    return True, "ok"


def _daily_credit(rules):
    n = 0
    for k in (DAILY_KEY, ALIYUN_KEY):
        r = rules.get(k) or {}
        n += int(r.get("today_earned_amount") or 0)
    return n


def _like_progress(rules):
    """(今日已得, 今日剩余次数)"""
    r = rules.get(LIKE_KEY) or {}
    used = int(r.get("today_used") or 0)
    remain = int(r.get("today_remain") or 0)
    return int(r.get("today_earned_amount") or 0), remain


# ---------------------------------------------------------------- 命令
def cmd_init(args):
    """init <cookie整行> —— 支持 "a=1; b=2" 或 fetch 头里的 cookie 值"""
    raw = args[0] if args else ""
    if not raw:
        print("用法: python ms_magicube.py init \"<Cookie整行>\"")
        sys.exit(1)
    cookie = " ".join(raw.split())  # 清洗：去换行/首尾空白
    cfg = load_cfg()
    acc, how = upsert_account(cfg, cookie)
    rules, st, _ = fetch_rules(cookie)
    if rules is None:
        print("[x] %s %s 但校验失败 HTTP %s（cookie 可能无效或需重抓）" % (how, acc["name"], st))
        sys.exit(3)
    bal, _ = fetch_balance(cookie)
    save_cfg(cfg)
    print("[ok] %s 账号 %s，魔粒余额: %s · 当前共 %d 个账号" % (how, acc["name"], bal, len(cfg["accounts"])))
    print("     可自动化进度: 登录 %s/250 · 点赞 %s/40" % (
        _daily_credit(rules), _like_progress(rules)[0]))


def cmd_rules(args):
    cfg = load_cfg()
    if not cfg["accounts"]:
        print("[x] 未配置 cookie，请先 init")
        sys.exit(2)
    rules, st, raw = fetch_rules(cfg["accounts"][0]["cookie"])
    if rules is None:
        log("[x] 查询失败 HTTP %s" % st)
        sys.exit(3)
    for k, r in sorted(rules.items(), key=lambda x: -(int(x[1].get("amount") or 0))):
        log("  %-40s +%s/次 剩余%s/天cap%s 已得%s | %s" % (
            k, r.get("amount"), r.get("today_remain"), r.get("daily_cap"),
            r.get("today_earned_amount"), r.get("title")))


def run_one(acc, do_farm):
    """处理单账号。返回 (成功?, 播报行 list)。"""
    cookie = acc["cookie"]
    lines = []
    rules, st, raw = fetch_rules(cookie)
    if rules is None:
        lines.append("[x] 查询规则失败 HTTP %s，cookie 可能失效: %s" % (st, (raw or "")[:150]))
        return False, lines
    bal0, _ = fetch_balance(cookie)
    lines.append("[开始] 余额 %s · 登录已得 %s/250 · 点赞已得 %s/40（剩余 %s 次）" % (
        bal0, _daily_credit(rules), _like_progress(rules)[0], _like_progress(rules)[1]))

    # 打卡（触发每日登录发放）
    before = _daily_credit(rules)
    ok = daily_checkin(cookie)
    time.sleep(1)
    rules, _, _ = fetch_rules(cookie)
    if rules is None:
        lines.append("[x] 打卡后重查规则失败，cookie 可能失效")
        return False, lines
    after = _daily_credit(rules)
    if after > before:
        lines.append("[打卡] ✅ 本次 +%s（访问 %s/2 页成功，触发每日登录发放）" % (after - before, ok))
    elif after > 0:
        lines.append("[打卡] ℹ️ 今日登录魔粒已发放（%s），重复访问无新增属正常" % after)
    else:
        lines.append("[打卡] ⚠️ 今日未发放且累计为 0，登录态可能未真正建立（请重抓 cookie）")

    # 刷点赞
    got, remain = _like_progress(rules)
    if not do_farm:
        lines.append("[点赞] status 模式跳过刷分（今日 %s/40）" % got)
    elif remain <= 0:
        lines.append("[点赞] 今日 %s/40 已拿满，跳过" % got)
    else:
        rounds = min(remain, LIKE_MAX_ROUNDS)
        if rounds < remain:
            lines.append("[点赞] 保守模式: 本次只刷 %s 轮（剩余 %s 轮留给后续，避免高频吊销会话）"
                         % (rounds, remain - rounds))
        lines.append("[点赞] 开始刷 %s 轮（star→unstar 循环，每轮 +2，间隔 %ss）" % (rounds, LIKE_DELAY))
        done, fail = 0, 0
        for i in range(rounds):
            ok2, msg = like_once(cookie)
            if ok2:
                done += 1
                if done % 5 == 0 or done == rounds:
                    lines.append("[点赞] 进度 %s/%s" % (done, rounds))
            else:
                fail += 1
                lines.append("[点赞] 第 %s 轮失败: %s" % (i + 1, msg))
                # 401 未登录态是账号级问题, 重试无意义, 立刻停
                if "需更高登录态" in msg:
                    lines.append("[点赞] 登录态不足, 跳过本账号点赞(不影响登录魔粒)")
                    break
                if fail >= 3:
                    lines.append("[点赞] 连续失败 3 次，停止（防风控）")
                    break
            time.sleep(LIKE_DELAY)
        rules, _, _ = fetch_rules(cookie)
        got2, remain2 = _like_progress(rules) if rules else (got, remain)
        lines.append("[点赞] 完成 %s 轮，今日进度 %s/40，剩余 %s 次" % (done, got2, remain2))

    # 汇总
    bal1, _ = fetch_balance(cookie)
    rules, _, _ = fetch_rules(cookie)
    if rules:
        lines.append("[结束] 登录 %s/250 · 点赞 %s/40 · 余额 %s（参考，短期魔粒 24h 过期）"
                     % (_daily_credit(rules), _like_progress(rules)[0], bal1))
    else:
        lines.append("[结束] 余额 %s" % bal1)
    return True, lines


def run_all(do_farm, quiet=False):
    cfg = load_cfg()
    if not cfg["accounts"]:
        print("[x] 未配置 cookie，请先 init：python ms_magicube.py init \"<Cookie整行>\"")
        return 2
    n, fails = len(cfg["accounts"]), 0
    print("──────────────── 魔搭魔粒 %s ────────────────" % ("auto" if do_farm else "status"))
    for i, acc in enumerate(cfg["accounts"]):
        print("[%d/%d %s]" % (i + 1, n, acc.get("name")))
        ok, lines = run_one(acc, do_farm)
        for l in lines:
            print("  " + l)
        if not ok:
            fails += 1
    print("[汇总] 成功 %d/%d" % (n - fails, n))
    return 3 if fails else 0


def _tg(text):
    """推 Telegram; 凭据取青龙 Envs 表(裸跑/定时都可用), 失败不影响任务结果。"""
    import sqlite3
    import subprocess
    try:
        c = sqlite3.connect("/ql/data/db/database.sqlite")
        q = dict(c.execute("SELECT name,value FROM Envs WHERE name IN %s" % str(TG_NAMES)))
    except Exception as e:
        print("[tg] 读凭据失败: %s" % e)
        return
    tok = q.get("TG_BOT_TOKEN") or ""
    chat = q.get("TG_USER_ID") or q.get("TG_CHAT_ID") or ""
    if not tok or not chat:
        print("[tg] 未配置 TG_BOT_TOKEN/TG_USER_ID, 跳过推送")
        return
    body = "【魔搭魔粒】\n" + (text or "(无输出)").strip()[:3500]
    cmd = ["curl", "-s", "--max-time", "20"]
    if q.get("TG_PROXY_HOST"):
        cmd += ["--proxy", "http://%s:%s" % (q["TG_PROXY_HOST"], q.get("TG_PROXY_PORT") or "7890")]
    cmd += ["--data-urlencode", "chat_id=" + chat, "--data-urlencode", "text=" + body,
            "https://api.telegram.org/bot%s/sendMessage" % tok]
    r = subprocess.run(cmd, capture_output=True, text=True)
    print("[tg] %s" % ("推送成功" if '"ok":true' in r.stdout else "推送失败 " + r.stdout[:200]))


def main():
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        return 1
    cmd, rest = args[0], args[1:]
    if cmd == "init":
        cmd_init(rest)
        return 0
    if cmd == "status":
        return run_all(do_farm=False, quiet=True)
    if cmd == "auto":
        return run_all(do_farm=True)
    if cmd == "rules":
        cmd_rules(rest)
        return 0
    print(__doc__)
    return 1


if __name__ == "__main__":
    import contextlib
    import io
    _buf = io.StringIO()
    with contextlib.redirect_stdout(_buf):
        try:
            _rc = main()
        except SystemExit as e:
            _rc = e.code if isinstance(e.code, int) else 1
    _out = _buf.getvalue()
    sys.stdout.write(_out)
    if os.environ.get("MS_NO_TG") != "1":
        _tg(_out)
    sys.exit(_rc)
