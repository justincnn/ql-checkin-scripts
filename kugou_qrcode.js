/**
 * 酷狗音乐二维码登录脚本（概念版 / lite）
 *
 * 功能说明：
 * - 使用 KuGouMusicApi 原生模块生成二维码
 * - 轮询扫码状态，登录成功后自动保存 token / userid
 * - 凭据保存到当前目录的 kg_token.json 文件
 * - 可选 Keepalive 机制（KEEPALIVE=true 时启用）
 *
 * 当前时间: 2026-06-06
 */

const crypto = require('crypto');
const fs = require('fs');
const path = require('path');
const qrcode = require('qrcode-terminal');

// 加载 KuGouMusicApi 程序化入口
const api = require('./api/main');

// 本地凭据文件路径
const TOKEN_FILE = path.join(__dirname, 'kg_token.json');

// ===================== 日志 =====================

const Logger = {
    info: (msg) => console.log(`\x1b[32m[INFO] ${new Date().toISOString().replace('T', ' ').substring(0, 19)} ${msg}\x1b[0m`),
    warn: (msg) => console.log(`\x1b[33m[WARN] ${new Date().toISOString().replace('T', ' ').substring(0, 19)} ${msg}\x1b[0m`),
    error: (msg) => console.log(`\x1b[31m[ERROR] ${new Date().toISOString().replace('T', ' ').substring(0, 19)} ${msg}\x1b[0m`)
};

// ===================== 本地凭据文件读写 =====================

/**
 * 从 kg_token.json 加载凭据
 * @returns {object|null}
 */
function loadTokenFile() {
    try {
        if (fs.existsSync(TOKEN_FILE)) {
            const raw = fs.readFileSync(TOKEN_FILE, 'utf-8');
            const data = JSON.parse(raw);
            Logger.info(`已加载本地凭据文件: ${TOKEN_FILE}`);
            return data;
        }
    } catch (err) {
        Logger.warn(`读取凭据文件失败: ${err.message}`);
    }
    return null;
}

/**
 * 保存凭据到 kg_token.json
 * @param {object} data - 凭据数据
 */
function saveTokenFile(data) {
    try {
        // 仅保留必要字段
        const toSave = {
            token: data.token || '',
            userid: data.userid || '',
            KUGOU_API_GUID: data.KUGOU_API_GUID || '',
            KUGOU_API_MID: data.KUGOU_API_MID || '',
            KUGOU_API_DEV: data.KUGOU_API_DEV || '',
            KUGOU_API_MAC: data.KUGOU_API_MAC || '',
            dfid: data.dfid || '-',
            t1: data.t1 || '',
            vip_type: data.vip_type || '0',
            vip_token: data.vip_token || '',
        };
        fs.writeFileSync(TOKEN_FILE, JSON.stringify(toSave, null, 2), 'utf-8');
        Logger.info(`凭据已保存到: ${TOKEN_FILE}`);
    } catch (err) {
        Logger.error(`保存凭据文件失败: ${err.message}`);
    }
}

// ===================== 设备标识生成 =====================

/**
 * 生成 GUID（UUID v4 格式）
 */
function generateGUID() {
    const e = () => ((65536 * (1 + Math.random())) | 0).toString(16).substring(1);
    return `${e()}${e()}-${e()}-${e()}-${e()}-${e()}${e()}${e()}`;
}

/**
 * MD5 哈希
 */
function md5(str) {
    return crypto.createHash('md5').update(str).digest('hex');
}

/**
 * 生成 10 位随机大写字符串
 */
function randomStr(len = 10) {
    const chars = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789';
    let result = '';
    for (let i = 0; i < len; i++) {
        result += chars[Math.floor(Math.random() * chars.length)];
    }
    return result;
}

/**
 * 根据 GUID 计算 MID（大整数）
 * 使用与 api/util/util.js 中 calculateMid 相同的逻辑
 */
function calculateMid(guid) {
    const digest = crypto.createHash('md5').update(guid).digest('hex');
    // 简化版大整数计算（使用 BigInt）
    let result = 0n;
    const base = 16n;
    const len = BigInt(digest.length);
    for (let i = 0; i < digest.length; i++) {
        const charVal = BigInt(parseInt(digest[i], 16));
        const power = base ** (len - 1n - BigInt(i));
        result += charVal * power;
    }
    return result.toString();
}

/**
 * 构建完整的 Cookie 对象（模拟概念版客户端环境）
 */
function buildCookieObject(overrides = {}) {
    // 优先从本地文件加载已有设备标识
    const existing = loadTokenFile();
    const guid = md5(existing?.KUGOU_API_GUID || generateGUID());
    const mid = calculateMid(guid);
    const dev = (existing?.KUGOU_API_DEV || randomStr(10)).toUpperCase();
    const mac = (existing?.KUGOU_API_MAC || '02:00:00:00:00:00').toUpperCase();
    const dfid = existing?.dfid || '-';

    return {
        KUGOU_API_PLATFORM: 'lite',
        KUGOU_API_GUID: guid,
        KUGOU_API_MID: mid,
        KUGOU_API_DEV: dev,
        KUGOU_API_MAC: mac,
        dfid,
        ...overrides,
    };
}

// ===================== 凭据持久化 =====================

/**
 * 保存登录凭据
 * - 写入 kg_token.json 本地文件
 * - 同时写入 process.env 供当前进程使用
 */
async function saveCredentials(token, userid, cookieObj) {
    // 写入当前进程环境变量（方便同一进程内后续调用）
    process.env.KUGOU_TOKEN = token;
    process.env.KUGOU_USERID = String(userid);

    // 保存到本地文件
    saveTokenFile({ ...cookieObj, token, userid });
}

// ===================== 二维码登录流程 =====================

/**
 * 获取二维码 key
 */
async function getQRKey(cookieBase) {
    try {
        const result = await api.login_qr_key({
            type: 'app',
            cookie: cookieBase,
        });

        if (result.status === 200 && result.body?.data?.qrcode) {
            const qrKey = result.body.data.qrcode;
            Logger.info(`获取二维码 Key 成功: ${qrKey.substring(0, 12)}...`);
            return qrKey;
        }
        Logger.error(`获取二维码 Key 失败: ${JSON.stringify(result.body)}`);
        return null;
    } catch (err) {
        Logger.error(`获取二维码 Key 异常: ${err.body?.msg || err.message}`);
        return null;
    }
}

/**
 * 生成并显示二维码（终端 + Data URL 用于浏览器打开）
 */
async function showQRCode(qrKey) {
    try {
        const result = await api.login_qr_create({
            key: qrKey,
            qrimg: true,
        });

        if (result.status === 200 && result.body?.data?.url) {
            const qrUrl = result.body.data.url;
            const base64 = result.body.data.base64 || '';

            // 终端显示二维码
            qrcode.generate(qrUrl, { small: true }, (code) => {
                Logger.info('请打开酷狗音乐 APP 扫描以下二维码登录：');
                console.log(code);
            });

            // Data URL：复制到浏览器地址栏即可显示清晰二维码
            if (base64 && base64.startsWith('data:image')) {
                Logger.info('──────────────────────────────────────');
                Logger.info('📄 复制下方 Data URL 到浏览器地址栏可查看清晰二维码：');
                Logger.info('\x1b[33m（请全选复制下面这行↓）\x1b[0m');
                console.log(base64);
                Logger.info('──────────────────────────────────────');
            }

            return true;
        }
        Logger.error('生成二维码失败');
        return false;
    } catch (err) {
        Logger.error(`生成二维码异常: ${err.body?.msg || err.message}`);
        return false;
    }
}

/**
 * 轮询检查扫码状态
 * @returns {Promise<{token: string, userid: string, cookies: string[], body: object} | null>}
 */
async function checkQRStatus(qrKey, cookieBase) {
    try {
        const result = await api.login_qr_check({
            key: qrKey,
            cookie: cookieBase,
        });

        const body = result.body || {};
        const data = body.data || {};

        switch (data.status) {
            case 4:
                // 扫码授权成功
                const token = data.token || '';
                const userid = data.userid || '';
                return {
                    token,
                    userid,
                    cookies: result.cookie || [],
                    body: data,
                };
            case 2:
                // 已扫码，等待确认
                Logger.info('已扫码，请在手机上确认登录...');
                return null;
            case 1:
                // 等待扫码
                return null;
            case 0:
            default:
                // 二维码过期或无效
                Logger.warn('二维码已过期，请重新获取');
                return { expired: true };
        }
    } catch (err) {
        Logger.warn(`轮询扫码状态异常: ${err.body?.msg || err.message}`);
        return null;
    }
}

/**
 * 休眠
 */
function sleep(ms) {
    return new Promise((resolve) => setTimeout(resolve, ms));
}

// ===================== 设备注册 & Token 刷新 =====================

/**
 * 注册设备（必须先注册才能刷新 Token）
 */
async function registerDevice(cookieObj) {
    try {
        Logger.info('正在注册设备...');
        const result = await api.register_dev({
            token: cookieObj.token,
            userid: cookieObj.userid,
            cookie: cookieObj,
        });

        if (result.status === 200 && result.body?.status === 1) {
            const newCookie = { ...cookieObj };
            if (result.cookie) {
                for (const c of result.cookie) {
                    const [k, v] = c.split('=');
                    if (k && v !== undefined) newCookie[k] = v;
                }
            }
            // 注册成功后可能返回 dfid
            if (result.body?.data?.dfid) {
                newCookie.dfid = result.body.data.dfid;
            }
            Logger.info('设备注册成功');
            return { cookie: newCookie };
        }

        Logger.warn(`设备注册返回异常: ${JSON.stringify(result.body)}`);
        return null;
    } catch (err) {
        const errMsg = err.body?.msg || err.body?.error_msg || err.body?.message
            || (err.body ? JSON.stringify(err.body).substring(0, 200) : '')
            || err.message || String(err);
        Logger.error(`设备注册异常: ${errMsg}`);
        return null;
    }
}

/**
 * 刷新登录 Token
 */
async function refreshToken(cookieObj) {
    try {
        const result = await api.login_token({
            token: cookieObj.token,
            userid: cookieObj.userid,
            cookie: cookieObj,
        });

        if (result.status === 200 && result.body?.status === 1) {
            const data = result.body.data || {};
            const newToken = data.token || '';
            const newUserId = data.userid || cookieObj.userid;

            // 合并新的 cookie
            const newCookie = { ...cookieObj };
            if (result.cookie) {
                for (const c of result.cookie) {
                    const [k, v] = c.split('=');
                    if (k && v !== undefined) newCookie[k] = v;
                }
            }

            Logger.info('Token 刷新成功');
            return { token: newToken, userid: newUserId, cookie: newCookie };
        }

        Logger.warn(`Token 刷新返回异常: ${JSON.stringify(result.body)}`);
        return null;
    } catch (err) {
        // 提取真实错误信息
        const errMsg = err.body?.msg || err.body?.error_msg || err.body?.message
            || (err.body ? JSON.stringify(err.body).substring(0, 200) : '')
            || err.message || String(err);
        Logger.error(`Token 刷新异常: ${errMsg}`);
        return null;
    }
}

/**
 * 启动 Keepalive 定时刷新
 */
function startKeepalive(cookieObj) {
    // 每 1.5 小时刷新一次
    const intervalMs = 90 * 60 * 1000;

    const timer = setInterval(async () => {
        Logger.info('Keepalive: 尝试刷新 Token...');
        const result = await refreshToken(cookieObj);
        if (result) {
            // 更新 cookieObj 引用
            Object.assign(cookieObj, result.cookie);
            await saveCredentials(result.token, result.userid, result.cookie);
        } else {
            Logger.error('Keepalive: Token 刷新失败，请重新扫码登录');
            clearInterval(timer);
        }
    }, intervalMs);

    Logger.info(`Keepalive 已启动，每 ${intervalMs / 60000} 分钟刷新一次 Token`);
    return timer;
}

// ===================== 主流程 =====================

async function main() {
    Logger.info('========== 酷狗概念版二维码登录 ==========');

    // 1. 构建基础设备 Cookie（QR 登录返回的是标准版 Token，不设 lite）
    let cookieBase = buildCookieObject();
    Logger.info(`设备标识: GUID=${cookieBase.KUGOU_API_GUID.substring(0, 8)}... MID=${cookieBase.KUGOU_API_MID}`);

    // 2. 获取二维码 Key
    const qrKey = await getQRKey(cookieBase);
    if (!qrKey) {
        Logger.error('无法获取二维码，请检查网络后重试');
        return;
    }

    // 3. 显示二维码
    const shown = await showQRCode(qrKey);
    if (!shown) {
        Logger.error('无法生成二维码');
        return;
    }

    // 4. 轮询扫码状态（最长等待 5 分钟）
    Logger.info('等待扫码登录（超时时间: 5 分钟）...');
    const startTime = Date.now();
    const timeout = 5 * 60 * 1000;

    let loginResult = null;
    while (Date.now() - startTime < timeout) {
        const status = await checkQRStatus(qrKey, cookieBase);

        if (status?.expired) {
            Logger.error('二维码已过期');
            return;
        }

        if (status?.token && status?.userid) {
            loginResult = status;
            break;
        }

        await sleep(3000);
    }

    if (!loginResult) {
        Logger.error('扫码登录超时，请重试');
        return;
    }

    // 5. 登录成功，构建完整 Cookie
    Logger.info('✅ 扫码登录成功！');
    const { token, userid, cookies } = loginResult;

    cookieBase.token = token;
    cookieBase.userid = String(userid);

    // 解析返回的 cookies 并合并
    for (const c of cookies) {
        const [k, v] = c.split('=');
        if (k && v !== undefined) cookieBase[k] = v;
    }

    Logger.info(`用户 ID: ${userid}`);
    Logger.info(`Token: ${token.substring(0, 16)}...`);

    // 6. 保存凭据
    await saveCredentials(token, userid, cookieBase);

    // 7. 注册设备 + 刷新 Token（获取更完整的凭据，包括 t1/dfid 等参数）
    Logger.info('正在注册设备并刷新凭据...');
    const registered = await registerDevice(cookieBase);
    if (registered) {
        Object.assign(cookieBase, registered.cookie);
        await saveCredentials(cookieBase.token, cookieBase.userid, cookieBase);

        // 设备注册成功后刷新 Token
        const refreshed = await refreshToken(cookieBase);
        if (refreshed) {
            Object.assign(cookieBase, refreshed.cookie);
            await saveCredentials(refreshed.token, refreshed.userid, refreshed.cookie);
        } else {
            Logger.warn('Token 刷新未成功，但二维码登录凭据已保存，main.js 运行时会自动重试刷新');
        }
    } else {
        Logger.warn('设备注册未成功，但二维码登录凭据已保存，main.js 运行时会自动重试');
    }

    // 8. 启动 Keepalive
    if (process.env.KEEPALIVE === 'true') {
        startKeepalive(cookieBase);
    } else {
        Logger.info('Keepalive 未启用（设置环境变量 KEEPALIVE=true 可开启自动续期）');
    }

    Logger.info('========== 登录流程完成 ==========');
    Logger.info(`凭据已保存到: ${TOKEN_FILE}`);
    Logger.info('可运行 main.js 进行每日签到');

    // 显式退出进程（避免 HTTP keep-alive 等残留连接阻止退出）
    process.exit(0);
}

// 运行
if (require.main === module) {
    main().catch(err => {
        Logger.error(`脚本执行异常: ${err.message}`);
        console.error(err);
        process.exit(1);
    });
}

module.exports = { buildCookieObject, saveCredentials, refreshToken, loadTokenFile, saveTokenFile, Logger };