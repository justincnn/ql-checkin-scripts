#!/usr/bin/env python3
# V2EX 每日签到 v3: requests.Session 保持 cookie 会话 + TG 推送
import os, re, sys, pathlib, requests, subprocess

PROXY = f"http://{os.environ.get('TG_PROXY_HOST','172.17.0.1')}:{os.environ.get('TG_PROXY_PORT','7890')}"
UA = os.environ.get('V2EX_UA', 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/131.0.0.0 Safari/537.36')

def get_cookies():
    if os.environ.get('V2EX_COOKIES'): return os.environ['V2EX_COOKIES']
    if os.environ.get('COOKIES'): return os.environ['COOKIES']
    p = pathlib.Path('/ql/data/scripts/zaizai/v2ex.cookies')
    if p.exists(): return p.read_text().strip()
    return ''

def build_session(cookies):
    s = requests.Session()
    s.proxies = {'http': PROXY, 'https': PROXY}
    s.headers['User-Agent'] = UA
    s.headers['Referer'] = 'https://www.v2ex.com/mission/daily'
    for kv in cookies.split(';'):
        if '=' in kv:
            k, v = kv.strip().split('=', 1)
            s.cookies.set(k, v, domain='.v2ex.com')
    return s

def tg_push(text):
    token = os.environ.get('TG_BOT_TOKEN')
    chat = os.environ.get('TG_USER_ID')
    if not token or not chat:
        print('(no TG_BOT_TOKEN/TG_USER_ID, skip push)'); return
    try:
        r = subprocess.run(['curl', '-s', '--proxy', PROXY, '--data-urlencode', f'chat_id={chat}',
                            '--data-urlencode', f'text=V2EX签到\n{text}',
                            f'https://api.telegram.org/bot{token}/sendMessage'],
                           capture_output=True, text=True, timeout=30)
        print('TG push:', 'ok' if r.returncode == 0 else f'fail rc={r.returncode}')
    except Exception as e:
        print('TG push fail:', e)

def main():
    cookies = get_cookies()
    if not cookies:
        print('NO COOKIE'); sys.exit(1)
    try:
        s = build_session(cookies)
        r = s.get('https://www.v2ex.com/mission/daily', timeout=20)
        if '/signin' in r.url:
            print('未登录需新cookie'); sys.exit(0)
        if '每日登录奖励已领取' in r.text:
            msg = '✅ 每日登录奖励已领取（今日已完成）'
            print(msg); tg_push(msg); sys.exit(0)
        m = re.search(r'redeem\?once=([0-9]+)', r.text)
        if not m:
            print('❌ 无法获取 once'); sys.exit(0)
        s.get(f'https://www.v2ex.com/mission/daily/redeem?once={m.group(1)}', allow_redirects=True)
        r3 = s.get('https://www.v2ex.com/mission/daily')
        msg = '✅ 每日登录奖励领取成功' if '每日登录奖励已领取' in r3.text else '❌ 领取失败，需检查'
        print(msg); tg_push(msg)
    except Exception as e:
        print('ERR:', e)

if __name__ == '__main__':
    main()