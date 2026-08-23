#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# cron: 15 9 * * *
# new Env('网易云音乐签到')
"""
网易云音乐每日签到脚本
适配青龙面板,凭据只读环境变量(需安装 pycryptodome,见 README)。

依赖:
  pip3 install pycryptodome        # 仅用于 weapi AES 加密

功能:
  - 每日签到获取云贝(weapi 接口,AES-128-CBC + RSA 加密)
  - 自动识别: 签到成功 / 今日已签到 / Cookie 失效
  - 支持多账号(每行一条 Cookie)
  - 运行结束发送通知总结(青龙内置通知 / PushPlus / Server酱)

环境变量(在青龙面板配置):
  NETEASE_COOKIES        多账号,每行一条 Cookie(# 开头为注释,优先)
  NETEASE_COOKIE         单账号 Cookie
  PUSHPLUS_TOKEN         PushPlus 推送 token(可选)
  SENDKEY                Server酱 SendKey(可选)

Cookie 获取(只需 2 个字段,或整段全复制):
  1. 浏览器登录 https://music.163.com
  2. F12 → Application → Cookies → https://music.163.com
  3. 必填: MUSIC_U(登录态) / __csrf(与请求体 csrf_token 一致)
     推荐: __remember_me=true(保持会话)
"""
import base64
import json
import os
import random
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

try:
    from Crypto.Cipher import AES  # noqa: PLC0415
except ImportError:
    print("缺少依赖 pycryptodome,请执行: pip3 install pycryptodome")
    sys.exit(1)

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

# weapi 签到接口(HTTP POST, body 需加密)
WEAPI_SIGN_URL = "https://music.163.com/weapi/point/dailyTask"


# ---------------------------------------------------------------------------
# weapi 加密(pycryptodome;RSA 为原始 RSA,标准库 pow 实现)
# ---------------------------------------------------------------------------
MODULUS = (
    "00e0b509f6259df8642dbc35662901477df22677ec152b5ff68ace615bb7"
    "b725152b3ab17a876aea8a5aa76d2e417629ec4ee341f56135fccf695280"
    "104e0312ecbda92557c93870114af6c9d05c4f7f0c3685b7a46bee255932"
    "575cce10b424d813cfe4875d3e82047b97ddef52741d546b8e289dc6935b"
    "3ece0462db0a22b8e7"
)
PUBKEY = "010001"
_NONCE = b"0CoJUm6Qyw8W8jud"
_IV = b"0102030405060708"


def _aes_encrypt(plaintext, key, iv):
    """AES-128-CBC 加密,带 PKCS7 填充,返回 bytes"""
    padlen = 16 - (len(plaintext) % 16)
    plaintext = plaintext + bytes([padlen]) * padlen
    cipher = AES.new(key, AES.MODE_CBC, iv)
    return cipher.encrypt(plaintext)


def _rsa_encrypt(text):
    """原始 RSA 公钥加密(weapi 专用: 反转字节 → int → pow → 256 hex)"""
    c = pow(int.from_bytes(text[::-1], "big"), int(PUBKEY, 16), int(MODULUS, 16))
    return format(c, "x").zfill(256)


def weapi_encrypt(plain_dict):
    """weapi 加密: 双层 AES-128-CBC + RSA,返回 {'params','encSecKey'}
    第1层: 加密原始 JSON → 输出 base64 字符串
    第2层: 加密第1层的 base64 字符串(utf-8 字节,同样 PKCS7 填充)→ 输出 base64
    """
    data = json.dumps(plain_dict, separators=(",", ":")).encode("utf-8")
    secret = base64.b64encode(os.urandom(16)).decode()[:16].encode()  # 16字节随机key
    inner_b64 = base64.b64encode(_aes_encrypt(data, _NONCE, _IV)).decode()
    params = base64.b64encode(
        _aes_encrypt(inner_b64.encode("utf-8"), secret, _IV)
    ).decode()
    return {
        "params": params,
        "encSecKey": _rsa_encrypt(secret),
    }


# ---------------------------------------------------------------------------
# HTTP 工具
# ---------------------------------------------------------------------------
def _http(url, method="GET", data=None, headers=None, timeout=15):
    """返回 (status, text)"""
    h = {
        "Accept": "*/*",
        "Accept-Language": "zh-CN,zh;q=0.9",
        "User-Agent": UA,
        "Referer": "https://music.163.com/",
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


# ---------------------------------------------------------------------------
# 签到
# ---------------------------------------------------------------------------
def parse_cookie(cookie_str):
    """Cookie 字符串 -> dict,用于提取 __csrf"""
    d = {}
    for part in cookie_str.split(";"):
        part = part.strip()
        if "=" in part:
            k, v = part.split("=", 1)
            d[k.strip()] = v.strip()
    return d


def sign_in(cookie_str):
    """weapi 签到,返回 (ok, msg)"""
    ck = parse_cookie(cookie_str)
    if "MUSIC_U" not in ck:
        return False, "Cookie 缺少 MUSIC_U,请重新登录获取"
    csrf = ck.get("__csrf", "")

    # 明文参数 → weapi 加密 → POST form
    plain = {"type": 1, "csrf_token": csrf}
    encrypted = weapi_encrypt(plain)
    body = urllib.parse.urlencode(encrypted).encode("utf-8")

    hdrs = {
        "Content-Type": "application/x-www-form-urlencoded",
        "Origin": "https://music.163.com",
        "Cookie": cookie_str,
    }
    st, txt = _http(WEAPI_SIGN_URL, method="POST", data=body, headers=hdrs)
    if st != 200:
        return False, "HTTP %s: %s" % (st, txt[:200])
    try:
        d = json.loads(txt)
    except ValueError:
        return False, "返回非JSON: %s" % txt[:200]
    code = d.get("code")
    msg = d.get("message") or d.get("msg") or ""
    if code == 200:
        point = d.get("point")
        return True, "签到成功%s" % ((",+%s云贝" % point) if point else "")
    if code == -2 or "重复" in msg:
        return True, "今日已签到"
    if code == 301:
        return False, "Cookie失效(301),请重新登录获取"
    return False, "code=%s %s" % (code, msg)


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
    """优先 NETEASE_COOKIES(多账号, 每行一条);否则 NETEASE_COOKIE(单账号)"""
    accounts = []
    multi = os.environ.get("NETEASE_COOKIES", "").strip()
    if multi:
        for line in multi.splitlines():
            line = line.strip().strip("'").strip('"')
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                accounts.append(line)
    else:
        ck = os.environ.get("NETEASE_COOKIE", "").strip().strip("'").strip('"')
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
        print("  多账号: NETEASE_COOKIES (每行一条 Cookie)")
        print("  单账号: NETEASE_COOKIE")
        sys.exit(1)

    print("=" * 50)
    print("网易云音乐签到开始,共 %d 个账号" % len(accounts))
    print("=" * 50)

    all_lines = []
    ok_count = 0
    for i, ck in enumerate(accounts, 1):
        label = "账号%d(%s)" % (i, mask_cookie(ck))
        print("\n>>> %s" % label)
        ok, msg = sign_in(ck)
        icon = "✅" if ok else "❌"
        print("  %s %s" % (icon, msg))
        all_lines.append("【账号%d】%s %s" % (i, icon, msg))
        if ok:
            ok_count += 1
        time.sleep(random.uniform(1, 3))

    summary = "\n".join(all_lines)
    print("\n" + "=" * 50)
    print(summary)
    print("=" * 50)

    title = "网易云音乐签到: %d/%d 账号成功" % (ok_count, len(accounts))
    channel = send_notify(title, summary)
    print("\n通知渠道: %s" % channel)
    return 0 if ok_count > 0 else 1


if __name__ == "__main__":
    sys.exit(main())
