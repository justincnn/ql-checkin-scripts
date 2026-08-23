// V2EX 每日签到 - 单次执行（青龙适配版）
// 所有出站都走 mihomo 代理(172.17.0.1:7890)，与其它脚本一致；无第三方依赖
import { readFileSync, existsSync } from 'node:fs';
import { execFileSync } from 'node:child_process';

const PROXY = `http://${process.env.TG_PROXY_HOST || '172.17.0.1'}:${process.env.TG_PROXY_PORT || '7890'}`;
const UA = process.env.V2EX_UA ||
  'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/131.0.0.0 Safari/537.36';

function getCookies() {
  if (process.env.V2EX_COOKIES) return process.env.V2EX_COOKIES;
  if (process.env.COOKIES) return process.env.COOKIES;
  if (existsSync('/ql/data/scripts/zaizai/v2ex.cookies'))
    return readFileSync('/ql/data/scripts/zaizai/v2ex.cookies', 'utf8').trim();
  return '';
}

// GET via curl(proxy); returns body
function get(url, cookie) {
  return execFileSync('curl', ['-sk', '--proxy', PROXY, '-H', `Cookie: ${cookie}`,
    '-H', `User-Agent: ${UA}`, url], { encoding: 'utf8', maxBuffer: 4 * 1024 * 1024 });
}
function getOnce(url, cookie) {
  return execFileSync('curl', ['-sk', '--proxy', PROXY, '-L', '-H', `Cookie: ${cookie}`,
    '-H', `User-Agent: ${UA}`, url], { encoding: 'utf8', maxBuffer: 4 * 1024 * 1024 });
}

async function tgPush(text) {
  const token = process.env.TG_BOT_TOKEN;
  const chat = process.env.TG_USER_ID;
  if (!token || !chat) { console.warn('(no TG_BOT_TOKEN/TG_USER_ID)'); return; }
  try {
    execFileSync('curl', ['-s', '--proxy', PROXY, '--data-urlencode', `chat_id=${chat}`,
      '--data-urlencode', `text=V2EX签到\n${text}`,
      `https://api.telegram.org/bot${token}/sendMessage`], { timeout: 20000 });
    console.log('TG push sent');
  } catch (e) { console.log('TG push fail:', e.message); }
}

async function main() {
  const cookies = getCookies();
  if (!cookies) { console.log('NO COOKIE: set V2EX_COOKIES or v2ex.cookies'); process.exit(1); }
  const log = [];
  const push = async (ok) => { console.log(log.join('\n')); await tgPush(log.join('\n')); };

  let index;
  try { index = getOnce('https://www.v2ex.com/', cookies); }
  catch (e) { console.log('ERR fetch index:', e.message); await tgPush('网络/代理失败: ' + e.message); process.exit(1); }
  if (index.includes('/signin')) { log.push('❌ 登录状态已失效'); await push(); return; }

  const mission = getOnce('https://www.v2ex.com/mission/daily', cookies);
  if (mission.includes('每日登录奖励已领取')) { log.push('✅ 每日登录奖励已领取（今日已完成）'); await push(); return; }

  const m = /redeem\?once=([^']*)/.exec(mission);
  if (!m) { log.push('❌ 无法获取 once 参数'); await push(); return; }
  getOnce(`https://www.v2ex.com/mission/daily/redeem?once=${m[1]}`, cookies);
  const final = getOnce('https://www.v2ex.com/mission/daily', cookies);
  if (final.includes('每日登录奖励已领取')) log.push('✅ 每日登录奖励领取成功');
  else log.push('❌ 领取失败，需检查');
  await push();
}

main().catch((e) => { console.error(e); process.exit(1); });