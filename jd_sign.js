// jd_sign.js - 入口脚本
// 功能：下载 NobyDa 京东多合一签到脚本 -> 注入多账号 Cookie -> 执行 -> 多通道推送结果
// 由 GitHub Actions 定时调用，本地零资源占用。
// 支持账号：JD_COOKIE / JD_COOKIE2 / JD_COOKIE3（第三个可选，可自行扩展）
// 支持推送：Server酱(PUSH_KEY)、Bark(BARK_KEY)

const fs = require('fs')
const http = require('http')
const https = require('https')

// ---------------- 配置读取 ----------------
const JD_COOKIE = process.env.JD_COOKIE || ''
const JD_COOKIE2 = process.env.JD_COOKIE2 || ''
const JD_COOKIE3 = process.env.JD_COOKIE3 || ''
const PUSH_KEY = (process.env.PUSH_KEY || '').trim()
const BARK_KEY = (process.env.BARK_KEY || '').trim()
const SCRIPT_URL =
  process.env.JD_SCRIPT_URL ||
  'https://raw.githubusercontent.com/NobyDa/Script/master/JD-DailyBonus/JD_DailyBonus.js'

// ---- 风控优化参数 ----
// 请求间随机延迟区间（单位毫秒），模拟真人；默认 5-15 秒随机
const RANDOM_DELAY = process.env.JD_DELAY || '5000-15000'
// 要禁用的接口代号（逗号分隔）。默认禁用已被京东新签名拦截、实测报错/参数异常的接口，
// 避免反复触发京东风控，只保留有真实响应的接口。如需全量签到设为空。
const JD_DISABLE = (process.env.JD_DISABLE || '').trim()
// 请求 UA。必须与抓取 Cookie 时的浏览器 UA 保持一致，否则京东会判定异常导致封号。
// 默认给一个桌面 Firefox 常用 UA（若你抓 Cookie 的口径不同，务必设置 JD_UA）
const JD_UA =
  process.env.JD_UA ||
  'Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:156.0) Gecko/20100101 Firefox/156.0'

const SCRIPT_PATH = './JD_DailyBonus.js'
const RESULT_PATH = './result.txt'
const ERROR_PATH = './error.txt'

// ---------------- 工具函数 ----------------
const downloadProxy = process.env.JD_DOWNLOAD_PROXY || ''
// ---------------- 工具函数 ----------------
function download(url, dest) {
  return new Promise((resolve, reject) => {
    const { execFile } = require('child_process')
    const args = ['-sL', '--max-time', '120']
    if (downloadProxy) args.push('-x', downloadProxy)
    args.push(url, '-o', dest)
    execFile('curl', args, { timeout: 130000 }, (err, stdout, stderr) => {
      if (err) {
        reject(new Error(`下载失败（curl ${err.code || err.message}）-> ${url}`))
        return
      }
      resolve(dest)
    })
  })
}


// 拼接多账号 Cookie 数组为 OtherKey 需要的 JSON 串
function buildOtherKey() {
  const raw = [JD_COOKIE, JD_COOKIE2, JD_COOKIE3].filter((c) => c && c.trim())
  const cookies = []
  // 支持用 & 或换行分隔多个账号
  for (const entry of raw) {
    for (const c of entry.split(/[&\n]/)) {
      const cookie = c.trim()
      if (!cookie) continue
      cookies.push({ cookie: cookie.endsWith(';') ? cookie : cookie + ';' })
    }
  }
  // 去重
  const seen = new Set()
  const unique = cookies.filter((o) => {
    if (seen.has(o.cookie)) return false
    seen.add(o.cookie)
    return true
  })
  return JSON.stringify(unique)
}

// 向 Server酱（微信）推送
function pushServerChan(title, desp) {
  return new Promise((resolve) => {
    const url = `https://sctapi.ftqq.com/${PUSH_KEY}.send`
    const data = `title=${encodeURIComponent(title)}&desp=${encodeURIComponent(desp)}`
    const req = https.request(url, { method: 'POST', headers: { 'Content-Type': 'application/x-www-form-urlencoded' } }, (res) => {
      let body = ''
      res.on('data', (d) => (body += d))
      res.on('end', () => {
        try {
          const json = JSON.parse(body)
          if (json.code === 0) console.log('[Server酱] 推送成功')
          else console.log('[Server酱] 推送失败:', json.message || body)
        } catch {
          console.log('[Server酱] 推送响应:', body)
        }
        resolve()
      })
    })
    req.on('error', (e) => {
      console.log('[Server酱] 推送异常:', e.message)
      resolve()
    })
    req.write(data)
    req.end()
  })
}

// 向 Bark（iOS 推送）推送
function pushBark(title, body) {
  return new Promise((resolve) => {
    const url = `https://api.day.app/${BARK_KEY}/${encodeURIComponent(title)}/${encodeURIComponent(body)}`
    const req = https.get(url, (res) => {
      let data = ''
      res.on('data', (d) => (data += d))
      res.on('end', () => {
        try {
          const json = JSON.parse(data)
          if (json.code === 200) console.log('[Bark] 推送成功')
          else console.log('[Bark] 推送失败:', json.message || data)
        } catch {
          console.log('[Bark] 推送响应:', data)
        }
        resolve()
      })
    })
    req.on('error', (e) => {
      console.log('[Bark] 推送异常:', e.message)
      resolve()
    })
  })
}

async function pushNotification(title, body) {
  const tasks = []
  if (PUSH_KEY) tasks.push(pushServerChan(title, body))
  if (BARK_KEY) tasks.push(pushBark(title, body))
  if (tasks.length === 0) {
    console.log('未配置推送渠道（PUSH_KEY / BARK_KEY），跳过推送。')
    return
  }
  await Promise.all(tasks)
}

function dateStr() {
  const d = new Date()
  const mm = String(d.getMonth() + 1).padStart(2, '0')
  const dd = String(d.getDate()).padStart(2, '0')
  return `${d.getFullYear()}.${mm}.${dd}`
}

// ---------------- 主流程 ----------------
async function main() {
  if (![JD_COOKIE, JD_COOKIE2, JD_COOKIE3].some((c) => c && c.trim())) {
    console.log('未配置 JD_COOKIE，请在 GitHub Secrets 中添加。')
    process.exit(1)
  }

  try {
    console.log('1. 下载签到脚本...')
    await download(SCRIPT_URL, SCRIPT_PATH)

    console.log('2. 注入账号 Cookie...')
    const otherKey = buildOtherKey()
    let content = fs.readFileSync(SCRIPT_PATH, 'utf8')
    // 替换 OtherKey 模板字符串（兼容旧版 Key 变量一并处理）
    if (/var OtherKey = ``/.test(content)) {
      content = content.replace('var OtherKey = ``;', `var OtherKey = \`${otherKey}\`;`)
    } else {
      // 如果脚本不含 OtherKey，降级注入 Key
      content = content.replace(/var Key = '';/, `var Key = '${otherKey}';`)
    }

    // 风控优化1：注入随机延迟（脚本原生支持 var stop，支持区间随机）
    if (RANDOM_DELAY && /var stop = '0';/.test(content)) {
      content = content.replace(/var stop = '0';/, `var stop = '${RANDOM_DELAY}';`)
      console.log(`   已注入随机延迟: ${RANDOM_DELAY} (毫秒)`)
    }

    // 风控优化2：禁用高风险/低收益接口，只保留核心京豆（JDBean）
    if (JD_DISABLE) {
      // 通过脚本持久化键 JD_DailyBonusDisables 注入禁用列表
      const nodeSet = './CookieSet.json'
      let set = {}
      try { set = JSON.parse(fs.readFileSync(nodeSet, 'utf8') || '{}') } catch (e) { set = {} }
      set.JD_DailyBonusDisables = JD_DISABLE
      fs.writeFileSync(nodeSet, JSON.stringify(set), 'utf8')
      console.log(`   已禁用接口: ${JD_DISABLE}`)
    }

    // 风控优化3：替换请求 UA 为抓 Cookie 时一致的桌面浏览器 UA（关键！）
    if (JD_UA) {
      const oldUA = /options\.headers\['User-Agent'\] = '([^']*)'/g
      if (oldUA.test(content)) {
        content = content.replace(oldUA, `options.headers['User-Agent'] = '${JD_UA}'`)
        console.log(`   已替换请求 UA`)
      } else {
        console.log('   未找到 UA 注入点，跳过')
      }
    }

    fs.writeFileSync(SCRIPT_PATH, content, 'utf8')
    console.log(`   已写入账号数: ${JSON.parse(otherKey).length}`)

    console.log('3. 执行签到脚本...')
    const { execFile } = require('child_process')
    try {
      const out = execFile('node', [SCRIPT_PATH], { timeout: 10 * 60 * 1000 })
      out.stdout.on('data', (d) => fs.appendFileSync(RESULT_PATH, d))
      out.stderr.on('data', (d) => fs.appendFileSync(RESULT_PATH, d))
      await new Promise((resolve, reject) => {
        out.on('close', (code) => (code === 0 ? resolve() : reject(new Error(`脚本退出码 ${code}`))))
        out.on('error', reject)
      })
    } catch (e) {
      console.log('   脚本执行异常:', e.message)
      fs.writeFileSync(ERROR_PATH, e.message, 'utf8')
    }

    console.log('4. 收集结果...')
    let desp = '未生成结果文件'
    if (fs.existsSync(RESULT_PATH)) desp = fs.readFileSync(RESULT_PATH, 'utf8').trim()
    const title = `京东签到 ${dateStr()}`

    console.log('5. 推送结果...')
    await pushNotification(title, desp)

    // 上传结果文件给 Actions 读取
    console.log('DONE. result_size=' + Buffer.byteLength(desp))
  } catch (err) {
    console.log('任务失败:', err.message)
    await pushNotification('京东签到 失败 ' + dateStr(), String(err.message))
    process.exit(1)
  }
}

main()
