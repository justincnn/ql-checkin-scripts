#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
xhx_office_points.py — 商汤「办公小浣熊」(xiaohuanxiong.com) 每日积分 · 多账号
================================================================================
积分机制(官方 changelog + 规则页 2026-10 确认):
  - 个人免费版: 每日赠送 300 积分, **会话活跃自动发放, 无手动签到接口**
    (实测 GET /api/web/points/v1/balance 即自动入账, 无需任何领取动作)
  - 个人升级版: 每日 500 + 每月 10000;  积分加油包可购买
鉴权: POST /api/web/auth/v1/refresh  body {"refresh_token": <rt>}
      -> data.access_token / data.refresh_token(**每次续期服务端都会轮换 rt**)
      accessToken 短寿命(约3h), 脚本自动续期。

配置 xhx_config.json(脚本自动维护, 服务端轮换后的新 rt 只在这里):
  {"accounts": [{"name": "RaccoonNoah", "token": <access JWT>,
                 "refresh_token": <rt>, "token_exp": <exp unix>}], "cfg_updated": iso}
  ★ 以本文件为权威值; 青龙 env XHX_RT 只在**首次(无本文件时)**做全量种子。

用法:
  python xhx_office_points.py initrt <refresh_token>   # 追加/更新一个账号(推荐)
  python xhx_office_points.py auto                     # 每日任务: 全账号续期+查余额
  python xhx_office_points.py status                   # 同上, 只读播报
  python xhx_office_points.py init <access_token> [refresh_token]   # 兼容旧用法
换账号: 用 initrt 追加, 或删掉 xhx_config.json 改 env XHX_RT(换行分隔多账号)重跑。
"""
import base64
import json
import os
import ssl
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime

BASE = os.path.dirname(os.path.abspath(__file__))
CFG_PATH = os.path.join(BASE, "xhx_config.json")
HOST = "https://xiaohuanxiong.com"
API = HOST + "/api/web"

CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE

sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def now_iso():
    return datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


def jwt_payload(token):
    try:
        p = token.split(".")[1]
        p += "=" * (-len(p) % 4)
        return json.loads(base64.urlsafe_b64decode(p))
    except Exception:
        return {}


def jwt_exp(token):
    return jwt_payload(token).get("exp", 0) or 0


def jwt_name(token):
    return jwt_payload(token).get("name", "") or ""


def save_cfg(cfg):
    cfg["cfg_updated"] = now_iso()
    with open(CFG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


def load_cfg():
    """读配置。文件为准; 文件不存在时才用 env XHX_RT(换行分隔)做全量种子。"""
    try:
        with open(CFG_PATH, "r", encoding="utf-8") as f:
            cfg = json.load(f)
    except Exception:
        cfg = None

    if isinstance(cfg, dict):
        # 兼容旧单账号格式
        if "accounts" not in cfg and cfg.get("refresh_token"):
            tok = cfg.get("token") or ""
            cfg = {"accounts": [{"token": tok,
                                 "refresh_token": cfg["refresh_token"],
                                 "token_exp": cfg.get("token_exp") or jwt_exp(tok),
                                 "name": jwt_name(tok) or jwt_name(cfg["refresh_token"])}]}
        cfg.setdefault("accounts", [])
        return cfg

    if cfg is not None:  # 文件存在但内容不是 dict
        return {"accounts": []}

    env_rt = (os.environ.get("XHX_RT") or "").strip()
    accs = []
    for rt in [x.strip() for x in env_rt.splitlines() if x.strip()]:
        accs.append({"token": "", "refresh_token": rt, "token_exp": 0,
                     "name": jwt_name(rt)})
    if accs:
        print("[cfg] 首次初始化: 从 env XHX_RT 载入 %d 个账号" % len(accs))
    return {"accounts": accs}


def http_json(method, url, body=None, token=None):
    headers = {
        "accept": "*/*",
        "content-type": "application/json",
        "user-agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"),
        "referer": "https://office.xiaohuanxiong.com/",
        "x-client-platform": "web",
        "x-raccoon-language": "zh",
    }
    if token:
        headers["authorization"] = "Bearer " + token
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        r = urllib.request.urlopen(req, timeout=25, context=CTX)
        return r.status, json.loads(r.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw.decode("utf-8", "replace"))
        except Exception:
            return e.code, {"raw": raw.decode("utf-8", "replace")[:200]}
    except Exception as e:
        return -1, {"message": "网络异常 %s" % e}


def refresh_token(acc):
    """用 refresh_token 换新 accessToken(服务端轮换 rt, 两个都要写回)。"""
    rt = acc.get("refresh_token") or ""
    st, j = http_json("POST", API + "/auth/v1/refresh",
                      body={"refresh_token": rt}, token=acc.get("token"))
    if st == 200 and isinstance(j, dict) and (j.get("data") or {}).get("access_token"):
        d = j["data"]
        acc["token"] = d["access_token"]
        if d.get("refresh_token"):
            acc["refresh_token"] = d["refresh_token"]
        acc["token_exp"] = jwt_exp(d["access_token"])
        if not acc.get("name"):
            acc["name"] = jwt_name(acc["refresh_token"])
        return True, "续期成功"
    msg = ""
    if isinstance(j, dict):
        msg = j.get("message") or json.dumps(j, ensure_ascii=False)[:120]
    return False, "HTTP %s %s" % (st, msg)


def ensure_token(acc):
    exp = int(acc.get("token_exp") or jwt_exp(acc.get("token") or ""))
    if exp and exp - time.time() < 1800:
        return refresh_token(acc)
    return True, ""


def get_balance(acc):
    st, j = http_json("GET", API + "/points/v1/balance", token=acc.get("token"))
    if st == 200 and isinstance(j, dict) and j.get("code") == 0:
        return j.get("data") or {}, None
    if st == 401 or (isinstance(j, dict) and "token" in str(j.get("message", "")).lower()):
        ok, msg = refresh_token(acc)
        if ok:
            st, j = http_json("GET", API + "/points/v1/balance", token=acc.get("token"))
            if st == 200 and isinstance(j, dict) and j.get("code") == 0:
                return j.get("data") or {}, None
        return None, "token 续期失败: %s —【小浣熊失效】需重抓 refresh_token" % (msg or "")
    return None, "HTTP %s %s" % (st, str(j)[:150])


def label(acc, idx):
    return acc.get("name") or ("账号%d" % (idx + 1))


def upsert_account(cfg, rt):
    """按 rt 里的 name 找账号更新, 找不到就追加。"""
    name = jwt_name(rt)
    for a in cfg["accounts"]:
        if name and a.get("name") == name:
            a["refresh_token"] = rt
            a["token"] = ""
            a["token_exp"] = 0
            return a, "更新"
    acc = {"token": "", "refresh_token": rt, "token_exp": 0, "name": name}
    cfg["accounts"].append(acc)
    return acc, "新增"


def run_all(cfg, quiet=False):
    """遍历账号: 续期 + 查余额。返回 (结果行列表, 失败数)。"""
    lines, fails = [], 0
    n = len(cfg["accounts"])
    for i, acc in enumerate(cfg["accounts"]):
        tag = "[%d/%d %s]" % (i + 1, n, label(acc, i))
        ok, msg = ensure_token(acc)
        if not ok:
            lines.append("%s 续期失败: %s —【小浣熊失效】需重抓 refresh_token" % (tag, msg))
            fails += 1
            continue
        data, err = get_balance(acc)
        if err:
            lines.append("%s 失败: %s" % (tag, err))
            fails += 1
            continue
        exp = int(acc.get("token_exp") or 0)
        tail = "" if quiet or not exp else " · token 剩余 %.1fh" % ((exp - time.time()) / 3600)
        lines.append("%s 可用 %s · 每日池 %s%s" % (
            tag, data.get("available_points"), data.get("daily_points"), tail))
    return lines, fails


def cmd_initrt():
    args = sys.argv[2:]
    if not args or not args[0].strip():
        print("用法: python xhx_office_points.py initrt <refresh_token>")
        return 2
    rt = args[0].strip()
    cfg = load_cfg()
    acc, how = upsert_account(cfg, rt)
    ok, msg = refresh_token(acc)
    print(("[OK] %s 账号 %s: %s" % (how, label(acc, cfg["accounts"].index(acc)), msg))
          if ok else ("[失败] %s: %s" % (how, msg)))
    if not ok:
        return 3
    save_cfg(cfg)
    data, err = get_balance(acc)
    print("[OK] 可用积分 %s · 当前共 %d 个账号 · 已写入 %s" % (
        data.get("available_points") if not err else err, len(cfg["accounts"]), CFG_PATH))
    return 0


def cmd_init():
    args = sys.argv[2:]
    if not args or not args[0].strip():
        print("用法: python xhx_office_points.py init <access_token> [refresh_token]")
        return 2
    cfg = load_cfg()
    tok = args[0].strip()
    rt = args[1].strip() if len(args) > 1 and args[1].strip() else tok
    acc, how = upsert_account(cfg, rt)
    acc["token"] = tok
    acc["token_exp"] = jwt_exp(tok)
    data, err = get_balance(acc)
    print("[%s] %s accessToken %s" % (how, label(acc, cfg["accounts"].index(acc)),
                                      "有效, 可用积分 %s" % data.get("available_points")
                                      if not err else "验证失败: %s" % err))
    save_cfg(cfg)
    print("[OK] 当前共 %d 个账号 · 已写入 %s" % (len(cfg["accounts"]), CFG_PATH))
    return 0


def cmd_status(quiet=False):
    cfg = load_cfg()
    if not cfg["accounts"]:
        print("[失败] 未配置。执行 initrt <refresh_token>")
        return 2
    lines, fails = run_all(cfg, quiet=quiet)
    save_cfg(cfg)
    for l in lines:
        print(l)
    print("[汇总] 成功 %d/%d" % (len(cfg["accounts"]) - fails, len(cfg["accounts"])))
    return 3 if fails else 0


def _tg(text):
    """推 Telegram; 凭据取青龙 Envs 表(裸跑/定时都可用), 失败不影响任务结果。"""
    import sqlite3
    import subprocess
    try:
        c = sqlite3.connect("/ql/data/db/database.sqlite")
        q = dict(c.execute("SELECT name,value FROM Envs WHERE name IN "
                           "('TG_BOT_TOKEN','TG_USER_ID','TG_CHAT_ID','TG_PROXY_HOST','TG_PROXY_PORT')"))
    except Exception as e:
        print("[tg] 读凭据失败: %s" % e)
        return
    tok = q.get("TG_BOT_TOKEN") or ""
    chat = q.get("TG_USER_ID") or q.get("TG_CHAT_ID") or ""
    if not tok or not chat:
        print("[tg] 未配置 TG_BOT_TOKEN/TG_USER_ID, 跳过推送")
        return
    body = "【办公小浣熊积分】\n" + (text or "(无输出)").strip()[:3500]
    cmd = ["curl", "-s", "--max-time", "20"]
    if q.get("TG_PROXY_HOST"):
        cmd += ["--proxy", "http://%s:%s" % (q["TG_PROXY_HOST"], q.get("TG_PROXY_PORT") or "7890")]
    cmd += ["--data-urlencode", "chat_id=" + chat, "--data-urlencode", "text=" + body,
            "https://api.telegram.org/bot%s/sendMessage" % tok]
    r = subprocess.run(cmd, capture_output=True, text=True)
    print("[tg] %s" % ("推送成功" if '"ok":true' in r.stdout else "推送失败 " + r.stdout[:200]))


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "initrt":
        return cmd_initrt()
    if cmd == "init":
        return cmd_init()
    if cmd == "status":
        return cmd_status(quiet=True)
    if cmd == "auto":
        return cmd_status(quiet=False)
    print(__doc__)
    return 1


if __name__ == "__main__":
    import contextlib
    import io
    _buf = io.StringIO()
    with contextlib.redirect_stdout(_buf):
        _rc = main()
    _out = _buf.getvalue()
    sys.stdout.write(_out)
    if os.environ.get("XHX_NO_TG") != "1":
        _tg(_out)
    sys.exit(_rc)
