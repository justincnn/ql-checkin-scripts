# 青龙面板每日签到脚本合集

一组**已在青龙面板实测跑通**的每日自动签到脚本（Python / Node.js / Shell）。所有脚本均为**环境变量传凭据**，不内置任何账号信息，可直接 `task` 到青龙定时任务。

> ⚠️ 所有凭据（cookie/token/账号密码）通过**青龙环境变量**注入，请勿在脚本内硬编码。本项目不含任何真实凭据，纯公共脚本。

## 平台清单

| 平台 | 文件 | 凭据（环境变量） | 说明 |
|---|---|---|---|
| **百度贴吧** | `tieba_sign.js`[自写] | `BAIDU_COOKIE`（含 BDUSS） | 关注吧全签；自动跟随重定向 |
| **百度网盘** | `baidu_signin.py` | `BAIDU_COOKIE` | 每日签到+答题（agluo 版） |
| **百度网盘(旧)** | `an_baiduwangpan_checkin.py` | `BAIDU_COOKIE` | 已废弃，可用 baidu_signin.py 替代 |
| **V2EX** | `v2ex_signin.py`[自写] | `V2EX_COOKIES` | 铜币签到；requests.Session 保持会话（修复 PB3_SESSION 轮换导致领取失败） |
| **V2EX(旧)** | `v2ex_signin.mjs` | `V2EX_COOKIES` | 已废弃，PB3 会话问题，勿再用 |
| **AgentRouter** | `agentrouter_checkin.py` | `AGENTROUTER_ACCOUNT` | 登录+签到 |
| **WorkBuddy** | `raw_main_workbuddy_signin.py` | 相关 WB 变量 | 每日任务 |
| **WorkBuddy成长** | `wb_growth_signin.py` + `wb_growth.sh` | `WB_*` | 成长任务 |
| **速维云** | `svyun.py`[自写] | `SVYUN_USER1`/`SVYUN_PWD1` | 每日签到+转盘抽奖，多账号 |
| **亿邦AI** | `ebondai_checkin.py`[自写] | `ebondai-credentials.md` + `ebondai-api-key.md`(不入库) | 对话签到保 API 额度 |
| **CodeBuddy/Trae** | `checkin.py`[自写] | 凭据 JSON(accessToken 等) | 双平台状态保活 |
| **NodeSeek** | `nodeseek_checkin.py`[自写] | `NODESEEK_COOKIE` | 论坛签到 |
| **GLaDOS** | `glados_checkin.py`[自写] | cookie | 网络签到 |
| **OSHWHub** | `oshwhub-checkin.py`[自写] | cookie | 立创开源硬件签到 |
| **有道云笔记** | `youdao_checkin.py`[自写] | cookie | 签到 |
| **微博/超话** | `weibo_checkin.py` / `weibo_chaohua_checkin.py`[自写] | 微博 cookie | 签到 |
| **什么值得买** | `zdm_draw.py`[自写] | `SMZDM_COOKIE` | 抽奖 |
| **京东** | `jd_sign.js`[自写] | 京东 cookie | 每日任务 |
| **Telegram 推送** | `tg_send.py` / `notify.py` / `notify_wrap.sh`[自写] | `TG_BOT_TOKEN`/`TG_USER_ID` | 通用通知 helper |
| **Trae** | `trae_checkin.sh` | `TRAE_*` | IDE 签到 |
| **酷狗概念版(VIP)** | `kugou_main.js` + `kugou_qrcode.js` | `kg_token.json`(二维码登录) | 听歌+看广告领 VIP，自动续 token |
| **酷狗金币任务** | `kugou_task.py` | `KGGNB`(备注#token#userid) | 每日金币任务自动领取(多账号,签名逆向后零依赖) |
| **酷狗金币提现** | `kugou_withdraw.py` | `KG`(备注#token#userid#openid) | 定时抢额度提现(窗口 0/8/12/16/20 点,需 App 内已微信授权+实名) |
| **爱奇艺** | `dlp/iqiyi_checkin.py` | `IQIYI_COOKIE` | 签到+抽奖 |
| **网易云音乐** | `dlp/netease_music_checkin.py` | `NETEASE_COOKIE` | 签到 |
| **腾讯视频** | `dlp/tencent_video_checkin.py` | `TENCENT_VIDEO_COOKIE` | 签到 |
| **139云盘** | `dlp/139_yunduo.py` | `YDYP_PHONE`/`YDYP_AUTH` | 云朵任务+签 |
| **Bilibili** | `dlp/bilibili.py` | `BILIBILI_COOKIES` | 每日经验+投币 |
| **LDC商店(ChatGPT)** | `ldc_checkin.py` | `CHECKIN_COOKIE` | 每日积分 |

## 快速部署（青龙）

1. 把脚本放进青龙的 `/ql/data/scripts/`（或 `task` 指定路径）
2. 在青龙「环境变量」加对应凭据（见上表）
3. 「定时任务」新增：
   ```
   task /ql/data/scripts/<脚本>
   ```
4. 建议 cron 形如 `0 8 * * *`（每日 08:00），避免与平台风控集中。

### 示例：贴吧签到
```bash
ql repo https://raw.githubusercontent.com/justincnn/ql-checkin-scripts/main/tieba_sign.js
```
（或手动 upload 脚本文件）

## 各平台凭据获取
- **BAIDU_COOKIE**：登录 pan.baidu.com → F12 → Application → Cookies → 复制含 `BDUSS` 的完整 cookie
- **酷狗**(VIP续费)：`cd kgcheckin && npm install && node kugou_qrcode.js` → 手机酷狗 App 扫码 → 生成 `kg_config.json`（自动续 token）
- 其它均为对应平台登录后抓取 cookie（`xxx_COOKIE`）

## 说明 / 免责声明
- 本项目脚本仅供个人学习任务自动化研究，请遵守各平台服务条款。
- 凭据仅存于你自己青龙环境，脚本零上报、零外传。
- 平台接口可能变动导致失效，失效时请刷新对应 cookie。

## License
MIT