'''
================================================================================
 酷狗概念版 (KugouYouth) —— 【提现脚本】金币自动提现   多账号版
 青龙面板 零依赖单文件脚本 (Python3 标准库，无需 pip install 任何东西)
================================================================================

 【环境变量】 KG        多个账号用「换行」分隔。三种写法都支持：

  写法1（推荐）:  备注#token#userid#openid#nickname
      账号1#h573F0E37...#2247744797#ofAeO6gX9q9_ewwdUfJBhlBjZJGg#花开富贵
      （nickname 可省 → 写 4 段也行）

  写法2（直接粘贴抓包 URL，自动抠 token / userid / openid）:
      账号1#https://gateway.kugou.com/yutc/...?userid=2247744797&...&token=h573F0E37...

  写法3（老格式，兼容）:  备注#token#userid#mid#dfid#devid#openid#nickname

  · token   ：抓包里那个很长的大写 token
  · userid  ：酷狗号
  · openid  ：微信授权拿到的 openid（抓包 get_wechat_auth / get_thirdinfo 响应里的 openid）
  · nickname：微信昵称（可留空，仅展示用）
  · mid / dfid / devid **不用填**！脚本会用 token 推导稳定的伪设备指纹。

  ⚠ 前提：该账号必须已在 App 内完成【微信授权提现】+【实名认证】，否则会返回
     20010 参数错误 - openid / 50019 未实名认证。

 【提现规则】
   · 开放时段：每天 0:00 / 8:00 / 12:00 / 16:00 / 20:00（服务端 times[] 下发）
   · 每天限提 1 次（max_apply_count=1）
   · 档位：首次提现专享 3000 金币(0.3元) → 5000 / 10000 / 50000 / 100000 金币
   · 汇率：10000 金币 = 1 元

 【脚本行为】
   1) 先查余额（只提"余额够"的档位，不够的标 ✗ 跳过）
   2) 若距开窗 ≤ pre_open_seconds(默认120秒)，就地毫秒级精准守候，到点（整点 + open_epsilon 提前量，默认0.2s）立即提现，尽量少延迟（提前2分钟准备）
   3) 余额够的档位按顺序逐个尝试（可申请/标签优先 → 金币从小到大），每成功一笔复查每日次数
   4) 一轮没提成就等几秒再抢一遍（档位常无库存），共 sweep_rounds 轮

 【协议要点】
   签名 : signature = MD5( SALT + "k1=v1k2=v2..."(参数名升序,无分隔符) + [POST原始JSON体] + SALT )
          SALT = NVPh5oo715z5DIWAeQlhMDsWXXQV4hwt
   提现 : POST https://gateway.kugou.com/yutc/youth/v1/withdraw/apply
          body {"openid","nickname","total_fee"(分),"coins","channel":3,"level_id"}
   成功 : {"status":1,"data":{"balance_coins":xxx,"tip":"提现成功，预计在1个工作日内到账"}}

 【青龙定时建议】
   方式A 定时触发（推荐，稳）——建两条：
        58,59 23,7,11,15,19 * * *    task kugou_withdraw.py     ← 提前2分钟启动守候
        0,1   0,8,12,16,20 * * *     task kugou_withdraw.py     ← 窗口内再补一刀
   方式B 常驻循环（一次启动一直跑）：
        task kugou_withdraw.py --loop
        —— 脚本算准每个开窗时刻提前守着，空闲时**不发任何请求**，一天只有 5 次唤醒。
           想跑一会儿就停用 --loop-hours=24（跑24小时自动退出）。

 【命令行】
   python3 kugou_withdraw.py                    # 检查余额/时段并提现（环境变量 KG）
   python3 kugou_withdraw.py --loop             # 常驻循环，每天到点自动提
   python3 kugou_withdraw.py --loop --loop-hours=24   # 常驻 24 小时后退出
   python3 kugou_withdraw.py --force            # 忽略时段限制，立即尝试
   python3 kugou_withdraw.py --info             # 只看各账号余额/档位/时段/今日次数，不提交
================================================================================
'''

import gzip
import hashlib
import json
import os
import random
import re
import string
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
CFG = {'withdraw_hours': [0, 8, 12, 16, 20], 'withdraw_window_min': 25, 'pre_open_seconds': 120, 'open_epsilon': 0.2, 'only_levels': [50000], 'min_coins': 0, 'check_balance': True, 'level_order': 'desc', 'prefer_tags': [], 'burst_count': 10, 'burst_gap': 0.1, 'allow_off_window': True, 'sweep_rounds': 3, 'stock_retry_gap': 3, 'apply_retry': 3, 'apply_retry_gap': 2, 'fatal_errcodes': [20010, 50019, 401, 11001], 'delay_min': 0.5, 'delay_max': 1.2, 'lock': True, 'retry': 2, 'loop_hours': 0, 'loop_grace': 20}
ENV_NAME = 'KG'
RE_MID = re.compile('^\\d{25,45}$')
RE_TOKEN_IN_TXT = re.compile('token=([0-9A-Za-z]{64,})')
RE_UID_IN_TXT = re.compile('userid=(\\d{4,12})')
RE_OPENID_IN_TXT = re.compile('openid=([0-9A-Za-z_\\-]{25,33})')
DEFAULT_ACCOUNTS = [{'remark': '本地调试', 'token': 'h573F0E37B41CD4DCF414AB93FB2BC3EA6ADF5470F4B2F35680FD1D07A06668BD73F6AF4360B9C05C27773DBDEA86D12CF8E9A4F55C4E00AE7F7B4E09285336B6E0B3952E7652A78588246D215A91FF286DB53E0C5A503468C62751FF89D7D93AAFAA5C1CEC9D2E3E2527886C88F3AB94F98F0A74CE9D36AC62C341036D6BEA60E', 'userid': '2247744797', 'mid': '117031343721896449724314978024697721034', 'dfid': '1J9A8J1ZDCDy4OPWYB4Yj6c2', 'devid': '67fc1b03bbc00a9ae43634e474fa5ef49fa05ab729ff9a6e34a636a9aceaca2fbb968f6bf5abe75099773787a72217ab321390982e8a406ba0add51130ea212771e0985ea9cb166afd3bad5df44c35efe243777ec74adb88c53baf808c3c615d31bf7b8dba5319e3c2433a2f57e8dae8', 'openid': 'ofAeO6gX9q9_ewwdUfJBhlBjZJGg', 'nickname': '花开富贵'}]
SALT = 'NVPh5oo715z5DIWAeQlhMDsWXXQV4hwt'
BASE = 'https://gateway.kugou.com'
PREFIX = '/yutc'
UA = 'Mozilla/5.0 (Linux; Android 14; 2203121C Build/UKQ1.231003.002; wv) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/118.0.0.0 Mobile Safari/537.36 KugouYouth'
TIMEOUT = 25
LOCK_FILE = os.path.join(tempfile.gettempdir(), 'kugou_withdraw.lock')
LOCK_TTL = 1800
REQ_COUNT = 0
ACC = {}

class Stopped(Exception):
    '''外部请求中断（GUI 的「暂停/停止」按钮）'''

STOP_CHECK = None

def check_stop():
    if STOP_CHECK is not None and STOP_CHECK():
        raise Stopped('已手动暂停/停止')

def log(msg):
    check_stop()
    print(f'[{time.strftime("%H:%M:%S")}] {msg}', flush=True)

def sleep_rand():
    time.sleep(random.uniform(CFG['delay_min'], CFG['delay_max']))


def sleep_until(target):
    '''高精度睡到目标时刻（time.time 秒）：粗睡 + 2ms 尾轮询 + perf_counter 忙等，
    消除计时器粒度与调度抖动，唤醒误差亚毫秒级'''
    while True:
        rem = target - time.time()
        if rem <= 0:
            return
        if rem > 0.05:
            time.sleep(rem - 0.05)
        elif rem > 0.002:
            time.sleep(0.002)
        else:
            # 最后 ~2ms：perf_counter 忙等，绕开 time.sleep 的计时粒度
            tpc = time.perf_counter()
            target_pc = tpc + rem
            while time.perf_counter() < target_pc:
                pass
            return


def open_target_seconds(srv, open_epsilon=0.2):
    '''给定服务器时钟 srv(秒)，返回距「下一整点开窗」的本地等待秒数（含 epsilon 提前量）。
    以服务器时钟为准，本地时钟漂移不影响开窗时刻的判断。'''
    return (int(float(srv)) // 3600 + 1) * 3600 - float(srv) + open_epsilon

def bj_hour_min(ts):
    '''UNIX 时间戳 -> 北京时间 (时, 分)，不依赖面板时区'''

    t = time.gmtime(int(ts) + 28800)
    return t.tm_hour, t.tm_min

def bj_hms(ts):
    '''UNIX 时间戳 -> 北京时间 (时, 分, 秒)'''

    t = time.gmtime(int(ts) + 28800)
    return t.tm_hour, t.tm_min, t.tm_sec

def derive_mid(token):
    '''用 token 推导稳定的伪设备 mid（39 位数字，形状与真实抓包一致）'''

    n = int(hashlib.md5(('kg-mid:' + token).encode()).hexdigest(), 16)
    return str(n % 10 ** 39).zfill(39)

def derive_dfid(token):
    '''用 token 推导稳定的伪设备 dfid（23 位字母数字）'''

    h = hashlib.sha256(('kg-dfid:' + token).encode()).hexdigest()
    pool = string.ascii_letters + string.digits
    return ''.join((pool[int(h[i * 2:i * 2 + 2], 16) % len(pool)] for i in range(23)))

def parse_accounts():
    '''解析环境变量。四种写法都支持：
      1) 最简：   备注#token#userid#openid#nickname
      2) 省 nickname：备注#token#userid#openid
      3) 老格式： 备注#token#userid#mid#dfid#devid#openid#nickname
      4) 直接粘贴抓包 URL / 查询串（自动抠 token / userid / openid）：
                 备注#https://...?userid=2247744797&...&token=h573...
    '''

    raw = (os.environ.get(ENV_NAME) or '').strip()
    raw = raw.replace('\\r', '').replace('\\n', '\n')
    accounts = []
    for line in raw.split('\n'):
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        parts = [x.strip() for x in line.split('#')]
        parts = [x for x in parts if x]
        if len(parts) < 1:
            continue
        acc = {'remark': parts[0], 'token': '', 'userid': '', 'mid': '', 'dfid': '', 'devid': '', 'openid': '', 'nickname': ''}
        tk = RE_TOKEN_IN_TXT.search(line)
        uid = RE_UID_IN_TXT.search(line)
        if tk:
            # 现代格式：直接粘贴抓包 URL / 查询串，自动抠 token / userid / openid
            acc['token'] = tk.group(1)
            acc['userid'] = uid.group(1) if uid else ''
            op = RE_OPENID_IN_TXT.search(line)
            if op:
                acc['openid'] = op.group(1)
            for f in parts[1:]:
                if not len(f) <= 6:
                    if '=' in f or '/' in f:
                        continue
                    if not acc['openid'] and f[0] == 'o':
                        if 25 <= len(f) <= 33:
                            acc['openid'] = f
                        elif not acc['nickname']:
                            acc['nickname'] = f
                else:
                    if acc['token']:
                        if acc['userid']:
                            break
                    log(f'! 从这段里没解析出 token/userid（URL 要带 userid= 和 token=）：{line[:60]}...')
                    continue
        else:
            # 老格式：备注#token#userid#mid#dfid#devid#openid#nickname（openid/nickname 可省）
            if len(parts) < 2:
                log(f'! 跳过格式错误的行: {line[:40]}...')
                continue
            acc['token'] = parts[1]
            rest = parts[2:]
            if len(rest) >= 2 and RE_MID.match(rest[1]):
                acc['userid'] = rest[0]
                acc['mid'] = rest[1]
                acc['dfid'] = rest[2] if len(rest) > 2 else ''
                acc['devid'] = rest[3] if len(rest) > 3 else ''
                acc['openid'] = rest[4] if len(rest) > 4 else ''
                acc['nickname'] = rest[5] if len(rest) > 5 else ''
            else:
                acc['userid'] = rest[0] if rest else ''
                acc['openid'] = rest[1] if len(rest) > 1 else ''
                acc['nickname'] = rest[2] if len(rest) > 2 else ''
            if not acc['userid']:
                log(f'! 跳过缺少 token/userid 的行（格式：备注#token#userid#openid#nickname）: {line[:40]}...')
                continue
        acc['mid'] = acc['mid'] or derive_mid(acc['token'])
        acc['dfid'] = acc['dfid'] or derive_dfid(acc['token'])
        acc['remark'] = acc['remark'] or acc['userid']
        accounts.append(acc)
    if not accounts:
        log(f'! 环境变量 {ENV_NAME} 未配置或为空，使用脚本内置调试账号（仅 1 个）')
        accounts = [dict(a) for a in DEFAULT_ACCOUNTS]
    return accounts

def acquire_lock():
    if not CFG.get('lock', True):
        return True
    try:
        if os.path.exists(LOCK_FILE):
            age = time.time() - os.path.getmtime(LOCK_FILE)
            if age < LOCK_TTL:
                return False
            log(f'发现过期锁({int(age)}秒)，强制接管')
    except OSError:
        pass
    try:
        with open(LOCK_FILE, 'w') as f:
            f.write(str(int(time.time())))
    except OSError:
        pass
    return True

def release_lock():
    if not CFG.get('lock', True):
        return
    try:
        os.remove(LOCK_FILE)
    except OSError:
        pass

def make_sign(params, raw_body=None):
    keys = sorted(params.keys())
    joined = ''.join((f'{k}={params[k]}' for k in keys))
    if raw_body:
        joined += raw_body
    return hashlib.md5((SALT + joined + SALT).encode('utf-8')).hexdigest()

def api(path, params=None, data=None, method='GET', _retry=None):
    global REQ_COUNT
    if _retry is None:
        _retry = CFG['retry']
    p = {'appid': '3116', 'clientver': '11571', 'clienttime': str(int(time.time())), 'mid': ACC.get('mid', ''), 'uuid': '-', 'dfid': ACC.get('dfid', ''), 'srcappid': '2919', 'userid': ACC.get('userid', ''), 'token': ACC.get('token', ''), 'from': 'client'}
    if params:
        p.update({k: str(v) for k, v in params.items()})
    raw = None
    if data is not None:
        raw = json.dumps(data, ensure_ascii=False, separators=(',', ':'))
    p['signature'] = make_sign(p, raw)
    url = BASE + PREFIX + path + '?' + urllib.parse.urlencode(p)
    req = urllib.request.Request(url, data=raw.encode('utf-8') if raw else None, method=method)
    req.add_header('User-Agent', UA)
    req.add_header('Content-Type', 'application/json; charset=utf-8')
    if ACC.get('devid'):
        req.add_header('KG-DEVID', ACC['devid'])
    req.add_header('KG-CLIENTTIMEMS', str(int(time.time() * 1000)))
    req.add_header('Accept-Encoding', 'gzip')
    last_err = None
    for attempt in range(_retry + 1):
        try:
            REQ_COUNT += 1
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                body = r.read()
                if r.headers.get('Content-Encoding') == 'gzip':
                    body = gzip.decompress(body)
            return json.loads(body.decode('utf-8', 'replace'))
        except urllib.error.HTTPError as e:
            b = e.read()
            try:
                if e.headers.get('Content-Encoding') == 'gzip':
                    b = gzip.decompress(b)
            except Exception:
                pass
            last_err = f'HTTP {e.code}: {b[:200].decode("utf-8", "replace")}'
        except Exception as e:
            last_err = f'{type(e).__name__}: {e}'
        if attempt < _retry:
            time.sleep(1.5 + attempt * 1.5)
    raise RuntimeError(f'{method} {path} 失败 -> {last_err}')

def get_levels():
    r = api('/youth/v1/withdraw/levels')
    if r.get('status') != 1:
        raise RuntimeError(f'withdraw/levels 异常: {r.get("error") or r.get("errcode")}')
    return r['data']

def get_account():
    return api('/youth/v1/user/info')['data']

def check_window(d):
    '''是否处于开放提现时段。返回 (within, msg, seconds_to_open)'''

    srv = int(d.get('server_time') or time.time())
    h, m, s = bj_hms(srv)
    times = d.get('times') or []
    hit = next((it for it in times if int(it.get('hour', -1)) == h), None)
    if not hit:
        nxt = None
        for hh in CFG['withdraw_hours']:
            if hh > h:
                nxt = (hh - h) * 3600 - m * 60 - s
                break
        if nxt is None:
            nxt = (24 - h + CFG['withdraw_hours'][0]) * 3600 - m * 60 - s
        return False, f'当前 {h:02d}:{m:02d}:{s:02d} 不在提现时段 {CFG["withdraw_hours"]}', nxt
    elapsed = (srv - int(hit['timestamp'])) // 60
    if elapsed > CFG['withdraw_window_min']:
        return False, f'当前 {h:02d}:{m:02d} 已超出发放窗口({CFG["withdraw_window_min"]}分钟)，下场 {CFG["withdraw_hours"]}', None
    return True, f'{h:02d}:{m:02d}:{s:02d} 处于提现窗口', 0

def get_balance():
    '''当前账号金币余额'''

    acc = get_account()
    return int((acc.get('account') or {}).get('balance_coins') or 0)

def level_queue(d, balance=None):
    '''返回本次要尝试提现的档位列表（全部"余额够"的档位都试，不只第一个）
    排序：可申请 > 无库存；同组内 标签命中(首次专享) > 金币(按 level_order)
    '''

    lst = [x for x in d.get('list') or [] if int(x.get('coins') or 0) >= CFG['min_coins']]
    only = CFG.get('only_levels') or []
    if only:
        lst = [x for x in lst if int(x.get('coins') or 0) in only]
    if balance is not None and CFG.get('check_balance', True):
        lst = [x for x in lst if int(x.get('coins') or 0) <= balance]
    reverse = str(CFG.get('level_order', 'asc')).lower() == 'desc'
    def rank(x):
        tag_pref = CFG['prefer_tags'].index(x.get('tag')) if x.get('tag') in CFG['prefer_tags'] else len(CFG['prefer_tags'])
        coins = int(x.get('coins') or 0)
        return 0 if x.get('can_apply') else 1, tag_pref, -coins if reverse else coins

    lst.sort(key=rank)
    return lst

def level_mark(x):
    if x.get('can_apply'):
        return '可申请'
    if x.get('no_stock'):
        return '无库存'
    return '不可申请'

def build_body(lvl):
    coins = int(lvl.get('coins') or 0)
    money = coins / 10000.0
    body = {'openid': ACC.get('openid', ''), 'nickname': ACC.get('nickname', ''), 'total_fee': int(round(100 * money)), 'coins': coins, 'channel': 3}
    if lvl.get('level_id') is not None:
        body['level_id'] = lvl['level_id']
    if int(lvl.get('type') or 0) == 1:
        body['coin_level'] = -1
    return body, coins, money

def apply_withdraw(body):
    return api('/youth/v1/withdraw/apply', data=body, method='POST')

def show_info():
    d = get_levels()
    acc = get_account()
    a = acc.get('account') or {}
    log(f'  余额 : {a.get("balance_coins")} 金币  (≈ {float(a.get("cash") or 0):.2f} 元)')
    within, why, nxt = check_window(d)
    log(f'  时段 : {why}' + (f'（距下一场约 {nxt // 60} 分钟）' if nxt else ''))
    log(f'  今日 : 已提 {d.get("day_apply_count")}/{d.get("max_apply_count")} 次')
    log('  档位 :（★=本次会优先尝试，顺序即提交顺序；✗=余额不够）')
    balance = None
    try:
        balance = get_balance()
    except Exception:
        pass
    queued = [int(x.get('coins') or 0) for x in level_queue(d, balance)] if balance is not None else []
    for x in d.get('list') or []:
        coins = int(x.get('coins') or 0)
        if balance is not None and coins > balance:
            mark = '✗'
        else:
            mark = '★' if queued and coins == queued[0] else ' '
        log(f'        {mark} {coins:>7} 金币 (≈ {coins / 10000:.2f} 元) {level_mark(x):<5} tag={x.get("tag", "")} level_id={x.get("level_id")}' + (f'  ← 余额差 {coins - balance}' if balance is not None and coins > balance else ''))
    log(f'  提交顺序: {" → ".join((str(c) for c in queued)) if queued else "(无)"}')
    log(f'  openid: {ACC.get("openid") or "(空)"} / nickname: {ACC.get("nickname") or "(空)"}')
    if not ACC.get('openid'):
        log('  ! 未配置 openid —— 提现会返回 20010 参数错误 - openid')

def do_withdraw_all(force=False):
    '''锁定档位（only_levels）+ 密集连发 burst_count 次，无论成败打满。
    allow_off_window=True（测试模式）：不在提现时段也直接尝试，跳过守候。
    守候模式下：到点直接用守候前的档位数据开抢，省一次刷新往返 → 第一枪几乎整点发出。'''

    try:
        d = get_levels()
    except Exception as e:
        log(f'  ! 档位查询失败: {e}')
        return
    balance = None
    if CFG.get('check_balance', True):
        try:
            balance = get_balance()
            log(f'  余额 : {balance} 金币（≈ {balance / 10000.0:.2f} 元）')
        except Exception as e:
            log(f'  ! 余额查询失败（将不按余额过滤）: {e}')
    within, why, nxt = check_window(d)
    log(f'  {why}')
    if not within and not force:
        if CFG.get('allow_off_window'):
            log('  · 已开启【允许不准点提现】（测试模式），跳过守候，立即尝试')
        else:
            pre = max(0, int(CFG.get('pre_open_seconds', 0)))
            if pre and nxt and 0 < nxt <= pre:
                srv = d.get('server_time') or time.time()
                eps = float(CFG.get('open_epsilon', 0.2))
                log(f'  · 距开窗 {nxt} 秒（≤{pre}s），精准守候（+{eps:g}s 提前量），到点立即连发…')
                sleep_until(time.time() + open_target_seconds(srv, eps))
                # 到点直接用守候前的档位数据开抢（不再刷新档位 → 第一枪省一个网络往返，几乎整点发出）
            else:
                log(f'  · 距下一场开窗还有 {nxt // 60} 分 {nxt % 60} 秒，本次先退出')
                return
    if balance is not None:
        cands = level_queue(d, balance)
        log(f'  · 余额 {balance}，可提档位 {len(cands)} 个' + ('：' + '、'.join((str(int(x.get('coins') or 0)) for x in cands)) if cands else '（余额不足任何档位，先去刷金币）'))
        if not cands:
            return
    else:
        cands = level_queue(d)
        log(f'  · 候选档位 {len(cands)} 个')
    lvl = cands[0]
    body, coins, money = build_body(lvl)
    burst = max(1, int(CFG.get('burst_count', 10)))
    gap = max(0.0, float(CFG.get('burst_gap', 0.1)))
    lock = CFG.get('only_levels') or []
    lock_txt = f'（锁定 {sorted(lock)} 金币）' if lock else ''
    log(f'  ── 密集连发 {burst} 次 × {coins} 金币(≈{money:.2f}元){lock_txt}，间隔 {gap:g}s，无论成败打满 ──')
    ok = 0
    for shot in range(1, burst + 1):
        try:
            res = apply_withdraw(body)
        except Exception as e:
            log(f'  {shot}/{burst} ! 请求异常: {e}（继续连发）')
            res = None
        if res and res.get('status') == 1:
            ok += 1
            rd = res.get('data') or {}
            log(f'  {shot}/{burst} ✓ 提现成功! {rd.get("tip", "")}（剩余 {rd.get("balance_coins")} 金币）')
        elif res:
            err = res.get('error') or f'errcode={res.get("errcode")}'
            log(f'  {shot}/{burst} ✗ {err}（errcode={res.get("errcode")}）')
            if res.get('errcode') in CFG['fatal_errcodes']:
                log('    · openid/实名/账号级错误，停止连发')
                break
        else:
            log(f'  {shot}/{burst} ✗ 无响应')
        if shot < burst:
            time.sleep(gap)
    if ok:
        log(f'  ── 连发 {burst} 次，成功 {ok} 笔，合计 {ok * coins} 金币（≈{ok * money:.2f} 元）──')
    else:
        log(f'  · 连发 {burst} 次全部未成功（接口繁忙 / 未到窗口 / 无库存 / 已达上限）')

def seconds_to_next_preopen():
    '''距下一个「开窗前 pre_open_seconds」还有多少秒。已过今天的窗口就取下一场'''

    now = int(time.time())
    pre = max(0, int(CFG.get('pre_open_seconds', 0)))
    t = time.gmtime(now + 28800)
    day0 = now - (t.tm_hour * 3600 + t.tm_min * 60 + t.tm_sec)
    best = None
    for h in CFG['withdraw_hours']:
        target = day0 + int(h) * 3600 - pre
        if target <= now:
            target += 86400
        if best is None or target < best:
            best = target
    return max(0, int(best) - now)

def run_forever(accounts, force=False):
    '''常驻循环：算准时间去守着开窗提现，空闲时几乎不发请求'''

    hours = float(CFG.get('loop_hours') or 0)
    deadline = time.time() + hours * 3600 if hours else None
    rnd = 0
    while True:
        rnd += 1
        log('==============================================================')
        log(f' 第 {rnd} 轮' + (f'（{hours:g} 小时后自动退出）' if deadline else '（常驻，Ctrl+C / 停任务 退出）'))
        log('==============================================================')
        for acc in accounts:
            try:
                run_account(acc, force=force, mode='run')
            except Exception as e:
                log(f'! 账号[{acc.get("remark")}] 执行失败: {e}')
            sleep_rand()
        if deadline and time.time() >= deadline:
            log(f'已达到 loop_hours={hours:g} 上限，退出循环')
            return
        wait = seconds_to_next_preopen() + CFG.get('loop_grace', 20)
        if wait <= 0:
            wait = 30
        rem = deadline - time.time() if deadline else None
        if rem is not None:
            if rem <= 1:
                log(f'已达到 loop_hours={hours:g} 上限，退出循环')
                return
            wait = int(min(wait, rem))
        log(f'→ 距下一次开窗守候时刻还有 {wait // 60} 分 {wait % 60} 秒，休眠等待（期间不发请求）')
        try:
            time.sleep(wait)
        except KeyboardInterrupt:
            log('收到中断信号，退出循环')
            return

def run_account(acc, force=False, mode='run'):
    global ACC
    ACC = acc
    log(f'━━━ 账号 [{acc["remark"]}] userid={acc["userid"]} ━━━')
    if mode == 'info':
        show_info()
        return
    if not ACC.get('openid'):
        log('  ! 未配置 openid，跳过（请先在 App 完成微信授权后把 openid 填进环境变量 KG）')
        return
    do_withdraw_all(force=force)

def main():
    args = set(sys.argv[1:])
    mode = "info" if "--info" in args else "run"
    force = "--force" in args
    loop = "--loop" in args
    for a in args:
        if a.startswith("--loop-hours="):
            CFG["loop_hours"] = float(a.split("=", 1)[1])
    log("==============================================================")
    log(" 酷狗概念版 · 提现脚本 (多账号 · 环境变量 %s)" % ENV_NAME)
    log("==============================================================")

    accounts = parse_accounts()
    log(f"共 {len(accounts)} 个账号")
    if mode == "info":
        for acc in accounts:
            try:
                run_account(acc, mode="info")
            except Exception as e:
                log(f"! 账号[{acc.get('remark')}] 查询失败: {e}")
        log(f"总请求数: {REQ_COUNT}")
        return

    elif not acquire_lock():
        log("已有实例在运行（命中运行锁），本次直接退出")
        sys.exit(0)
    try:
        if loop:
            run_forever(accounts, force=force)
        else:
            for acc in accounts:
                try:
                    run_account(acc, force=force, mode="run")
                except Exception as e:
                    log(f"! 账号[{acc.get('remark')}] 执行失败: {e}")
                sleep_rand()
    except KeyboardInterrupt:
        log("收到中断信号，退出")
    finally:
        release_lock()
    log(f"总请求数: {REQ_COUNT}")
    log("全部账号完成。")

if __name__ == '__main__':
    main()
