'''
================================================================================
 酷狗概念版 (KugouYouth) —— 【任务脚本】金币任务自动领取   多账号版
 青龙面板 零依赖单文件脚本 (Python3 标准库，无需 pip install 任何东西)
================================================================================

 【环境变量】 KGGNB        多个账号用「换行」分隔。三种写法都支持：

  写法1（推荐，最简）:  备注#token#userid
      账号1#h573F0E37...#2247744797

  写法2（直接粘贴抓包 URL，脚本自动抠 token 和 userid）:
      账号1#https://gateway.kugou.com/yutc/youth/v1/user/info?userid=2247744797&...&token=h573F0E37...
      或  账号1#userid=2247744797&token=h573F0E37...

  写法3（老格式，兼容）:  备注#token#userid#mid#dfid#devid

  · token ：抓包里那个很长的大写 token（`token=` 后面那串）
  · userid：酷狗号
  · mid / dfid / devid **不用填**！脚本会用 token 推导出稳定的伪设备指纹。
    实测：mid/dfid 用随机值服务端照样认（返回的就是同一账号的余额），只有 userid 必须真实
    （userid 与 token 是绑定的，填错会报 11002 请先登录）。

 【协议要点】(逆向自 APK + 抓包，2026-09-10)
   真实接口域名 : https://gateway.kugou.com + 路径前缀 /yutc
   签名算法     : signature = MD5( SALT + "k1=v1k2=v2..."(参数名升序, 无分隔符拼接)
                                   + [POST 原始 JSON 体] + SALT )
                  SALT = NVPh5oo715z5DIWAeQlhMDsWXXQV4hwt

 【业务闭环】
   1) GET  /yutc/youth/v1/system/infos      -> 服务器时间 ts + 全部任务清单(含可领状态)
   2) GET  /yutc/youth/v1/task/check_risk   -> 签到前置风控 token
      POST /yutc/youth/v1/task/signon       -> 每日签到(领币)
   3) POST /yutc/youth/v1/task/submit       -> 提交任意任务领币
   4) 翻倍: 拿上一步的 double_code 再 submit 一次(不需要真看广告)

 【青龙定时建议】
   方式A 定时触发（推荐，稳）：*/8 * * * *     命令: task kugou_task.py
   方式B 常驻循环（一次启动一直跑）：命令: task kugou_task.py --loop
         —— 一直刷；空闲时按服务端 1105 冷却时间智能休眠（最长 25 分钟），
            不用反复起进程。想跑一会儿就停用 -loop-hours=6（跑 6 小时自动退出）。

 【命令行】
   python3 kugou_task.py                # 跑一轮（环境变量 KGGNB 里的所有账号）
   python3 kugou_task.py --loop         # 常驻循环，一直跑
   python3 kugou_task.py --loop --loop-hours=6   # 常驻 6 小时后退出
   python3 kugou_task.py --info         # 只看各账号余额/任务状态，不领任何东西
   python3 kugou_task.py --test         # 签名自检
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
CFG = {'skip_taskids': [2124], 'task_loop_caps': {9: 60, 45: 15, 2022: 4, 1105: 35, 1107: 10}, 'delay_min': 1.6, 'delay_max': 3.8, 'lock': True, 'wait_1105_max': 300, 'retry': 2, 'loop_idle_min': 60, 'loop_idle_max': 1500, 'loop_hours': 0, 'daily_once': True, 'state_file': ''}
ENV_NAME = 'KGGNB'
RE_MID = re.compile('^\\d{25,45}$')
RE_TOKEN_LIKE = re.compile('^[0-9A-Za-z]{64,}$')
RE_TOKEN_IN_TXT = re.compile('token=([0-9A-Za-z]{64,})')
RE_UID_IN_TXT = re.compile('userid=(\\d{4,12})')
DEFAULT_ACCOUNTS = [{'remark': '本地调试', 'token': 'h573F0E37B41CD4DCF414AB93FB2BC3EA6ADF5470F4B2F35680FD1D07A06668BD73F6AF4360B9C05C27773DBDEA86D12CF8E9A4F55C4E00AE7F7B4E09285336B6E0B3952E7652A78588246D215A91FF286DB53E0C5A503468C62751FF89D7D93AAFAA5C1CEC9D2E3E2527886C88F3AB94F98F0A74CE9D36AC62C341036D6BEA60E', 'userid': '2247744797', 'mid': '117031343721896449724314978024697721034', 'dfid': '1J9A8J1ZDCDy4OPWYB4Yj6c2', 'devid': '67fc1b03bbc00a9ae43634e474fa5ef49fa05ab729ff9a6e34a636a9aceaca2fbb968f6bf5abe75099773787a72217ab321390982e8a406ba0add51130ea212771e0985ea9cb166afd3bad5df44c35efe243777ec74adb88c53baf808c3c615d31bf7b8dba5319e3c2433a2f57e8dae8'}]
SALT = 'NVPh5oo715z5DIWAeQlhMDsWXXQV4hwt'
BASE = 'https://gateway.kugou.com'
PREFIX = '/yutc'
UA = 'Mozilla/5.0 (Linux; Android 14; 2203121C Build/UKQ1.231003.002; wv) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/118.0.0.0 Mobile Safari/537.36 KugouYouth'
TIMEOUT = 25
LOCK_FILE = os.path.join(tempfile.gettempdir(), 'kugou_task.lock')
LOCK_TTL = 3600
REQ_COUNT = 0
ACC = {}

class Stopped(Exception):
    '''外部请求中断（GUI 的「暂停/停止」按钮）'''

STOP_CHECK = None

def check_stop():
    if STOP_CHECK is not None and STOP_CHECK():
        raise Stopped('已手动暂停/停止')

def state_path():
    p = CFG.get('state_file')
    if p:
        return p
    if getattr(sys, 'frozen', False):
        base = os.path.dirname(os.path.abspath(sys.argv[0]))
    else:
        base = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, '.kugou_task_state.json')

def load_state():
    try:
        with open(state_path(), 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}

def save_state(st):
    try:
        with open(state_path(), 'w', encoding='utf-8') as f:
            json.dump(st, f, ensure_ascii=False, indent=1)
    except Exception as e:
        log(f'! 写入执行记录失败(不影响本次执行): {e}')
        return

def today_bj():
    t = time.gmtime(time.time() + 28800)
    return f'{t.tm_year:04d}-{t.tm_mon:02d}-{t.tm_mday:02d}'

def daily_done(uid):
    '''该账号今天是否已经执行过'''

    if not CFG.get('daily_once', True):
        return False
    return (load_state().get('last_run') or {}).get(str(uid)) == today_bj()

def daily_mark(uid):
    if not CFG.get('daily_once', True):
        return
    st = load_state()
    st.setdefault('last_run', {})[str(uid)] = today_bj()
    save_state(st)

def log(msg):
    check_stop()
    print(f'[{time.strftime("%H:%M:%S")}] {msg}', flush=True)

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
    '''解析环境变量。三种写法都支持：
      1) 最简：   备注#token#userid
      2) 老格式： 备注#token#userid#mid#dfid#devid
      3) 直接粘贴抓包 URL / 查询串（自动抠 token + userid）：
                 备注#https://gateway.kugou.com/yutc/...?userid=2247744797&...&token=h573...
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
        remark = parts[0]
        acc = {'remark': remark, 'token': '', 'userid': '', 'mid': '', 'dfid': '', 'devid': '', 'openid': '', 'nickname': ''}
        tk = RE_TOKEN_IN_TXT.search(line)
        uid = RE_UID_IN_TXT.search(line)
        if tk:
            # 现代格式：直接粘贴抓包 URL / 查询串，自动抠 token + userid
            acc['token'] = tk.group(1)
            acc['userid'] = uid.group(1) if uid else ''
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
                    if acc['userid']:
                        break
                    log(f'! 从这段里没解析出 userid（URL 里要带 userid=）：{line[:60]}...')
                    continue
        else:
            # 老格式：备注#token#userid#mid#dfid#devid（mid/dfid/devid 可省）
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
            else:
                acc['userid'] = rest[0] if rest else ''
                acc['openid'] = rest[1] if len(rest) > 1 else ''
                acc['nickname'] = rest[2] if len(rest) > 2 else ''
            if not acc['token'] or not acc['userid']:
                log(f'! 跳过缺少 token/userid 的行（格式：备注#token#userid）: {line[:40]}...')
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

def sleep_rand():
    time.sleep(random.uniform(CFG['delay_min'], CFG['delay_max']))

def make_sign(params, raw_body=None):
    keys = sorted(params.keys())
    joined = ''.join((f'{k}={params[k]}' for k in keys))
    if raw_body:
        joined += raw_body
    return hashlib.md5((SALT + joined + SALT).encode('utf-8')).hexdigest()

def api(path, params=None, data=None, method='GET', _retry=None):
    '''调用酷狗网关接口，返回解析后的 JSON(dict)；失败抛异常'''

    global REQ_COUNT
    if _retry is None:
        _retry = CFG['retry']
    p = {'appid': '3116', 'clientver': '11571', 'clienttime': str(int(time.time())), 'mid': ACC.get('mid', ''), 'uuid': '-', 'dfid': ACC.get('dfid', ''), 'srcappid': '2919', 'userid': ACC.get('userid', ''), 'token': ACC.get('token', '')}
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

def get_infos():
    return api('/youth/v1/system/infos')

def get_account():
    return api('/youth/v1/user/info')['data']

def get_sign_state():
    return api('/youth/v1/task/sign_state')['data']

def do_signon():
    '''每日签到：check_risk 拿风控 token -> signon 带今日 code'''

    st = get_sign_state()
    today = next((x for x in st.get('list', []) if x.get('today') == 1), None)
    if not today:
        return (False, '未找到今日签到项')
    if today.get('state') != 0:
        return False, f'今日已签到 (state={today.get("state")})'
    code = today.get('code')
    try:
        r = api('/youth/v1/task/check_risk', params={'taskid': 100001})
        if r.get('status') != 1:
            log(f'    ! 签到风控未通过: {r.get("error") or r.get("errcode")}')
    except Exception as e:
        log(f'    ! 签到风控请求异常(继续尝试签到): {e}')
    sleep_rand()
    r = api('/youth/v1/task/signon', params={'taskid': '100001', 'from': 'client'}, data={'code': code, 'source': 18}, method='POST')
    if r.get('status') == 1:
        return True, f'签到成功 (code={code})'
    return False, f'签到失败: {r.get("error") or r.get("errcode")}'

def submit_task(tid, show_coins):
    '''提交任务，返回 (本次总到账金币, 响应, 错误说明)'''

    body = {'appver': 1000, 'taskid': tid, 'show_coins': int(show_coins or 0), 'source': 18}
    r = api('/youth/v1/task/submit', data=body, method='POST')
    if r.get('status') != 1:
        return 0, r, r.get('error') or f'errcode={r.get("errcode")}'
    d = r.get('data') or {}
    aw = d.get('awards') or {}
    coins = int(aw.get('coins') or 0)
    dc = d.get('double_code')
    if dc:
        sleep_rand()
        try:
            body2 = dict(body)
            body2['double_code'] = dc
            body2['double_award_type'] = 2
            body2['use_uni_award'] = 1
            r2 = api('/youth/v1/task/submit', data=body2, method='POST')
            if r2.get('status') == 1:
                aw2 = (r2.get('data') or {}).get('awards') or {}
                coins += int(aw2.get('coins') or 0)
        except Exception as e:
            log(f'    · 翻倍请求异常(忽略): {e}')
    return coins, r, ''

def collect_tasks():
    '''刷一遍所有可领任务，返回本轮总金币'''

    infos = get_infos()
    if infos.get('status') != 1:
        raise RuntimeError(f'system/infos 异常: {infos.get("error") or infos.get("errcode")}')
    data = infos['data']
    srv_ts = int(data.get('ts') or time.time())
    tasks = data.get('tasks') or []
    log('  ── 每日签到 ──')
    try:
        ok, msg = do_signon()
        log(('    ✓ ' if ok else '    · ') + msg)
    except Exception as e:
        log(f'    ! 签到异常: {e}')
    log('  ── 扫描可领任务 ──')
    todo = []
    for t in tasks:
        pr = t.get('profile') or {}
        st = t.get('state') or {}
        tid = pr.get('taskid')
        if tid in CFG['skip_taskids']:
            continue
        if tid == 1:
            continue
        if not pr.get('open', 1):
            continue
        done = int(st.get('done_count') or 0)
        cap = int(st.get('max_done_count') or pr.get('max_done_count') or 0)
        if cap and done >= cap:
            continue
        wait_s = 0
        if tid == 1105:
            nat = st.get('next_award_time')
            if nat and int(nat) > srv_ts:
                wait_s = int(nat) - srv_ts
                if CFG['wait_1105_max'] and wait_s <= CFG['wait_1105_max']:
                    log(f'    · 定时领取金币(1105) 冷却剩 {wait_s} 秒，将就地等待后领取')
                else:
                    log(f'    · 定时领取金币(1105) 冷却中，还需 {wait_s // 60}分{wait_s % 60}秒，本轮跳过（可调 CFG[\'wait_1105_max\']）')
                    continue
        if tid == 1107:
            ch = (st.get('lottery') or {}).get('chances', 0)
            if ch and int(ch) <= 0:
                continue
        rest = cap - done if cap else 0
        todo.append((tid, pr.get('name') or '', int(pr.get('show_coins') or pr.get('award_coins') or 0), rest, wait_s))
        continue
    if not todo:
        log('    · 暂无可领任务')
        return 0
    total = 0
    for tid, name, show_coins, rest, wait_s in todo:
        limit = CFG['task_loop_caps'].get(tid, 0)
        if limit:
            rest = min(rest, limit) if rest else limit
        if not rest:
            rest = 1
        if wait_s:
            time.sleep(wait_s + 2)
        got = 0
        hit = None
        for i in range(rest):
            try:
                coins, r, err = submit_task(tid, show_coins)
            except Exception as e:
                log(f'    ! [{tid}]{name} 请求异常: {e}')
            if r.get('status') != 1:
                hit = err
                break
            got += coins
            log(f'    ✓ [{tid}]{name} #{i + 1} +{coins}')
            if i < rest - 1:
                sleep_rand()
        total += got
        if hit:
            log(f'    · [{tid}]{name} 结束（{hit}）')
        continue
    log(f'  ── 本轮任务累计 +{total} 金币 ──')
    return total

def show_info():
    acc = get_account()
    a = acc.get('account') or {}
    rate = acc.get('coin_rmb_rate') or 10000
    log(f'  余额 : {a.get("balance_coins")} 金币  (≈ {float(a.get("cash") or 0):.2f} 元, 汇率 {rate})')
    log(f'  累计 : {a.get("total_coins")}')
    st = get_sign_state()
    today = next((x for x in st.get('list', []) if x.get('today') == 1), None)
    log(f'  签到 : {"今日已签" if today and today.get("state") != 0 else "今日未签"}  (今日可领 {today.get("award_coins") if today else "-"} 金币)')
    infos = get_infos()
    ts = int(infos['data'].get('ts') or time.time())
    log('  任务 :')
    for t in infos['data'].get('tasks', []):
        pr, s = t.get('profile') or {}, t.get('state') or {}
        done = int(s.get('done_count') or 0)
        cap = int(s.get('max_done_count') or 0)
        flag = '可领' if not cap or done < cap else '已满'
        extra = ''
        if pr.get('taskid') == 1105 and s.get('next_award_time'):
            left = int(s['next_award_time']) - ts
            if left > 0:
                extra = f'  冷却剩 {left // 60}分{left % 60}秒'
        log(f'        [{pr.get("taskid"):>5}] {pr.get("name", "")[:12]:<12} {done}/{cap} {flag}{extra}')
        continue

def self_test():
    log('── 签名自检 ──')
    sample = 'appid=3116clientver=11571dfid=1J9A8J1ZDCDy4OPWYB4Yj6c2mid=117031343721896449724314978024697721034srcappid=2919token=Tuserid=2247744797uuid=-'
    p = {'appid': '3116', 'clientver': '11571', 'dfid': '1J9A8J1ZDCDy4OPWYB4Yj6c2', 'mid': '117031343721896449724314978024697721034', 'srcappid': '2919', 'token': 'T', 'userid': '2247744797', 'uuid': '-'}
    got = make_sign(p)
    expect = hashlib.md5((SALT + sample + SALT).encode()).hexdigest()
    if got != expect:
        log(f'  ✗ 签名实现异常: {got} != {expect}')
        return False
    log(f'  ✓ 签名算法一致: {got}')
    return True

def run_account(acc, mode, force=False):
    global ACC
    ACC = acc
    log(f'━━━ 账号 [{acc["remark"]}] userid={acc["userid"]} ━━━')
    if mode == 'info':
        show_info()
        return
    if daily_done(acc['userid']) and not force:
        log(f'  · 今天({today_bj()})已经执行过了，跳过（要重跑用 --force）')
        return
    if not self_test():
        raise RuntimeError('签名自检失败')
    try:
        acc0 = get_account()
        before = int((acc0.get('account') or {}).get('balance_coins') or 0)
        log(f'  起始金币: {before}')
    except Exception as e:
        log(f'  ! 读取余额失败(账号可能已失效): {e}')
        return
    try:
        collect_tasks()
        daily_mark(acc['userid'])
    except Exception as e:
        log(f'  ! 任务流程异常: {e}')
    try:
        acc1 = get_account()
        after = int((acc1.get('account') or {}).get('balance_coins') or 0)
        log(f'  结束金币: {after}   本次净增 +{after - before}  ({(after - before) / 10000.0:.2f} 元)')
    except Exception as e:
        log(f'  ! 读取余额失败: {e}')
        return

def seconds_to_next_bj_midnight(ts):
    '''距下一个北京时间 00:00 还有多少秒（任务每日重置）'''

    t = time.gmtime(int(ts) + 28800)
    sec = t.tm_hour * 3600 + t.tm_min * 60 + t.tm_sec
    return 86400 - sec + 60

def next_action_wait(infos):
    '''常驻模式下"下一轮该等多久"。
    以 1105(定时领取金币) 的冷却为准 —— 其他任务都是每日一次，每轮开头都会整体扫一遍，
    不需要为它们频繁唤醒（避免抽奖这类"看起来能领其实没机会"的任务把循环拖成每分钟一次）。
    '''

    data = infos.get('data') or {}
    srv = int(data.get('ts') or time.time())
    for t in data.get('tasks') or []:
        pr, st = t.get('profile') or {}, t.get('state') or {}
        if pr.get('taskid') != 1105:
            continue
        done = int(st.get('done_count') or 0)
        cap = int(st.get('max_done_count') or pr.get('max_done_count') or 0)
        if cap and done >= cap:
            return seconds_to_next_bj_midnight(srv)
        nat = st.get('next_award_time')
        if nat and int(nat) > srv:
            return int(nat) - srv
        return 0
    return seconds_to_next_bj_midnight(srv)

def run_forever(accounts, force=False):
    '''常驻循环：一直刷，直到 loop_hours 上限或 Ctrl+C'''

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
                run_account(acc, 'run', force=force)
            except Exception as e:
                log(f'! 账号[{acc.get("remark")}] 执行失败: {e}')
            time.sleep(random.uniform(2, 5))
        if deadline and time.time() >= deadline:
            log(f'已达到 loop_hours={hours:g} 上限，退出循环')
            return
        gap = CFG['loop_idle_min']
        try:
            ACC = accounts[0]
            globals()['ACC'] = ACC
            gap = next_action_wait(get_infos())
        except Exception as e:
            log(f'! 估算下一轮时间失败(用默认间隔): {e}')
        gap = int(max(CFG['loop_idle_min'], min(CFG['loop_idle_max'], gap + random.uniform(3, 20))))
        rem = deadline - time.time() if deadline else None
        if rem is not None:
            if rem <= 1:
                log(f'已达到 loop_hours={hours:g} 上限，退出循环')
                return
            gap = int(min(gap, rem))
        log(f'→ 休眠 {gap // 60} 分 {gap % 60} 秒后进入第 {rnd + 1} 轮')
        try:
            time.sleep(gap)
        except KeyboardInterrupt:
            log('收到中断信号，退出循环')
            return

def main():
    args = set(sys.argv[1:])
    mode = "info" if "--info" in args else "run"
    loop = "--loop" in args
    force = "--force" in args
    for a in args:
        if a.startswith("--loop-hours="):
            CFG["loop_hours"] = float(a.split("=", 1)[1])
        elif a == "--loop-hours":
            pass
    log("==============================================================")
    log(" 酷狗概念版 · 任务脚本 (多账号 · 环境变量 %s)" % ENV_NAME)
    log("==============================================================")

    accounts = parse_accounts()
    log(f"共 {len(accounts)} 个账号")
    if "--test" in args:
        sys.exit(0 if self_test() else 1)
    if mode == "info":
        for acc in accounts:
            try:
                run_account(acc, "info")
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
                    run_account(acc, "run", force=force)
                except Exception as e:
                    log(f"! 账号[{acc.get('remark')}] 执行失败: {e}")
                time.sleep(random.uniform(2, 5))
    except KeyboardInterrupt:
        log("收到中断信号，退出")
    finally:
        release_lock()
    log(f"总请求数: {REQ_COUNT}")
    log("全部账号完成。")

if __name__ == '__main__':
    main()
