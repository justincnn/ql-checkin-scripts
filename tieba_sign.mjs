#!/usr/bin/env node
/* 百度贴吧 纯签到 (青龙原生, 零依赖)
 * 用 BAIDU_COOKIE 环境变量里的 BDUSS
 * 依赖: 青龙 node 内置 http/https
 * cron: task tieba_sign.js
 */
const https = require('https');
const http = require('http');

const CK = process.env.BAIDU_COOKIE || '';
const BDUSS = (CK.match(/BDUSS=([^;]+)/) || [])[1];
if (!BDUSS) { console.log('⚠️ 未在 BAIDU_COOKIE 中找到 BDUSS,跳过'); process.exit(0); }

function req(method, host, path, form, cookie) {
  return new Promise((resolve, reject) => {
    const mod = host.includes(':') ? http : https;
    const data = form ? new URLSearchParams(form).toString() : null;
    const opts = {
      method, host, path: path,
      headers: {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36',
        'Cookie': cookie || '',
      },
    };
    if (data) {
      opts.headers['Content-Type'] = 'application/x-www-form-urlencoded; charset=UTF-8';
      opts.headers['Content-Length'] = Buffer.byteLength(data);
      opts.headers['X-Requested-With'] = 'XMLHttpRequest';
    }
    const rq = (host === 'tieba.baidu.com' ? https : https).request(opts, (res) => {
      let b = '';
      res.on('data', (c) => b += c);
      res.on('end', () => resolve(b));
    });
    rq.on('error', reject);
    if (data) rq.write(data);
    rq.end();
  });
}

(async () => {
  console.log('==== 贴吧签到开始 ====');
  const cookie = `BDUSS=${BDUSS};`;
  // 1. get tbs
  let r = await req('GET', 'tieba.baidu.com', '/dc/common/tbs?x=' + Date.now(), null, cookie);
  const tbs = (JSON.parse(r).tbs || '');
  if (!tbs) { console.log('❌ 获取 tbs 失败(可能登录失效)'); process.exit(1); }
  console.log('tbs ✅');

  // 2. 关注列表 (移动端接口, 直接返回 like_forum)
  r = await req('GET', 'tieba.baidu.com', '/mo/q/newmoindex?x=' + Date.now(), null, cookie);
  const like = (JSON.parse(r).data || {}).like_forum || [];
  const forums = like.filter(f => Number(f.is_sign || 0) === 0).map(f => f.forum_name || f.name).filter(Boolean);
  console.log(`关注吧数: ${like.length}, 待签到: ${forums.length}`);

  // 3. 逐个签到
  let ok = 0, already = 0, fail = 0;
  const detail = [];
  for (const name of forums) {
    let body;
    try {
      body = await req('POST', 'tieba.baidu.com', '/sign/add', { ie: 'utf-8', kw: name, tbs }, cookie);
      const d = JSON.parse(body);
      const code = Number(d.no ?? d.error_code ?? -1);
      if (code === 0 || d.error === '') { ok++; detail.push(`✅ ${name}`); }
      else if (code === 1101 || /已经签|已签到/.test(d.error || '')) { already++; detail.push(`  ⏭️ ${name} 已签`); }
      else { fail++; detail.push(`  ❌ ${name} (${d.error || code})`); }
    } catch (e) { fail++; detail.push(`  ❌ ${name} (网络)`); }
    await new Promise(s => setTimeout(s, 1200 + Math.random() * 1000));
  }

  console.log('\n==== 签到结果 ====');
  console.log(detail.join('\n'));
  console.log(`签到成功 ${ok} | 已签到 ${already} | 失败 ${fail} | 共 ${forums.length}`);
})();