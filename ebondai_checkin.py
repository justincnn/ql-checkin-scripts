#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ebondai.com（EBond AI）每日保活脚本 —— 纯 HTTP 直连，不依赖浏览器。

这个站点是 Sub2API 系的 AI API 网关（控制台 https://ebondai.com ，后端
https://api.ebondai.com/api/v1 ）。它的「福利中心 → 每日签到」有一条硬门槛：

    准入门槛：签到需当天在本站有产生费用的 API 调用，兑换余额无门槛。
    时区固定为北京时间（UTC+8）。

也就是说，光开着一个 API Key 不算数，必须当天真的发生过一次「产生费用」的调用，
当天才能签到；签到本身还要再点一次。本脚本把这两步一次跑完：

  1) POST /api/v1/auth/login          邮箱 + 密码换 access_token（会话落盘复用）
  2) GET  /api/v1/usage               查今天本 Key 有没有已经花过钱的调用（幂等保护）
  3) POST /v1/chat/completions        用最便宜的文本模型发一句「hi」产生费用
  4) GET  /api/v1/welfare/overview    读签到状态与福利余额
  5) POST /api/v1/welfare/check-in    当天还没签到就签到，领当天福利余额
  6) POST /api/v1/welfare/exchange    把福利余额 1:1 兑换进账户余额（默认开启）

当天已经调用过就不再发包（只读状态），所以一天跑几次都安全。兑换也是幂等的：
福利余额已经是 0 就直接跳过，不会去调接口。

只做保活这一件事：一次最小调用 + 一次签到 + 一次兑换。不充值、不买额度、
不新建/删除/修改 API Key、不改密码、不动账户资料、不碰别的模型。

用法：
    python3 ebondai签到.py                 # 一条命令跑完
    python3 ebondai签到.py --json          # 机器可读输出
    python3 ebondai签到.py --force-call    # 忽略幂等保护，强制再发一次（会再花钱）
    python3 ebondai签到.py --no-checkin    # 只保活调用，不提交签到
    python3 ebondai签到.py --dry-run       # 只读状态，不发包、不签到
    python3 ebondai签到.py --cred <文件> --keyfile <文件> --session <文件>

退出码：
    0 成功（含「今天已经调用过 / 已经签到过」）
    2 登录失败（账号或密码不对）
    3 网络异常或接口报错
    4 凭据 / API Key 配置文件缺失或解析不到
    5 余额不足或 API Key 不可用（分组/权限问题）

凭据与 API Key 都从私有配置文件读，脚本里不写死、也不回显。
"""

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone

try:                       # 优先用 requests（和另外两个签到脚本保持一致）
    import requests
except ImportError:        # 沙箱里没装也能跑：退到标准库实现，避免定时任务因缺包夭折
    requests = None


if requests is None:
    import urllib.error
    import urllib.parse
    import urllib.request

    class RequestException(Exception):
        pass

    class _Resp(object):
        def __init__(self, status, body, headers=None):
            self.status_code = status
            self.text = body
            self.headers = headers or {}

        def json(self):
            return json.loads(self.text)

    def _request(method, url, **kw):
        # 用 **kw 接参数，避免形参名叫 json 把 json 模块遮住
        json_body = kw.pop("json", None)
        params = kw.pop("params", None)
        headers = kw.pop("headers", None)
        timeout = kw.pop("timeout", 30)
        if params:
            url += ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
        data = None
        if json_body is not None:
            data = json.dumps(json_body).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers=dict(headers or {}),
                                     method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return _Resp(resp.getcode(),
                             resp.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as exc:
            return _Resp(exc.code, exc.read().decode("utf-8", "replace"))
        except Exception as exc:      # URLError / socket 超时等
            raise RequestException(str(exc))

    class _Session(object):
        def __init__(self):
            self.headers = {}

        def _call(self, method, url, **kw):
            hdrs = dict(self.headers)
            hdrs.update(kw.pop("headers", None) or {})
            return _request(method, url, headers=hdrs, **kw)

        def get(self, url, **kw):
            return self._call("GET", url, **kw)

        def post(self, url, **kw):
            return self._call("POST", url, **kw)

    class _RequestsShim(object):
        RequestException = RequestException
        Session = _Session

        @staticmethod
        def post(url, **kw):
            return _request("POST", url, **kw)

    requests = _RequestsShim()

BASE_URL = "https://ebondai.com"
API = "https://api.ebondai.com/api/v1"          # 控制台接口
CHAT_URL = "https://api.ebondai.com/v1/chat/completions"   # OpenAI 兼容推理端点
FALLBACK_CHAT_URL = "https://cf.ebondai.com/v1/chat/completions"  # 海外备用端点

TIMEZONE = "Asia/Shanghai"        # 站点按北京时间（UTC+8）判定「今天」
DEFAULT_MODEL = "gpt-6-luna"      # 全场最便宜的文本模型（见 ebondai签到说明.md）

CRED_FILENAME = "ebondai-credentials.md"
KEY_FILENAME = "ebondai-api-key.md"
SESSION_FILENAME = "ebondai-session.json"
CONFIG_DIR = os.path.join(os.path.expanduser("~"), ".config", "today-checkin")
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

CST = timezone(timedelta(hours=8))


# --------------------------------------------------------------------------
# 配置文件解析（密码 / Key 永不打印、永不写日志）
# --------------------------------------------------------------------------
def _field(text, labels, require_prefix=None):
    """从 markdown 配置里取一个字段，兼容 '- 密码：xxx' / '**密码**: xxx' 等写法。"""
    for raw in text.splitlines():
        line = raw.strip()
        line = re.sub(r"^[-*+\s]+", "", line)          # 去掉列表符号
        line = re.sub(r"^\*+|\*+$", "", line).strip()  # 去掉加粗星号
        for label in labels:
            if not line.startswith(label):
                continue
            rest = line[len(label):]
            # 标签后面必须紧跟分隔符/括号，避免前缀误命中
            if rest and rest[0] not in "：:（(【[] \t":
                continue
            m = re.search(r"[:：]\s*(.+)$", rest)
            if not m:
                continue
            value = m.group(1).strip()
            # 取到第一个括号/空白为止，避免把后面的说明文字当成取值
            value = re.split(r"[（(\s]", value)[0].rstrip("）) ")
            if not value:
                continue
            if require_prefix and not value.startswith(require_prefix):
                continue
            return value
    return None


def _find_file(explicit, filename, extra_dirs=()):
    if explicit:
        return explicit if os.path.isfile(explicit) else None
    candidates = []
    env_dir = os.environ.get("CHECKIN_CRED_DIR")
    if env_dir:
        candidates.append(os.path.join(env_dir, filename))
    candidates += [os.path.join(d, filename) for d in (
        CONFIG_DIR, SCRIPT_DIR, os.path.join(SCRIPT_DIR, "accounts"),
        os.getcwd(), os.path.join(os.getcwd(), "accounts")) + tuple(extra_dirs)]
    for path in candidates:
        if os.path.isfile(path):
            return path
    return None


def load_credentials(path):
    with open(path, "r", encoding="utf-8") as fh:
        text = fh.read()
    email = os.environ.get("EBONDAI_EMAIL") or _field(
        text, ["登录邮箱", "邮箱", "Email", "email", "账号"])
    password = os.environ.get("EBONDAI_PASSWORD") or _field(
        text, ["登录密码", "密码", "Password", "password"])
    if not email or not password:
        return None, None
    return email, password


def load_apikey(path):
    """读 API Key 配置文件。返回 (key, key_id, model)；缺字段就返回 None。"""
    with open(path, "r", encoding="utf-8") as fh:
        text = fh.read()
    key = os.environ.get("EBONDAI_API_KEY")
    if not key:
        # 只认真以 sk- 开头的取值，避免把「Key 名称：…」这类同行字段当成 Key
        key = _field(text, ["API Key", "api_key", "Key", "密钥"], require_prefix="sk-")
    key_id = _field(text, ["Key ID", "ID"])
    model = _field(text, ["默认模型", "模型", "model", "Model"])
    if not key or not key.startswith("sk-"):
        return None, None, None
    try:
        key_id = int(key_id) if key_id else None
    except ValueError:
        key_id = None
    return key, key_id, model


# --------------------------------------------------------------------------
# 会话落盘
# --------------------------------------------------------------------------
def default_session_path():
    return os.path.join(CONFIG_DIR, SESSION_FILENAME)


def ensure_dir(path):
    os.makedirs(path, mode=0o700, exist_ok=True)
    try:
        os.chmod(path, 0o700)
    except OSError:
        pass


def write_private(path, data):
    ensure_dir(os.path.dirname(path))
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(data)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def load_session(path):
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as fh:
            payload = json.load(fh)
        data = payload.get("data") if isinstance(payload, dict) else None
        if isinstance(data, dict) and data.get("access_token"):
            return data
    except (OSError, ValueError):
        pass
    return None


def save_session(path, payload):
    if isinstance(payload, dict) and isinstance(payload.get("data"), dict):
        payload = payload["data"]
    write_private(path, json.dumps({"data": payload}, ensure_ascii=False, indent=2))


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------
def make_session(token=None, extra=None):
    s = requests.Session()
    headers = {
        "User-Agent": UA,
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Content-Type": "application/json",
        "Origin": BASE_URL,
        "Referer": BASE_URL + "/dashboard",
    }
    if token:
        headers["Authorization"] = "Bearer " + token
    if extra:
        headers.update(extra)
    s.headers.update(headers)
    return s


def unwrap(resp):
    """Sub2API 统一返回 {code, message, data}，code==0 才算成功。"""
    try:
        body = resp.json()
    except ValueError:
        raise RuntimeError("接口返回的不是 JSON（HTTP %s）：%s"
                           % (resp.status_code, resp.text[:200]))
    if isinstance(body, dict) and "code" in body:
        if body.get("code") != 0:
            raise RuntimeError("接口报错 code=%s message=%s"
                               % (body.get("code"), body.get("message")))
        return body.get("data")
    return body


def api_login(http, email, password):
    resp = http.post(API + "/auth/login",
                     json={"email": email, "password": password}, timeout=30)
    try:
        body = resp.json()
    except ValueError:
        raise RuntimeError("登录接口返回非 JSON（HTTP %s）：%s"
                           % (resp.status_code, resp.text[:200]))
    if resp.status_code != 200 or body.get("code") != 0:
        raise RuntimeError("登录失败（HTTP %s）：%s"
                           % (resp.status_code, body.get("message") or body))
    data = body.get("data") or {}
    if not data.get("access_token"):
        raise RuntimeError("登录接口未返回 access_token")
    return data


def api_refresh(http, refresh_token):
    resp = http.post(API + "/auth/refresh",
                     json={"refresh_token": refresh_token}, timeout=30)
    data = unwrap(resp)
    if not isinstance(data, dict) or not data.get("access_token"):
        raise RuntimeError("刷新接口未返回 access_token")
    return data


def api_me(http, token):
    resp = http.get(API + "/auth/me",
                    headers={"Authorization": "Bearer " + token},
                    params={"timezone": TIMEZONE}, timeout=30)
    if resp.status_code == 401:
        return None
    return unwrap(resp)


def api_usage_today(http, token, key_id, day):
    """查某个 API Key 在指定日期（北京时间）的花费记录。"""
    params = {"start_date": day, "end_date": day, "page": 1, "page_size": 100,
              "timezone": TIMEZONE}
    if key_id:
        params["api_key_id"] = key_id
    resp = http.get(API + "/usage", headers={"Authorization": "Bearer " + token},
                    params=params, timeout=30)
    if resp.status_code == 401:
        return None
    data = unwrap(resp)
    if isinstance(data, dict):
        return data.get("items") or []
    return data or []


def api_welfare(http, token):
    resp = http.get(API + "/welfare/overview",
                    headers={"Authorization": "Bearer " + token},
                    params={"timezone": TIMEZONE}, timeout=30)
    if resp.status_code == 401:
        return None
    return unwrap(resp)


def api_checkin(http, token):
    """提交签到。返回 (data|None, already: bool)。"""
    resp = http.post(API + "/welfare/check-in", json={},
                     headers={"Authorization": "Bearer " + token},
                     params={"timezone": TIMEZONE}, timeout=30)
    if resp.status_code == 401:
        return None, False
    if resp.status_code == 409:
        return None, True
    return unwrap(resp), False


def api_exchange(http, token, amount):
    """把福利余额兑换进账户余额。返回 (data|None, 错误说明|None)。"""
    resp = http.post(API + "/welfare/exchange", json={"amount": amount},
                     headers={"Authorization": "Bearer " + token},
                     params={"timezone": TIMEZONE}, timeout=30)
    if resp.status_code == 401:
        return None, "HTTP 401（会话失效）"
    if resp.status_code == 400:
        body = ""
        try:
            body = (resp.json() or {}).get("message") or resp.text[:200]
        except Exception:
            body = resp.text[:200]
        return None, "HTTP 400：%s" % body
    data = unwrap(resp)
    if data is None:
        return None, "HTTP %s：%s" % (resp.status_code, resp.text[:200])
    return data, None


def chat_once(http, api_key, model, prompt="hi", max_tokens=8):
    """用最便宜模型发一句 hi，返回 (记录dict, 原始响应dict)。"""
    body = {"model": model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_tokens}
    last_err = None
    for url in (CHAT_URL, FALLBACK_CHAT_URL):
        try:
            resp = requests.post(url, json=body, timeout=120,
                                 headers={"Authorization": "Bearer " + api_key,
                                          "Content-Type": "application/json",
                                          "User-Agent": UA,
                                          "Accept": "application/json"})
        except requests.RequestException as exc:
            last_err = exc
            continue
        if resp.status_code == 200:
            return resp.json(), url
        # 4xx 是业务错误，不要拿备用端点重试同一件事
        detail = resp.text[:300]
        if resp.status_code in (401, 403):
            raise PermissionError("API Key 被拒绝（HTTP %s）：%s"
                                  % (resp.status_code, detail))
        if resp.status_code == 402:
            raise BalanceError("余额不足（HTTP 402）：%s" % detail)
        if resp.status_code in (400, 404):
            raise ModelError("模型不可用或请求被拒（HTTP %s）：%s"
                             % (resp.status_code, detail))
        raise RuntimeError("推理接口报错（HTTP %s）：%s" % (resp.status_code, detail))
    raise RuntimeError("推理接口连不上：%s" % last_err)


class BalanceError(Exception):
    pass


class ModelError(Exception):
    pass


# --------------------------------------------------------------------------
# 进度事件（--progress-file）：面板"签到过程"时间线的数据源。
# 每行一个 JSON：{"ts": ..., "site": ..., "message": ...}，实时 flush。
# 不传 --progress-file 时完全无影响。
# --------------------------------------------------------------------------
def _open_progress_file(path):
    if not path:
        return None
    try:
        return open(path, "a", encoding="utf-8")
    except OSError:
        return None


def _progress_emit(fh, site, message):
    if not fh:
        return
    try:
        fh.write(json.dumps({
            "ts": datetime.now(CST).isoformat(timespec="seconds"),
            "site": site,
            "message": message,
        }, ensure_ascii=False) + "\n")
        fh.flush()
    except (OSError, ValueError):
        pass


# --------------------------------------------------------------------------
# 主流程
# --------------------------------------------------------------------------
def resolve_token(http, session_path, email, password, force_login, log, step=None):
    saved = None if force_login else load_session(session_path)
    step = step or (lambda m: None)
    if saved:
        token = saved.get("access_token")
        if api_me(http, token) is not None:
            log("复用本地会话（未重新登录）")
            step("登录状态有效，复用本地会话")
            return token, False
        step("登录状态失效")
        refresh_token = saved.get("refresh_token")
        if refresh_token:
            try:
                data = api_refresh(http, refresh_token)
                save_session(session_path, data)
                log("本地 token 过期，已用 refresh_token 续期")
                step("已用 refresh_token 续期成功")
                return data["access_token"], False
            except Exception:
                log("refresh_token 已失效，重新登录")
        else:
            log("本地会话已失效，重新登录")
        step("正在重新登录")
    else:
        step("无本地会话，正在登录")
    data = api_login(http, email, password)
    save_session(session_path, data)
    log("登录成功（%s）" % ("强制重新登录" if force_login else "首次/重新登录"))
    step("重新登录成功" if (saved or force_login) else "登录成功")
    return data["access_token"], True


def main():
    parser = argparse.ArgumentParser(
        description="ebondai.com 每日保活（最小调用 + 签到，HTTP 直连不用浏览器）")
    parser.add_argument("--cred", help="账号凭据文件路径（默认自动查找）")
    parser.add_argument("--keyfile", help="API Key 配置文件路径（默认自动查找）")
    parser.add_argument("--session", help="会话文件路径（默认 ~/.config/today-checkin/%s）"
                                         % SESSION_FILENAME)
    parser.add_argument("--model", help="覆盖默认模型（默认取 Key 配置里的默认模型）")
    parser.add_argument("--prompt", default="hi", help="保活调用的内容，默认 hi")
    parser.add_argument("--relogin", action="store_true", help="忽略本地会话，强制重新登录")
    parser.add_argument("--force-call", action="store_true",
                        help="忽略幂等保护，强制再发一次调用（会再花钱）")
    parser.add_argument("--no-checkin", action="store_true", help="只做保活调用，不提交签到")
    parser.add_argument("--no-exchange", action="store_true",
                        help="不把福利余额兑换进账户余额")
    parser.add_argument("--dry-run", action="store_true", help="只读状态，不发包、不签到")
    parser.add_argument("--json", action="store_true", help="输出机器可读 JSON")
    parser.add_argument("--quiet", action="store_true", help="只输出一行结论")
    parser.add_argument("--progress-file", help="进度事件输出文件（JSONL，供面板展示签到过程时间线）")
    args = parser.parse_args()

    logs = []

    def log(msg):
        logs.append(msg)

    progress_fh = _open_progress_file(args.progress_file)

    def step(message):
        _progress_emit(progress_fh, "ebondai", message)

    started = time.time()
    step("正在执行 ebondai 签到")
    now = datetime.now(CST)
    today = now.strftime("%Y-%m-%d")
    result = {"site": "ebondai.com", "host": BASE_URL, "ok": False,
              "date": today, "timezone": TIMEZONE, "exchanged": False}

    cred_path = _find_file(args.cred, CRED_FILENAME)
    if not cred_path:
        print("找不到账号凭据文件 %s。请用 --cred 指定，或放到 %s/"
              % (CRED_FILENAME, CONFIG_DIR), file=sys.stderr)
        return 4
    email, password = load_credentials(cred_path)
    if not email or not password:
        print("凭据文件 %s 里没解析到邮箱/密码字段。" % cred_path, file=sys.stderr)
        return 4
    result["account"] = email

    key_path = _find_file(args.keyfile, KEY_FILENAME)
    if not key_path:
        print("找不到 API Key 配置文件 %s。请用 --keyfile 指定，或放到 %s/"
              % (KEY_FILENAME, CONFIG_DIR), file=sys.stderr)
        return 4
    api_key, key_id, cfg_model = load_apikey(key_path)
    if not api_key:
        print("API Key 配置文件 %s 里没解析到有效的 Key。" % key_path, file=sys.stderr)
        return 4
    model = args.model or cfg_model or DEFAULT_MODEL

    result["model"] = model
    result["key_id"] = key_id
    result["key_name"] = _field(open(key_path, encoding="utf-8").read(), ["Key 名称", "名称"])

    session_path = args.session or default_session_path()
    http = make_session()

    try:
        token, relogin = resolve_token(http, session_path, email, password,
                                       args.relogin, log, step)
        result["relogin"] = relogin

        me = api_me(http, token)
        if me is None:
            step("登录状态失效，正在重新登录")
            data = api_login(http, email, password)
            step("重新登录成功")
            save_session(session_path, data)
            token, relogin = data["access_token"], True
            result["relogin"] = True
            me = api_me(http, token)
        if isinstance(me, dict):
            result["balance"] = me.get("balance")
            result["username"] = me.get("username") or me.get("email")
            result["account_status"] = me.get("status")

        # ---- 1) 今天有没有已经产生过费用的调用（幂等保护，服务端判定）----
        step("正在查询今日调用记录")
        records = api_usage_today(http, token, key_id, today)
        if records is None:
            raise RuntimeError("读取使用记录时被拒绝（HTTP 401）")
        today_calls = [r for r in records
                       if str(r.get("created_at") or "").startswith(today)]
        if key_id:
            today_calls = [r for r in today_calls if r.get("api_key_id") == key_id]
        already = bool(today_calls)
        result["already_called"] = already
        result["calls_today"] = len(today_calls)
        log("今日已产生费用的调用：%d 次" % len(today_calls))
        step("今日已有付费调用，跳过保活发包" if already else "今日暂无付费调用")

        if args.dry_run:
            log("--dry-run：不发包、不签到")
        elif already and not args.force_call:
            newest = max(today_calls, key=lambda r: str(r.get("created_at") or ""))
            result["cost"] = newest.get("actual_cost")
            result["usage_id"] = newest.get("id")
            result["called_at"] = newest.get("created_at")
            log("今天已经调用过，跳过发包（不重复花钱）")
        else:
            if already:
                log("--force-call：忽略幂等保护，再发一次调用")
            balance_before = result.get("balance")
            step("正在发起保活调用")
            data, used_url = chat_once(http, api_key, model,
                                       prompt=args.prompt, max_tokens=8)
            usage = (data or {}).get("usage") or {}
            reply = ""
            try:
                reply = data["choices"][0]["message"]["content"]
            except (KeyError, IndexError, TypeError):
                pass
            log("调用成功：%s，输入 %s / 输出 %s tokens"
                % ((data or {}).get("model", model),
                   usage.get("prompt_tokens"), usage.get("completion_tokens")))
            step("保活调用成功")
            result["called"] = True
            result["endpoint"] = used_url
            result["prompt_tokens"] = usage.get("prompt_tokens")
            result["completion_tokens"] = usage.get("completion_tokens")
            result["reply_preview"] = (reply or "")[:80]

            # ---- 2) 用服务端账单记录核算这次的实际花费 ----
            time.sleep(1)
            records = api_usage_today(http, token, key_id, today) or []
            fresh = [r for r in records
                     if str(r.get("created_at") or "").startswith(today)]
            if key_id:
                fresh = [r for r in fresh if r.get("api_key_id") == key_id]
            if fresh:
                newest = max(fresh, key=lambda r: str(r.get("created_at") or ""))
                result["cost"] = newest.get("actual_cost")
                result["cost_listed"] = newest.get("total_cost")
                result["rate_multiplier"] = newest.get("rate_multiplier")
                result["usage_id"] = newest.get("id")
            else:
                result["cost"] = None
                log("账单记录暂未落库，本次成本以余额差为准")

        me = api_me(http, token) or me
        if isinstance(me, dict):
            result["balance"] = me.get("balance")
            if result.get("cost") is None and result.get("called") and \
                    isinstance(me.get("balance"), (int, float)) and \
                    isinstance(result.get("balance_before"), (int, float)):
                result["cost"] = result["balance_before"] - me["balance"]

        # ---- 3) 福利中心签到（当天已调用过费用才算 eligible）----
        step("正在查询福利中心状态")
        welfare = api_welfare(http, token)
        if welfare is None:
            log("读取福利中心时被拒绝（HTTP 401），跳过签到")
        elif not isinstance(welfare, dict):
            log("福利中心返回格式异常，跳过签到")
        else:
            result["welfare_balance"] = welfare.get("balance")
            result["account_balance"] = welfare.get("account_balance")
            result["current_streak"] = welfare.get("current_streak")
            result["eligible_usage"] = welfare.get("eligible_usage")
            result["today_checked_in"] = welfare.get("today_checked_in")

            if args.no_checkin or args.dry_run:
                log("跳过签到（--no-checkin / --dry-run）")
            elif welfare.get("today_checked_in"):
                log("福利中心：今天已经签到过，不重复签到")
                step("今日已签到")
            elif not welfare.get("eligible_usage"):
                log("福利中心：当天还没有产生费用的调用，不满足签到门槛")
                step("今日暂无付费调用，不满足签到门槛")
            else:
                step("正在提交签到")
                data, was_done = api_checkin(http, token)
                if was_done:
                    log("福利中心：服务端回报今天已签到")
                    step("今日已签到")
                    result["today_checked_in"] = True
                elif isinstance(data, dict):
                    reward = data.get("reward")
                    log("签到成功，获得 %s 福利余额，连续签到 %s 天"
                        % (reward, data.get("current_streak")))
                    step("签到成功")
                    result["checked_in"] = True
                    result["reward"] = reward
                    result["welfare_balance"] = data.get("balance")
                    result["current_streak"] = data.get("current_streak")
                    result["today_checked_in"] = True

        # ---- 4) 把福利余额 1:1 兑换进账户余额（默认开启）----
        if args.no_exchange or args.dry_run:
            log("跳过兑换（--no-exchange / --dry-run）")
        else:
            welfare_bal = result.get("welfare_balance")
            if not isinstance(welfare_bal, (int, float)) or welfare_bal <= 0:
                log("福利余额为 0，无需兑换")
            else:
                amount = round(float(welfare_bal), 6)
                step("正在兑换福利余额")
                data, err = api_exchange(http, token, amount)
                if isinstance(data, dict):
                    log("兑换成功：%s 福利余额已转入账户余额" % amount)
                    step("兑换成功")
                    result["exchanged"] = True
                    result["exchange_amount"] = amount
                    result["welfare_balance"] = data.get("balance", 0)
                    if data.get("account_balance") is not None:
                        result["account_balance"] = data.get("account_balance")
                else:
                    log("兑换失败：%s" % err)
                    result["exchange_error"] = err

        result["ok"] = True
        if result.get("called"):
            result["message"] = "保活调用完成，本次成本 %s" % result.get("cost")
        elif result.get("already_called"):
            result["message"] = "今天已经调用过（未重复发包）"
        else:
            result["message"] = "只读状态（未发包）"
        if result.get("checked_in"):
            result["message"] += "；签到成功 +%s" % result.get("reward")
        elif result.get("today_checked_in"):
            result["message"] += "；今日已签到"
        if result.get("exchanged"):
            result["message"] += "；已兑换 +%s 到账户余额" % result.get("exchange_amount")
        elif result.get("exchange_error"):
            result["message"] += "；兑换失败：%s" % result.get("exchange_error")

    except PermissionError as exc:
        result["error"] = str(exc)
        result["error_type"] = "api_key_rejected"
        _emit(args, result, logs, started, code=5)
        return 5
    except BalanceError as exc:
        result["error"] = str(exc)
        result["error_type"] = "insufficient_balance"
        _emit(args, result, logs, started, code=5)
        return 5
    except ModelError as exc:
        result["error"] = str(exc)
        result["error_type"] = "model_unavailable"
        _emit(args, result, logs, started, code=3)
        return 3
    except requests.RequestException as exc:
        result["error"] = "网络异常：%s" % exc
        result["error_type"] = "network"
        _emit(args, result, logs, started, code=3)
        return 3
    except RuntimeError as exc:
        msg = str(exc)
        result["error"] = msg
        result["error_type"] = "login_failed" if "登录失败" in msg else "api_error"
        _emit(args, result, logs, started, code=2 if "登录失败" in msg else 3)
        return 2 if "登录失败" in msg else 3

    result["session_file"] = session_path
    _emit(args, result, logs, started, code=0)
    return 0


def _emit(args, result, logs, started, code):
    result["elapsed"] = round(time.time() - started, 2)
    result["exit_code"] = code
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    if args.quiet:
        print(result.get("message") or result.get("error"))
        return
    tag = "OK " if result.get("ok") else "ERR"
    print("[%s] ebondai.com 每日保活（%s 北京时间）" % (tag, result.get("date")))
    for line in logs:
        print("  · " + line)
    if result.get("message"):
        print("  结论：" + result["message"])
    if result.get("error"):
        print("  错误：" + str(result["error"]))
    if result.get("balance") is not None:
        print("  账户余额：$%s" % result["balance"])
    if result.get("welfare_balance") is not None:
        print("  福利余额：$%s | 连续签到：%s 天"
              % (result.get("welfare_balance"), result.get("current_streak")))
    if result.get("exchanged"):
        print("  已兑换：$%s 福利余额 -> 账户余额（当前账户余额 $%s）"
              % (result.get("exchange_amount"), result.get("account_balance")))
    elif result.get("exchange_error"):
        print("  兑换失败：%s" % result.get("exchange_error"))
    if result.get("session_file"):
        print("  会话文件：%s" % result["session_file"])
    print("  耗时：%ss" % result["elapsed"])


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
