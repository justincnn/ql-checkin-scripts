#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# cron: 0 9 * * *
# new Env('139云盘云朵任务')
"""
139云盘(中国移动云盘) 云朵任务自动执行脚本
适配青龙面板,纯 Python 标准库实现,无需 pip 安装任何依赖。

功能:
  - 全自动认证链: querySpecToken -> ssoToken -> tyrzLogin -> jwtToken
  - AI豆(原云朵)余额统计(getCloudNum)
  - 每日签到(startSignIn,+3豆,基于 infoV3 防重复)
  - 拉取带豆数的云朵任务列表(taskListV3)
  - 自动完成纯接口可完成的任务:
      * 319 小云互动礼(循环点击直至完成,送云朵)
      * 118 调查问卷(单次点击即完成)
      * 605/606/431 等点击即完成的任务(YDYP_TRY_ALL)
      * 106 手动上传一个文件(上传后 FINISH,完成后可自动删除)
  - 只读展示当前 AI豆余额可兑换的奖品清单(exchangeList,不执行兑换)
  - 运行结束发送通知总结(青龙内置通知 / PushPlus / Server酱)

环境变量(在青龙面板配置):
  YDYP_ACCOUNTS          多账号,每行一个,格式: 手机号|Basic <token> (优先)
  YDYP_PHONE             单账号手机号 (与 YDYP_AUTH 配合)
  YDYP_AUTH              单账号 Authorization 值(Basic 开头,可省略 Basic 前缀)
  YDYP_TRY_ALL           是否尝试所有未完成任务,默认 true
  YDYP_INTERACT_LIMIT    小云互动礼最大点击次数,默认 30
  YDYP_UPLOAD            是否执行上传任务(106),默认 true
  YDYP_DELETE_AFTER      上传完成后是否删除文件,默认 true
  PUSHPLUS_TOKEN         PushPlus 推送 token(可选)
  SENDKEY                Server酱 SendKey(可选)

如何获取 token(Authorization):
  1. 浏览器登录 https://yun.139.com (网页版)
  2. 打开开发者工具(F12) → 网络(Network)
  3. 刷新页面,任选一个 api.yun.139.com 或 yun.139.com 的请求
  4. 查看请求头 Authorization 字段(形如 Basic xxxxxx)
  5. 连同手机号填入青龙面板环境变量
"""
import base64
import hashlib
import json
import os
import random
import re
import string
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

BASE_URL = "https://yun.139.com/orchestration/auth-rebuild/token/v1.0/querySpecToken"
TYRZ_URL = "https://caiyun.feixin.10086.cn/portal/auth/tyrzLogin.action"
TASKLIST_URL = "https://caiyun.feixin.10086.cn/market/signin/task/taskList"
CLICK_URL = "https://caiyun.feixin.10086.cn/market/signin/task/click"

# H5 云朵中心(ycloud 域)
H5_BASE = "https://m.mcloud.139.com/ycloud"
H5_GET_CLOUD_NUM = H5_BASE + "/signin/page/getCloudNum"
H5_INFO_V3 = H5_BASE + "/signin/page/infoV3"
H5_START_SIGN_IN = H5_BASE + "/signin/page/startSignIn"
H5_TASK_LIST_V3 = H5_BASE + "/signin/task/taskListV3"
H5_EXCHANGE_LIST = H5_BASE + "/signin/page/exchangeList"

# 兑换奖品分类映射(exchangeList result 的 key)
EXCHANGE_CATEGORIES = {
    "0": "云盘", "1": "视频", "2": "音乐", "5": "外卖", "7": "快递",
    "8": "转存券", "9": "工具", "11": "美食", "15": "流量",
}

# 新平台 personal 上传/删除域
PERSONAL_HOST = "https://personal-kd-njs.yun.139.com/hcy"
FILE_CREATE_URL = PERSONAL_HOST + "/file/create"
FILE_COMPLETE_URL = PERSONAL_HOST + "/file/complete"
RECYCLE_TRASH_URL = PERSONAL_HOST + "/recyclebin/batchTrash"

MARKET_NAME = "sign_in_3"

# 纯接口即可完成的任务(其余任务需要真实操作:上传/分享/PC端/阅读等)
AUTO_TASKS = {
    319: "小云互动礼",   # 循环点击直至 FINISH,每次送云朵
    118: "调查问卷",     # 单次点击即 FINISH
}
# 点击即完成的任务(try_all 时也会尝试) —— 经验证 605/606/431
CLICK_DONE_TASKS = {605, 606, 431}

# 上传文件名前缀(可被识别并清理)
UPLOAD_PREFIX = "yunduo_task_"

# 完整 x-DeviceInfo / X-Yun-Client-Info(必须与此一致,否则 00010014)
X_DEVICE_INFO = "||9|7.14.0|chrome|120.0.0.0|||windows 10||zh-CN|||"
X_YUN_CLIENT_INFO = "||9|7.14.0|chrome|120.0.0.0|||windows 10||zh-CN|||dW5kZWZpbmVk||"


# ---------------------------------------------------------------------------
# HTTP 工具
# ---------------------------------------------------------------------------
def _http(url, method="GET", data=None, headers=None, timeout=15):
    """返回 (status, text)"""
    h = {
        "Accept": "*/*",
        "Accept-Language": "zh-CN,zh;q=0.9",
        "User-Agent": UA,
        "Referer": "https://yun.139.com/w/",
    }
    if headers:
        h.update(headers)
    body = None
    if data is not None:
        if isinstance(data, (dict, list)):
            body = json.dumps(data, ensure_ascii=False).encode("utf-8")
            h.setdefault("Content-Type", "application/json;charset=UTF-8")
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


def _basic(phone, auth_token):
    """
    构造 Authorization 值。
    用户提供的 token 通常已是 base64("pc:phone:authToken") 完整值;
    若为裸 authToken 则自动包装。
    """
    t = auth_token.strip()
    try:
        dec = base64.b64decode(t + "=" * (-len(t) % 4)).decode("utf-8", "replace")
        if dec.startswith("pc:"):
            return "Basic " + t
    except Exception:  # noqa: BLE001
        pass
    return "Basic " + base64.b64encode(
        ("pc:%s:%s" % (phone, t)).encode("utf-8")
    ).decode("utf-8")


def sanitize_token(val):
    """去掉 Basic 前缀、引号与空白"""
    if not val:
        return val
    v = val.strip().strip("'").strip('"')
    if v.lower().startswith("basic "):
        v = v[6:].strip()
    return v


# ---------------------------------------------------------------------------
# mcloud-sign 签名(新平台 personal 上传/删除接口)
# ---------------------------------------------------------------------------
def _esc(s):
    """encodeURIComponent 等价实现(保留 ! ' ( ) * )"""
    s = urllib.parse.quote(s, safe="")
    return (s.replace("%21", "!").replace("%27", "'")
             .replace("%28", "(").replace("%29", ")").replace("%2A", "*"))


def _mcloud_sign(body_str):
    """
    sign = md5( md5( base64( sort-chars( encodeURIComponent(body) ) ) ) + md5(ts + ":" + rand) ).upper()
    返回 "ts,rand,sign"
    """
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    rand = "".join(random.choice(string.ascii_letters + string.digits) for _ in range(16))
    eb = _esc(body_str)
    sorted_eb = "".join(sorted(eb))
    b64 = base64.b64encode(sorted_eb.encode("utf-8")).decode("ascii")
    h1 = hashlib.md5(b64.encode("utf-8")).hexdigest()
    h2 = hashlib.md5((ts + ":" + rand).encode("utf-8")).hexdigest()
    sign = hashlib.md5((h1 + h2).encode("utf-8")).hexdigest().upper()
    return "%s,%s,%s" % (ts, rand, sign)


# ---------------------------------------------------------------------------
# 认证链: SSO Token -> JWT Token
# ---------------------------------------------------------------------------
def get_sso_token(phone, basic):
    """POST querySpecToken -> ssoToken"""
    payload = {
        "toSourceId": "001005",
        "account": phone,
        "commonAccountInfo": {"account": phone, "accountType": 1},
    }
    hdrs = {"Authorization": basic, "Content-Type": "application/json;charset=UTF-8"}
    st, txt = _http(BASE_URL, method="POST", data=payload, headers=hdrs)
    if st != 200:
        return None, "SSO接口HTTP %s" % st
    try:
        d = json.loads(txt)
    except ValueError:
        return None, "SSO返回非JSON"
    if not d.get("success") and d.get("code") != "0":
        return None, "SSO失败: %s %s" % (d.get("code"), d.get("message", ""))
    tok = (d.get("data") or {}).get("token")
    if not tok:
        return None, "SSO无token字段"
    return tok, None


def get_jwt_token(sso, basic):
    """GET tyrzLogin.action?ssoToken= -> jwtToken"""
    url = TYRZ_URL + "?ssoToken=" + urllib.parse.quote(sso, safe="")
    hdrs = {"Authorization": basic}
    st, txt = _http(url, method="GET", headers=hdrs)
    if st != 200:
        return None, "JWT接口HTTP %s" % st
    try:
        d = json.loads(txt)
    except ValueError:
        return None, "JWT返回非JSON"
    if d.get("code") != 0:
        return None, "JWT失败: %s %s" % (d.get("code"), d.get("msg", ""))
    tok = (d.get("result") or {}).get("token")
    if not tok:
        return None, "JWT无token字段"
    return tok, None


def jwt_headers(jwt):
    """老平台任务接口需要的 jwt 双头"""
    return {"Cookie": "jwtToken=%s" % jwt, "jwtToken": jwt}


def h5_headers(jwt):
    """H5 ycloud 接口头: jwttoken + activityid + deviceid"""
    return {
        "jwttoken": jwt,
        "activityid": MARKET_NAME,
        "deviceid": "ql_%s" % random.randint(100000, 999999),
        "Content-Type": "application/json;charset=UTF-8",
        "Origin": "https://m.mcloud.139.com/",
        "Referer": "https://m.mcloud.139.com/",
    }


# ---------------------------------------------------------------------------
# H5 云朵中心接口
# ---------------------------------------------------------------------------
def h5_get(jwt, url):
    st, txt = _http(url, method="GET", headers=h5_headers(jwt))
    if st != 200:
        return None, "H5 HTTP %s" % st
    try:
        return json.loads(txt), None
    except ValueError:
        return None, "H5返回非JSON"


def get_cloud_num(jwt):
    """AI豆余额。result 为数字"""
    d, err = h5_get(jwt, H5_GET_CLOUD_NUM)
    if err:
        return None, err
    if d.get("code") != 0:
        return None, "getCloudNum失败: %s %s" % (d.get("code"), d.get("msg", ""))
    return d.get("result"), None


def get_sign_info(jwt):
    """infoV3: 返回 (result, err)。result 含 beforeTotal, signInPoints, cal[]"""
    d, err = h5_get(jwt, H5_INFO_V3)
    if err:
        return None, err
    if d.get("code") != 0:
        return None, "infoV3失败: %s %s" % (d.get("code"), d.get("msg", ""))
    result = d.get("result") or {}
    return result, None


def get_exchange_list(jwt):
    """兑换奖品列表(exchangeList,只读)。result 为 {分类ID: 奖品数组}"""
    d, err = h5_get(jwt, H5_EXCHANGE_LIST)
    if err:
        return None, err
    if d.get("code") != 0:
        return None, "exchangeList失败: %s %s" % (d.get("code"), d.get("msg", ""))
    return d.get("result") or {}, None


def is_signed_today(result):
    """根据 cal 判断今天是否已签到"""
    cal = result.get("cal") or []
    today = int(time.strftime("%d"))
    for c in cal:
        if c.get("d") == today and c.get("currentMonth") == 1:
            return bool(c.get("s"))
    return None  # 未知


def start_sign_in(jwt):
    """每日签到,+signInPoints 豆。result 含 todaySignIn"""
    d, err = h5_get(jwt, H5_START_SIGN_IN)
    if err:
        return None, err
    if d.get("code") != 0:
        return None, "startSignIn失败: %s %s" % (d.get("code"), d.get("msg", ""))
    return d.get("result") or {}, None


def task_list_v3(jwt):
    """taskListV3: 返回 (list_of_tasks, err)。每任务含 description(豆数)/state"""
    payload = {"marketname": MARKET_NAME, "clientVersion": ""}
    st, txt = _http(H5_TASK_LIST_V3, method="POST", data=payload, headers=h5_headers(jwt))
    if st != 200:
        return None, "taskListV3 HTTP %s" % st
    try:
        d = json.loads(txt)
    except ValueError:
        return None, "taskListV3返回非JSON"
    if d.get("code") != 0:
        return None, "taskListV3失败: %s %s" % (d.get("code"), d.get("msg", ""))
    result = d.get("result") or []
    # result 可能是数组,也可能包了一层
    tasks = []
    if isinstance(result, list):
        tasks = result
    elif isinstance(result, dict):
        for v in result.values():
            if isinstance(v, list):
                tasks.extend(v)
            elif isinstance(v, dict):
                tasks.append(v)
    return tasks, None


# ---------------------------------------------------------------------------
# 新平台上传/删除
# ---------------------------------------------------------------------------
def _personal_headers(basic, body_str):
    """file/create、file/complete、recyclebin/batchTrash 通用头"""
    return {
        "Accept": "application/json,text/plain,*/*",
        "Authorization": basic,
        "Caller": "web",
        "Cms-Device": "default",
        "Mcloud-Channel": "1000101",
        "Mcloud-Client": "10701",
        "Mcloud-Route": "001",
        "Mcloud-Sign": _mcloud_sign(body_str),
        "Mcloud-Version": "7.14.0",
        "Origin": "https://yun.139.com/",
        "Referer": "https://yun.139.com/",
        "x-DeviceInfo": X_DEVICE_INFO,
        "x-huawei-channelSrc": "10000034",
        "x-inner-ntwk": "2",
        "x-m4c-caller": "PC",
        "x-m4c-src": "10002",
        "x-SvcType": "1",
        "X-Yun-Api-Version": "v1",
        "X-Yun-App-Channel": "10000034",
        "X-Yun-Channel-Source": "10000034",
        "X-Yun-Client-Info": X_YUN_CLIENT_INFO,
        "X-Yun-Module-Type": "100",
        "X-Yun-Svc-Type": "1",
        "Content-Type": "application/json",
    }


def _personal_post(basic, url, payload):
    body_str = json.dumps(payload, separators=(",", ":"))
    st, txt = _http(url, method="POST", data=body_str, headers=_personal_headers(basic, body_str))
    if st != 200:
        return None, "personal HTTP %s: %s" % (st, txt[:200])
    try:
        d = json.loads(txt)
    except ValueError:
        return None, "personal返回非JSON: %s" % txt[:200]
    if d.get("code") != "0000" and d.get("code") != 0:
        return d, "personal失败: code=%s msg=%s" % (d.get("code"), d.get("msg", "").get("message", ""))
    return d, None


def upload_small_file(basic, name, size=10):
    """上传一个随机小文件,返回 (fileId, err)。成功后可触发 106 任务 FINISH"""
    data = os.urandom(size)
    sha = hashlib.sha256(data).hexdigest()
    payload = {
        "contentHash": sha,
        "contentHashAlgorithm": "SHA256",
        "contentType": "application/octet-stream",
        "parallelUpload": False,
        "partInfos": [{"partNumber": 1, "partSize": size}],
        "size": size,
        "parentFileId": "/",
        "name": name,
        "type": "file",
        "fileRenameMode": "auto_rename",
    }
    d, err = _personal_post(basic, FILE_CREATE_URL, payload)
    if err:
        return None, err
    dat = d.get("data") or {}
    file_id = dat.get("fileId")
    upload_id = dat.get("uploadId")
    parts = dat.get("partInfos") or []
    if not file_id or not parts:
        return None, "create 未返回 uploadUrl"
    url = parts[0].get("uploadUrl")
    if not url:
        return None, "create 未返回 uploadUrl"

    # PUT 分片数据
    req = urllib.request.Request(
        url, data=data, method="PUT",
        headers={
            "Content-Type": "application/octet-stream",
            "Content-Length": str(len(data)),
            "Origin": "https://yun.139.com/",
            "Referer": "https://yun.139.com/",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            if resp.status != 200:
                return None, "PUT 分片 HTTP %s" % resp.status
    except Exception as e:  # noqa: BLE001
        return None, "PUT 分片失败: %s" % str(e)[:200]

    # file/complete
    comp = {"contentHash": sha, "contentHashAlgorithm": "SHA256",
            "fileId": file_id, "uploadId": upload_id}
    d2, err2 = _personal_post(basic, FILE_COMPLETE_URL, comp)
    if err2:
        return None, "complete失败: %s" % err2
    return file_id, None


def trash_files(basic, file_ids):
    """删除(移入回收站)文件"""
    if not file_ids:
        return True, None
    payload = {"fileIds": file_ids}
    d, err = _personal_post(basic, RECYCLE_TRASH_URL, payload)
    if err:
        return False, err
    return True, None


# ---------------------------------------------------------------------------
# 单账号执行
# ---------------------------------------------------------------------------
def run_account(phone, auth_token, try_all=False, interact_limit=30,
                do_upload=True, delete_after=True):
    """
    执行一个账号的完整流程: 认证 -> 签到 -> 任务 -> 上传 -> 余额统计。
    返回 (ok, lines)  lines 为总结行列表
    """
    lines = []
    phone_masked = phone[:3] + "****" + phone[-4:] if len(phone) >= 7 else phone

    # ---- 认证 ----
    basic = _basic(phone, auth_token)
    sso, err = get_sso_token(phone, basic)
    if not sso:
        lines.append("❌ %s 认证失败(SSO): %s" % (phone_masked, err))
        return False, lines
    jwt, err = get_jwt_token(sso, basic)
    if not jwt:
        lines.append("❌ %s 认证失败(JWT): %s" % (phone_masked, err))
        return False, lines
    lines.append("✅ %s 认证成功" % phone_masked)

    # ---- 云朵余额 ----
    balance, err = get_cloud_num(jwt)
    balance_txt = "(获取失败:%s)" % err if balance is None else str(balance)
    lines.append("💰 %s 当前AI豆余额: %s" % (phone_masked, balance_txt))

    # ---- 每日签到 ----
    sign_info, err = get_sign_info(jwt)
    signed_today = is_signed_today(sign_info) if sign_info else None
    if err:
        lines.append("⚠️ %s 签到信息获取失败: %s" % (phone_masked, err))
    elif signed_today:
        pts = (sign_info or {}).get("signInPoints", 3)
        lines.append("✅ %s 今天已签到(+%s豆)" % (phone_masked, pts))
    else:
        r, err2 = start_sign_in(jwt)
        if err2:
            lines.append("❌ %s 签到失败: %s" % (phone_masked, err2))
        elif r and r.get("todaySignIn"):
            pts = (r).get("signInPoints", 3)
            lines.append("✅ %s 签到成功(+%s豆)!" % (phone_masked, pts))
        else:
            lines.append("⚠️ %s 签到未确认(todaySignIn=false)" % phone_masked)

    # ---- 任务列表 ----
    tasks, err = task_list_v3(jwt)
    if err:
        lines.append("❌ %s 拉取任务失败: %s" % (phone_masked, err))
        return False, lines

    def state_of(t):
        return t.get("state", "WAIT") or "WAIT"

    if tasks:
        finished = 0
        wait_ids = []
        for t in sorted(tasks, key=lambda x: x.get("sort", 0) or 0):
            tid = t.get("id")
            raw_name = t.get("name") or ("任务%s" % tid)
            name = re.sub(r"<[^>]+>", "", raw_name).strip()
            desc = re.sub(r"<[^>]+>", "", t.get("description") or "").strip()
            label = "%s[%s]%s" % (name, tid, ("(%s)" % desc) if desc else "")
            if state_of(t) == "FINISH":
                finished += 1
                lines.append("✅ 任务 %s 已完成" % label)
                continue
            if t.get("enable") == 0:
                lines.append("🚫 任务 %s 已停用" % label)
                continue

            if tid == 319:  # 小云互动礼: 循环点击直至 FINISH
                clicked = 0
                reward = None
                cur = t
                while clicked < interact_limit:
                    ok, msg = click_task(jwt, tid)
                    clicked += 1
                    if ok and msg and ("云朵" in msg or "云" in msg):
                        reward = msg
                    time.sleep(0.3)
                    if clicked % 5 == 0:
                        tl, e2 = task_list_v3(jwt)
                        if not e2:
                            for tc in tl:
                                if tc.get("id") == tid:
                                    cur = tc
                                    break
                    if state_of(cur) == "FINISH":
                        break
                if state_of(cur) == "FINISH":
                    finished += 1
                    lines.append("✅ 任务 %s 完成(点击%d次)%s"
                                 % (label, clicked, (" [%s]" % reward) if reward else ""))
                else:
                    wait_ids.append(tid)
                    lines.append("⏳ 任务 %s 点击%d次未完成" % (label, clicked))
                continue

            # 纯点击任务(118 等)或 try_all 时尝试所有任务
            if tid in AUTO_TASKS or tid in CLICK_DONE_TASKS or try_all:
                ok, msg = click_task(jwt, tid)
                time.sleep(0.3)
                tl, e2 = task_list_v3(jwt)
                cur = t
                if not e2:
                    for tc in tl:
                        if tc.get("id") == tid:
                            cur = tc
                            break
                if ok and state_of(cur) == "FINISH":
                    finished += 1
                    lines.append("✅ 任务 %s 完成" % label)
                elif ok:
                    wait_ids.append(tid)
                    lines.append("⏳ 任务 %s 已点击但未完成(%s)" % (label, msg))
                else:
                    wait_ids.append(tid)
                    lines.append("❌ 任务 %s 失败: %s" % (label, msg))
                continue

            # 106 手动上传一个文件: 上传触发 FINISH
            if tid == 106 and do_upload:
                fname = "%s%s_%d.txt" % (UPLOAD_PREFIX, time.strftime("%Y%m%d%H%M%S"), random.randint(1000, 9999))
                fid, uerr = upload_small_file(basic, fname)
                if fid:
                    time.sleep(1.5)
                    tl, e2 = task_list_v3(jwt)
                    cur_s = "WAIT"
                    if not e2:
                        for tc in tl:
                            if tc.get("id") == 106:
                                cur_s = tc.get("state", "WAIT")
                                break
                    if cur_s == "FINISH":
                        finished += 1
                        lines.append("✅ 任务 %s 完成(已上传 %s)" % (label, fname))
                    else:
                        wait_ids.append(tid)
                        lines.append("ℹ️ 任务 %s 已上传%s但未标记完成" % (label, fname))
                    if delete_after:
                        okt, derr = trash_files(basic, [fid])
                        lines.append("🗑 清理上传文件 %s: %s"
                                     % (fname, "已删除" if okt else ("失败:" + (derr or ""))))
                else:
                    wait_ids.append(tid)
                    lines.append("⚠️ 任务 %s 上传失败: %s" % (label, uerr))
                continue

            # 其他需要真实操作的任务
            wait_ids.append(tid)
            lines.append("⏳ 任务 %s 需要真实操作(上传/分享/PC端/阅读)" % label)

        total = len(tasks)
        lines.append("📊 %s 任务汇总: 完成 %d/%d" % (phone_masked, finished, total))

    # ---- 余额统计 ----
    if balance is not None:
        lines.append("💰 %s 当前AI豆余额: %s" % (phone_masked, balance))

    # ---- 可兑换清单 ----
    if balance is not None:
        ex, err = get_exchange_list(jwt)
        if err:
            lines.append("⚠️ %s 可兑换清单获取失败: %s" % (phone_masked, err))
        else:
            afford = []
            for cat_id, prizes in ex.items():
                if not isinstance(prizes, list):
                    continue
                cat = EXCHANGE_CATEGORIES.get(str(cat_id), "分类%s" % cat_id)
                for p in prizes:
                    price = p.get("POrder")
                    try:
                        price = int(price)
                    except (TypeError, ValueError):
                        continue
                    online = p.get("onLine", 1)
                    if 0 < price <= balance and online not in (0, "0", False):
                        afford.append((price, p.get("prizeName", "?"), cat,
                                       p.get("dailyRemainderCount", "?")))
            if afford:
                afford.sort(key=lambda x: x[0])
                lines.append("🛒 %s 可兑换清单(余额%d, %d项):"
                             % (phone_masked, balance, len(afford)))
                for price, name, cat, remain in afford:
                    lines.append("   · %s %s豆[%s] 今日剩%s" % (name, price, cat, remain))
            else:
                lines.append("🛒 %s 暂无余额可兑换的奖品" % phone_masked)

    return True, lines


# ---------------------------------------------------------------------------
# 老平台 click 任务接口
# ---------------------------------------------------------------------------
def click_task(jwt, tid):
    """点击任务,返回 (ok, msg)"""
    url = "%s?key=task&id=%s" % (CLICK_URL, tid)
    st, txt = _http(url, method="GET", headers=jwt_headers(jwt))
    if st != 200:
        return False, "click HTTP %s" % st
    try:
        d = json.loads(txt)
    except ValueError:
        return False, "click返回非JSON"
    if d.get("code") == 0:
        result = d.get("result") or d.get("data") or ""
        return True, str(result) if result else "success"
    return False, "click失败: %s %s" % (d.get("code"), d.get("msg", ""))


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
    """
    优先 YDYP_ACCOUNTS(多账号, 每行 手机号|Basic <token>);
    否则 YDYP_PHONE + YDYP_AUTH。
    """
    accounts = []
    multi = os.environ.get("YDYP_ACCOUNTS", "").strip()
    if multi:
        for line in multi.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split("|")
            if len(parts) >= 2:
                phone = parts[0].strip()
                tok = sanitize_token(parts[1])
                if phone and tok:
                    accounts.append((phone, tok))
    else:
        phone = os.environ.get("YDYP_PHONE", "").strip()
        tok = sanitize_token(os.environ.get("YDYP_AUTH", "").strip())
        if phone and tok:
            accounts.append((phone, tok))
    return accounts


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
def main():
    accounts = parse_accounts()
    if not accounts:
        print("未配置账号。请设置环境变量:")
        print("  多账号: YDYP_ACCOUNTS (每行 手机号|Basic <token>)")
        print("  单账号: YDYP_PHONE + YDYP_AUTH")
        sys.exit(1)

    try_all = os.environ.get("YDYP_TRY_ALL", "").strip().lower()
    try_all = try_all not in ("", "0", "false", "no", "off")  # 默认开启
    interact_limit = int(os.environ.get("YDYP_INTERACT_LIMIT", "30"))
    do_upload = os.environ.get("YDYP_UPLOAD", "").strip().lower() not in ("0", "false", "no", "off")
    delete_after = os.environ.get("YDYP_DELETE_AFTER", "").strip().lower() not in ("0", "false", "no", "off")

    print("=" * 50)
    print("139云盘 云朵任务开始,共 %d 个账号" % len(accounts))
    print("=" * 50)

    all_lines = []
    ok_count = 0
    for phone, tok in accounts:
        print("\n>>> 账号 %s..." % (phone[:3] + "****" + phone[-4:]))
        ok, lines = run_account(phone, tok, try_all=try_all, interact_limit=interact_limit,
                                do_upload=do_upload, delete_after=delete_after)
        for ln in lines:
            print("  " + ln)
        all_lines.append("【%s】" % (phone[:3] + "****" + phone[-4:]))
        all_lines.extend(lines)
        if ok:
            ok_count += 1

    # 总结
    summary = "\n".join(all_lines)
    print("\n" + "=" * 50)
    print(summary)
    print("=" * 50)

    # 通知
    title = "139云盘云朵任务: %d/%d 账号成功" % (ok_count, len(accounts))
    channel = send_notify(title, summary)
    print("\n通知渠道: %s" % channel)
    return 0 if ok_count > 0 else 1


if __name__ == "__main__":
    sys.exit(main())