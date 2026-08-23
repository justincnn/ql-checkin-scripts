#!/usr/bin/env node
/* 百度贴吧 纯签到 (青龙原生, 零依赖)
 * 用 BAIDU_COOKIE 环境变量里的 BDUSS, 通过 curl 请求(自动跟随重定向)
 * cron: task tieba_sign.js
 */
const { execFile } = require('child_process');

const CK = process.env.BAIDU_COOKIE || '';
const BDUSS = (CK.match(/BDUSS=([^;]+)/) || [])[1];
if (!BDUSS) { console.log('⚠️ 未在 BAIDU_COOKIE 中找到 BDUSS,跳过'); process.exit(0); }

let PROXY = [];
if (process.env.HTTPS_PROXY || process.env.HTTP_PROXY) {
  PROXY = ['-x', process.env.HTTPS_PROXY || process.env.HTTP_PROXY];
}

function req(method, path, form, cookie) {
  return new Promise((resolve, reject) => {
    const args = ['-sS', '-m', '25', '-L'];
    if (PROXY.length) args.push(...PROXY);
    args.push('-A', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36');
    args.push('--compressed');
    const data = form ? new URLSearchParams(form).toString() : null;
    if (cookie) args.push('-H', `Cookie: ${cookie}`);
    if (method === 'POST') {
      args.push('-X', 'POST');
      args.push('-H', 'Content-Type: application/x-www-form-urlencoded; charset=UTF-8');
      args.push('-H', 'X-Requested-With: XMLHttpRequest');
      if (data) args.push('--data', data);
    }
    args.push('https://tieba.baidu.com' + path);
    execFile('curl', args, { timeout: 25000 }, (err, stdout) => {
      if (err) return reject(new Error('curl 失败: ' + (err.message || '')));
      resolve(stdout);
    });
  });
}

(async () => {
  console.log('==== 贴吧签到开始 ====');
  const cookie = `BDUSS=${BDUSS};`;
  // 1. get tbs
  let r = await req('GET', '/dc/common/tbs?x=' + Date.now(), null, cookie);
  const tbs = (JSON.parse(r).tbs || '');
  if (!tbs) { console.log('❌ 获取 tbs 失败(可能登录失效)'); process.exit(1); }
  console.log('tbs ✅');

  // 2. 关注列表
  r = await req('GET', '/mo/q/newmoindex?x=' + Date.now(), null, cookie);
  const like = (JSON.parse(r).data || {}).like_forum || [];
  const forums = like.filter(f => Number(f.is_sign || 0) === 0).map(f => f.forum_name || f.name).filter(Boolean);
  console.log(`关注吧数: ${like.length}, 待签到: ${forums.length}`);

  // 3. 逐个签到
  let ok = 0, already = 0, fail = 0;
  const detail = [];
  for (const name of forums) {
    try {
      const body = await req('POST', '/sign/add', { ie: 'utf-8', kw: name, tbs }, cookie);
      const d = JSON.parse(body);
      const code = Number(d.no ?? d.error_code ?? -1);
      if (code === 0 || d.error === '') { ok++; detail.push(`✅ ${name} 成功`); }
      else if (code === 1101 || /已经签|已签到/.test(d.error || '')) { already++; detail.push(`  ⏭️ ${name} 已签`); }
      else { fail++; detail.push(`  ❌ ${name} (${d.error || code})`); }
    } catch (e) { fail++; detail.push(`  ❌ ${name} (网络/解析)`); }
    await new Promise(s => setTimeout(s, 1500 + Math.random() * 1000));
  }

  console.log('\n==== 签到结果 ====');
  console.log(detail.join('\n'));
  console.log(`签到成功 ${ok} | 已签到 ${already} | 失败 ${fail} | 共 ${forums.length}`);
})();