"""
new Env('速维云签到')

cron: 10 9 * * *

速维云（www.svyun.com）每日签到 + 转盘抽奖（青龙订阅版）

功能：账号密码自动登录 → 每日签到（含连签奖励）→ 自动用完当前可用的抽奖次数，
     多账号。无需抓包，拿账号密码即可。
     接口经线上验证（2026-10，魔方财务系统通用 console API）：
       登录   POST https://www.svyun.com/console/v1/login（密码 AES-128-CBC 加密，
              密钥/IV 为魔方财务前端通用值，服务端可正常解密）
       签到   GET/POST https://www.svyun.com/console/v1/daily_checkin/（info / checkin）
       抽奖   GET/POST https://www.svyun.com/console/v1/lucky_draw/（活动列表/剩余次数/draw）
     鉴权为登录返回的 JWT（Authorization: Bearer <jwt>），HTTP 层始终 200，
     业务结果看 JSON 体内的 status 字段（200 成功、400 参数/密码错误、401 未登录）。

环境变量：
  SVYUN_ACCOUNT   速维云账号（必填）：格式 用户名:密码，例如 user@example.com:Pass1234。
                  用户名与密码之间用英文冒号分隔（也认全角冒号或 ----）；
                  密码里含冒号没关系，按第一个冒号切分。
  多账号：值内用换行或 & 分隔多份，也支持编号轮询
                  SVYUN_ACCOUNT、SVYUN_ACCOUNT_1、SVYUN_ACCOUNT_2 ……（编号连续即可，
                  断号自动跳过；某个账号失败不影响其余账号）。
                  注意：密码里含 & 时请改用换行或编号变量分隔账号。

  SVYUN_DELAY     每日随机延迟上限（分钟），默认 10：脚本启动后随机等待 0~10 分钟
                  再执行，让每天实际签到时间不同；设为 0 关闭（手动调试用）。
                  多账号之间随机间隔 3~15 秒。

抽奖说明：签到和连签会送抽奖机会，脚本签到后自动查询剩余次数并全部抽完，
     奖品汇总进推送（「谢谢参与」也会如实展示）。没有活动或没次数时跳过不算失败。

依赖：requests、pycryptodome
     （青龙 依赖管理 → Python3 → 分别安装 requests 和 pycryptodome）
"""

import base64
import json
import os
import random
import re
import sys
import time

import requests

try:
    from Crypto.Cipher import AES
    from Crypto.Util.Padding import pad
except ImportError:  # 青龙镜像不一定预装，给一句能照做的安装提示
    AES = None

TIMEOUT = 15
API_BASE = "https://www.svyun.com/console/v1"
SITE = "https://www.svyun.com"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36")
# 魔方财务前端通用登录密码加密参数（AES-128-CBC + PKCS7 + base64），线上验证服务端可解
AES_KEY = b"idcsmart.finance"
AES_IV = b"9311019310287172"
DEVICE_ID = "qinglong-python-checkin"
THANKS_WORDS = ("谢谢", "参与奖", "未中", "再接再厉")


class SvyunError(Exception):
    """请求层失败（网络/HTTP/非 JSON），文案已可直接展示。"""


def encrypt_password(password: str) -> str:
    cipher = AES.new(AES_KEY, AES.MODE_CBC, AES_IV)
    return base64.b64encode(cipher.encrypt(pad(password.encode("utf-8"), AES.block_size))).decode()


class Account:
    def __init__(self, index: int, username: str, password: str):
        self.index = index
        self.username = username.strip()
        self.password = password.strip()

    @property
    def label(self) -> str:
        """脱敏展示名，用于区分账号。"""
        u = self.username
        if "@" in u:
            name, _, domain = u.partition("@")
            head = name[:3] if len(name) > 3 else name[:1]
            return f"{head}***@{domain}"
        if len(u) > 5:
            return f"{u[:3]}***{u[-2:]}"
        return f"{u[:1]}***"

    # ---------- 请求 ----------
    def _session(self, jwt: str = "") -> requests.Session:
        s = requests.Session()
        s.headers.update({
            "User-Agent": UA,
            "language": "zh-cn",
            "Accept": "application/json, text/plain, */*",
            "Content-Type": "application/json",
            "Origin": SITE,
            "Referer": f"{SITE}/",
        })
        if jwt:
            s.headers["Authorization"] = f"Bearer {jwt}"
        return s

    @staticmethod
    def _req(sess: requests.Session, method: str, path: str, **kwargs) -> dict:
        try:
            resp = sess.request(method, f"{API_BASE}/{path.lstrip('/')}", timeout=TIMEOUT, **kwargs)
        except requests.RequestException as e:
            raise SvyunError(f"网络异常 {e.__class__.__name__}: {e}")
        if resp.status_code != 200:
            raise SvyunError(f"HTTP {resp.status_code}")
        try:
            return resp.json()
        except ValueError:
            raise SvyunError("响应非 JSON（接口可能已变更）")

    # ---------- 各步骤 ----------
    def login(self) -> requests.Session:
        payload = {
            "type": "password",
            "account": self.username,
            "phone_code": "86",
            "code": "",
            "password": encrypt_password(self.password),
            "remember_password": "0",
            "captcha": "",
            "token": "",
            "security_verify_method": "",
            "security_verify_value": "",
            "certify_id": "",
            "security_verify_token": "",
        }
        body = self._req(self._session(), "POST", "login", json=payload)
        jwt = (body.get("data") or {}).get("jwt")
        if body.get("status") != 200 or not jwt:
            msg = str(body.get("msg") or "未知原因")
            hint = ""
            if any(word in msg for word in ("验证码", "安全", "验证", "频繁")):
                hint = "（触发了登录风控：先浏览器登录一次再试；若仍失败请核对账号密码）"
            elif "密码" in msg:
                hint = "（请核对 SVYUN_ACCOUNT 里的用户名和密码，格式 用户名:密码）"
            raise SvyunError(f"登录失败: {msg}{hint}")
        return self._session(jwt)

    def checkin(self, sess: requests.Session) -> str:
        info = ((self._req(sess, "GET", "daily_checkin/info").get("data") or {}).get("info")) or {}
        if info.get("today_checked"):
            return self._fmt_stats("今日已签到", info)
        sign = self._req(sess, "POST", "daily_checkin/checkin", json={})
        print(f"  签到响应: {json.dumps(sign, ensure_ascii=False)[:200]}", flush=True)
        if sign.get("status") == 200:
            data = sign.get("data") or {}
            line = self._fmt_stats("签到成功", {
                "current_streak": data.get("current_streak", info.get("current_streak")),
                "total_checkins": data.get("checkin_count", info.get("total_checkins")),
            })
            extra_draw = self._count_draw_rewards(data)
            if extra_draw:
                line += f"，获得抽奖机会 {extra_draw} 次"
            return line
        if "已签" in str(sign.get("msg", "")):
            return self._fmt_stats("今日已签到", info)
        return f"签到失败: {sign.get('msg') or sign.get('status')}"

    @staticmethod
    def _fmt_stats(prefix: str, stats: dict) -> str:
        parts = []
        if isinstance(stats.get("current_streak"), int):
            parts.append(f"连签 {stats['current_streak']} 天")
        if isinstance(stats.get("total_checkins"), int):
            parts.append(f"累计 {stats['total_checkins']} 次")
        return f"{prefix}（{'，'.join(parts)}）" if parts else prefix

    @staticmethod
    def _count_draw_rewards(data: dict) -> int:
        """签到记录 reward_info 里 times 字段之和 = 本次到手的抽奖次数。"""
        reward = data.get("reward_info") or {}
        results = list((reward.get("basic") or {}).get("results") or []) + list(reward.get("streak") or [])
        total = 0
        for item in results:
            try:
                total += int(float(item.get("times") or 0))
            except (TypeError, ValueError):
                continue
        return total

    def draw(self, sess: requests.Session) -> str:
        acts = ((self._req(sess, "GET", "lucky_draw/activity/list").get("data") or {}).get("list")) or []
        target = next((a for a in acts
                       if a.get("id") is not None
                       and (a.get("user_info") or {}).get("can_join") is not False), None)
        if target is None:
            return "抽奖: 暂无可参与的抽奖活动（跳过）"
        activity_id = target["id"]
        times_data = self._req(sess, "GET", "lucky_draw/getDrawTimesInfo",
                               params={"activity_id": activity_id}).get("data") or {}
        try:
            times = int(float(times_data.get("available_times") or 0))
        except (TypeError, ValueError):
            times = 0
        if times <= 0:
            return "抽奖: 无可用抽奖次数（跳过）"
        prizes = []
        for i in range(times):
            body = self._req(sess, "POST", "lucky_draw/draw",
                             json={"activity_id": activity_id, "device_id": DEVICE_ID})
            print(f"  第 {i + 1}/{times} 次抽奖响应: {json.dumps(body, ensure_ascii=False)[:200]}",
                  flush=True)
            if body.get("status") != 200:
                detail = f"{len(prizes)}/{times} 次后中断（{body.get('msg')}）" if prizes else str(body.get("msg"))
                return f"抽奖: {detail}" if prizes else f"抽奖失败: {detail}"
            data = body.get("data") or {}
            prizes.append(str((data.get("prize") or {}).get("name") or body.get("msg") or "未知结果"))
            if i < times - 1:
                time.sleep(1)
        counter = {}
        for p in prizes:
            counter[p] = counter.get(p, 0) + 1
        text = "，".join(f"{name} ×{count}" for name, count in counter.items())
        won = [p for p in prizes if p and not any(w in p for w in THANKS_WORDS)]
        note = "，有中奖🎉" if won else ""
        return f"抽奖: 共 {len(prizes)} 次（{text}）{note}"

    # ---------- 主流程 ----------
    def run(self) -> str:
        sess = self.login()
        lines = ["登录成功"]
        try:
            lines.append(self.checkin(sess))
        except SvyunError as e:
            lines.append(f"签到失败: {e}")
        try:
            lines.append(self.draw(sess))
        except SvyunError as e:
            lines.append(f"抽奖失败: {e}")
        return "\n".join(lines)


# ---------- 多账号 ----------
def split_entries(raw: str) -> list:
    """账号分隔符：换行或 &（密码内部可能出现冒号/空格，故不能拿它们当账号分隔符）。"""
    return [p.strip() for p in re.split(r"[\n\r&]+", raw or "") if p.strip()]


def parse_entry(entry: str):
    """把 用户名:密码（也认 ----、全角冒号、空格分隔）解析成二元组。"""
    for sep in ("----", "："):
        if sep in entry:
            user, _, pwd = entry.partition(sep)
            return user.strip(), pwd.strip()
    if ":" in entry:
        user, pwd = entry.split(":", 1)
        return user.strip(), pwd.strip()
    parts = entry.split()
    if len(parts) >= 2:
        return parts[0].strip(), " ".join(parts[1:]).strip()
    return entry.strip(), ""


def load_numbered(base_name: str) -> list:
    """单变量多账号 + 编号轮询（断号后连续 3 个缺失停止扫描），与其他脚本约定一致。"""
    entries = split_entries(os.getenv(base_name, ""))
    misses = 0
    for i in range(1, 100):
        got = split_entries(os.getenv(f"{base_name}_{i}", ""))
        if got:
            misses = 0
            entries.extend(got)
        else:
            misses += 1
            if misses >= 3:
                break
    seen, unique = set(), []
    for e in entries:
        if e not in seen:
            seen.add(e)
            unique.append(e)
    return unique


def build_accounts() -> list:
    accounts, bad = [], []
    for i, entry in enumerate(load_numbered("SVYUN_ACCOUNT"), 1):
        user, pwd = parse_entry(entry)
        if user and pwd:
            accounts.append(Account(i, user, pwd))
        else:
            bad.append(f"账号{i}")
    if bad:
        print(f"警告：{', '.join(bad)} 缺少用户名或密码，已跳过（格式应为 用户名:密码）", flush=True)
    return accounts


def random_delay():
    max_minutes = 10
    raw = os.getenv("SVYUN_DELAY", "").strip()
    if raw.isdigit():
        max_minutes = int(raw)
    if max_minutes <= 0:
        return
    seconds = random.randint(0, max_minutes * 60)
    print(f"随机延迟 {seconds // 60} 分 {seconds % 60} 秒后开始（SVYUN_DELAY={max_minutes} 分钟内随机）...",
          flush=True)
    time.sleep(seconds)


def main():
    if AES is None:
        print("缺少依赖 pycryptodome：青龙 → 依赖管理 → Python3 → 创建依赖 → 填 pycryptodome\n"
              "（或手动执行 pip3 install pycryptodome）")
        sys.exit(1)
    random_delay()
    accounts = build_accounts()
    if not accounts:
        print("未配置环境变量：SVYUN_ACCOUNT（格式 用户名:密码，多账号用换行或 & 分隔，"
              "详见脚本头部注释或仓库 README）")
        sys.exit(1)
    print(f"共 {len(accounts)} 个账号", flush=True)
    results = []
    for i, account in enumerate(accounts, 1):
        header = f"===== 账号 {i} 【{account.label}】====="
        print(header)
        try:
            msg = account.run()
        except SvyunError as e:
            msg = str(e)
        except Exception as e:  # noqa: BLE001 账号级失败隔离
            msg = f"执行异常: {e.__class__.__name__}: {e}"
        print(msg, "\n")
        results.append(f"{header}\n{msg}")
        if i < len(accounts):
            gap = random.randint(3, 15)
            print(f"等待 {gap}s 后处理下一个账号...", flush=True)
            time.sleep(gap)
    try:
        from notify import send  # type: ignore
        send("速维云签到", "\n\n".join(results))
    except Exception:
        pass


if __name__ == "__main__":
    main()
