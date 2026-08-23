/**
 * 酷狗概念版每日签到与听歌领取 VIP 脚本
 *
 * 功能说明：
 * - 从 kg_token.json 读取登录凭证
 * - 刷新 Token 确保凭据有效
 * - 模拟听歌行为（youth_listen_song）
 * - 领取每日 VIP 体验卡（youth_day_vip）
 * - 查询当前 VIP 状态
 * - 凭据更新后自动写回 kg_token.json
 *
 * 当前时间: 2026-06-06
 */

const fs = require('fs');
const path = require('path');
const crypto = require('crypto');

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

function loadTokenFile() {
    try {
        if (fs.existsSync(TOKEN_FILE)) {
            const raw = fs.readFileSync(TOKEN_FILE, 'utf-8');
            return JSON.parse(raw);
        }
    } catch (err) {
        Logger.error(`读取凭据文件失败: ${err.message}`);
    }
    return null;
}

function saveTokenFile(data) {
    try {
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
    } catch (err) {
        Logger.error(`保存凭据文件失败: ${err.message}`);
    }
}

// ===================== Cookie 构建 =====================

function buildCookieObject() {
    const data = loadTokenFile() || {};
    const guid = data.KUGOU_API_GUID || '';
    const dev = (data.KUGOU_API_DEV || 'DEV' + Math.random().toString(36).substring(2, 10)).toUpperCase();
    const mac = (data.KUGOU_API_MAC || '02:00:00:00:00:00').toUpperCase();
    const dfid = data.dfid || '-';
    const t1 = data.t1 || '';
    const token = data.token || '';
    const userid = data.userid || '0';

    // 从 GUID 计算 MID
    let mid = '';
    if (guid) {
        try {
            const digest = crypto.createHash('md5').update(guid).digest('hex');
            let result = 0n;
            const base = 16n;
            const len = BigInt(digest.length);
            for (let i = 0; i < digest.length; i++) {
                const charVal = BigInt(parseInt(digest[i], 16));
                const power = base ** (len - 1n - BigInt(i));
                result += charVal * power;
            }
            mid = result.toString();
        } catch { /* ignore */ }
    }

    const cookie = {
        KUGOU_API_PLATFORM: 'lite',
        KUGOU_API_GUID: guid,
        KUGOU_API_MID: mid,
        KUGOU_API_DEV: dev,
        KUGOU_API_MAC: mac,
        dfid,
        token,
        userid,
        t1,
        vip_type: '0',
        vip_token: '',
    };

    // 清理空值
    for (const [k, v] of Object.entries(cookie)) {
        if (v === '' || v === undefined) delete cookie[k];
    }

    return cookie;
}

// ===================== 业务流程 =====================

/**
 * 提取错误信息（兼容 API createRequest 的 reject 格式）
 */
function extractErrMsg(err) {
    if (!err) return '未知错误';
    // API reject 的是 {status:502, body: {status, error_code, error_msg, msg, data,...}}
    if (err.body && typeof err.body === 'object') {
        const b = err.body;
        // msg 可能是 axios Error 对象，跳过
        if (typeof b.msg === 'string' && b.msg) return b.msg;
        if (typeof b.error_msg === 'string' && b.error_msg) return b.error_msg;
        if (b.error_code !== undefined) {
            const parts = [`error_code=${b.error_code}`];
            if (b.status !== undefined) parts.unshift(`status=${b.status}`);
            return parts.join(' ');
        }
        return JSON.stringify(b);
    }
    if (err.message) return err.message;
    return String(err);
}

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
            if (result.body?.data?.dfid) {
                newCookie.dfid = result.body.data.dfid;
            }
            Logger.info('设备注册成功');
            return { cookie: newCookie };
        }

        Logger.warn(`设备注册返回异常: ${JSON.stringify(result.body)}`);
        return null;
    } catch (err) {
        Logger.error(`设备注册异常: ${extractErrMsg(err)}`);
        return null;
    }
}

/**
 * 刷新 Token
 */
async function refreshToken(cookieObj) {
    try {
        Logger.info('正在刷新登录凭据...');
        const result = await api.login_token({
            token: cookieObj.token,
            userid: cookieObj.userid,
            cookie: cookieObj,
        });

        if (result.status === 200 && result.body?.status === 1) {
            const data = result.body.data || {};
            const newToken = data.token || '';
            const newUserId = data.userid || cookieObj.userid;

            // 合并新的 cookie 字段
            if (result.cookie) {
                for (const c of result.cookie) {
                    const [k, v] = c.split('=');
                    if (k && v !== undefined) cookieObj[k] = v;
                }
            }

            Logger.info('凭据刷新成功');
            return { token: newToken, userid: newUserId, cookie: cookieObj };
        }

        Logger.warn(`凭据刷新返回异常: status=${result.body?.status}`);
        return null;
    } catch (err) {
        Logger.error(`凭据刷新异常: ${extractErrMsg(err)}`);
        return null;
    }
}

/**
 * 步骤1：上报听歌行为（模拟听歌）
 * @returns {Promise<{success: boolean, msg: string}>}
 */
async function reportListenSong(cookieObj) {
    try {
        Logger.info('[听歌] 正在上报听歌行为...');
        const result = await api.youth_listen_song({
            mixsongid: 666075191,
            cookie: cookieObj,
        });

        if (result.status === 200) {
            const body = result.body || {};
            if (body.status === 1 || body.error_code === 0) {
                Logger.info(`[听歌] ✅ 听歌行为上报成功`);
                return { success: true, msg: '听歌行为上报成功' };
            }
            // error_code=130012 表示今日已领取，也算成功
            if (body.error_code === 130012) {
                Logger.info(`[听歌] ℹ️ 今日听歌奖励已领取`);
                return { success: true, msg: '今日已领取听歌奖励' };
            }
            Logger.warn(`[听歌] 上报结果: ${body.error_msg || `error_code=${body.error_code}`}`);
            return { success: false, msg: body.error_msg || `error_code=${body.error_code}` };
        }

        Logger.warn(`[听歌] 上报失败: HTTP ${result.status}`);
        return { success: false, msg: `HTTP ${result.status}` };
    } catch (err) {
        // API reject 格式: {status:502, body:{status:0, error_code:xxx}}
        if (err.body?.error_code === 130012) {
            Logger.info(`[听歌] ℹ️ 今日听歌奖励已领取`);
            return { success: true, msg: '今日已领取听歌奖励' };
        }
        Logger.error(`[听歌] 异常: ${extractErrMsg(err)}`);
        return { success: false, msg: extractErrMsg(err) };
    }
}

/**
 * 步骤2：广告播放上报（领取 VIP）
 * @returns {Promise<{success: boolean, msg: string}>}
 */
async function reportAdVip(cookieObj) {
    try {
        const result = await api.youth_vip({
            cookie: cookieObj,
        });

        if (result.status === 200) {
            const body = result.body || {};
            if (body.status === 1 || body.error_code === 0) {
                return { success: true, msg: '广告上报成功' };
            }

            const msg = body.error_msg || body.msg || `error_code=${body.error_code}`;
            // 今天次数已用完也算成功
            if (msg.includes('次数已用光') || msg.includes('已用完') || msg.includes('30002')) {
                Logger.info(`[广告领VIP] ℹ️ ${msg}`);
                return { success: true, msg: '今日已完成' };
            }
            Logger.warn(`[广告领VIP] 结果: ${msg}`);
            return { success: false, msg };
        }

        Logger.warn(`[广告领VIP] 请求失败: HTTP ${result.status}`);
        return { success: false, msg: `HTTP ${result.status}` };
    } catch (err) {
        // API reject 格式: {status:502, body:{status:0, error_code:xxx, error_msg:...}}
        const errMsg = extractErrMsg(err);
        if (errMsg.includes('次数已用光') || errMsg.includes('已用完') || errMsg.includes('30002')) {
            Logger.info(`[广告领VIP] ℹ️ ${errMsg}`);
            return { success: true, msg: '今日已完成' };
        }
        Logger.error(`[广告领VIP] 异常: ${errMsg}`);
        return { success: false, msg: errMsg };
    }
}

/**
 * 步骤3：查询 VIP 状态
 * @returns {Promise<{success: boolean, isVip: boolean, vipType: number, endTime: string, msg: string}>}
 */
async function checkVIPStatus(cookieObj) {
    try {
        Logger.info('[VIP状态] 正在查询 VIP 状态...');
        const result = await api.user_vip_detail({
            cookie: cookieObj,
        });

        if (result.status === 200) {
            const body = result.body || {};
            const data = body.data || {};

            if (body.status === 1 || body.error_code === 0) {
                const isVip = data.is_vip === 1 || data.vip_type > 0;
                const endTime = data.end_time || data.vip_end_time || '';
                const vipType = data.vip_type || 0;

                Logger.info(`[VIP状态] VIP=${isVip ? '是' : '否'}, 类型=${vipType}, 到期=${endTime || '未知'}`);
                return { success: true, isVip, vipType, endTime, msg: '查询成功' };
            }

            Logger.warn(`[VIP状态] 查询结果: ${body.error_msg || JSON.stringify(body)}`);
            return { success: false, isVip: false, vipType: 0, endTime: '', msg: body.error_msg || '查询失败' };
        }

        Logger.warn(`[VIP状态] 请求失败: HTTP ${result.status}`);
        return { success: false, isVip: false, vipType: 0, endTime: '', msg: `HTTP ${result.status}` };
    } catch (err) {
        Logger.error(`[VIP状态] 异常: ${extractErrMsg(err)}`);
        return { success: false, isVip: false, vipType: 0, endTime: '', msg: extractErrMsg(err) };
    }
}

/**
 * 同步凭据到本地文件
 */
function syncCredentials(cookieObj) {
    saveTokenFile(cookieObj);
}

// ===================== 主流程 =====================

async function main() {
    Logger.info('========== 酷狗概念版每日签到 ==========');

    // 1. 从本地凭据文件加载
    const data = loadTokenFile();
    if (!data || !data.token || !data.userid) {
        Logger.error('❌ 未检测到登录凭证！');
        Logger.error('请先运行 node qrcodeLogin.js 扫码登录');
        Logger.error(`或手动创建 ${TOKEN_FILE} 文件，格式参考 README`);
        return;
    }

    const token = data.token;
    const userid = data.userid;

    // 2. 设置平台（不设 lite，QR 登录返回的是标准版 Token）
    // process.env.platform 默认为 undefined，使用标准版 appid=1005

    // 3. 构建 Cookie 对象
    let cookieObj = buildCookieObject();
    Logger.info(`用户 ID: ${userid}`);

    // 4. 注册设备 + 刷新 Token
    const registered = await registerDevice(cookieObj);
    if (registered) {
        cookieObj = registered.cookie;
        syncCredentials(cookieObj);

        const refreshed = await refreshToken(cookieObj);
        if (refreshed) {
            cookieObj = refreshed.cookie;
            syncCredentials(cookieObj);
        } else {
            Logger.warn('Token 刷新失败，尝试使用现有凭据继续...');
        }
    } else {
        Logger.warn('设备注册失败，尝试使用现有凭据继续...');
    }

    // 5. 上报听歌行为
    const listenResult = await reportListenSong(cookieObj);

    // 6. 等待 2 秒
    await new Promise((r) => setTimeout(r, 2000));

    // 7. 观看广告领 VIP（最多 8 次，每次间隔 30 秒）
    let adSuccess = 0;
    let adFailed = false;
    let adMsg = '';
    for (let i = 1; i <= 8; i++) {
        Logger.info(`[广告领VIP] 第 ${i} 次尝试...`);
        const adResult = await reportAdVip(cookieObj);
        if (adResult.success) {
            adSuccess++;
            Logger.info(`[广告领VIP] 第 ${i} 次 ✅ 成功`);
            if (i < 8) {
                await new Promise((r) => setTimeout(r, 30000));
            }
        } else if (adResult.msg.includes('30002') || adResult.msg.includes('已用光') || adResult.msg.includes('今日已完成')) {
            Logger.info(`[广告领VIP] 今日次数已用完`);
            adMsg = adResult.msg;
            break;
        } else {
            Logger.warn(`[广告领VIP] 第 ${i} 次 ❌ 失败: ${adResult.msg}`);
            adMsg = adResult.msg;
            adFailed = true;
            break;
        }
    }
    const vipResult = { success: adSuccess > 0 && !adFailed, msg: adSuccess > 0 ? `成功 ${adSuccess} 次` : (adMsg || '未成功'), count: adSuccess };

    // 8. 查询最终 VIP 状态
    const statusResult = await checkVIPStatus(cookieObj);

    // 9. 汇总输出
    Logger.info('========== 签到结果汇总 ==========');
    Logger.info(`听歌上报: ${listenResult.success ? '✅ 成功' : '❌ 失败'} - ${listenResult.msg}`);
    Logger.info(`广告领VIP: ${vipResult.success ? '✅' : '❌'} ${vipResult.msg}`);
    if (statusResult.success) {
        Logger.info(`VIP 状态: ${statusResult.isVip ? '🎉 已是 VIP' : '非 VIP'}, 到期: ${statusResult.endTime || '未知'}`);
    }

    if (listenResult.success || vipResult.success) {
        Logger.info('✅ 签到任务完成');
    } else {
        Logger.warn('⚠️ 签到任务未完全成功，请检查日志');
        Logger.info('可能原因: 1) 今日已签到 2) 网络问题 3) Token 已过期');
    }

    // 显式退出进程（避免 HTTP keep-alive 等残留连接阻止退出）
    process.exit(0);
}

// 运行
main().catch(err => {
    Logger.error(`脚本执行异常: ${err.message}`);
    console.error(err);
    process.exit(1);
});