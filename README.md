# WeChat Doorbell · 微信门铃桥

**把一个"已经认识你的个人 AI agent"接进你的个人微信。**

> A doorbell, not another chatbot: a dumb, always-on daemon listens on
> Tencent's official iLink bot protocol; when a message arrives it *rings*
> and wakes up your real agent — the one with your persona, your long-term
> memory, your context — which then replies as itself inside WeChat.

```
微信(你) ──▶ iLink 官方协议 ──▶ 守护进程(零智能) ──▶ inbox.jsonl ──▶ 门铃唤醒 ──▶ 你的 Agent 本人
            ◀── 发送脚本 ◀──────────────────────────── 读收件箱、带记忆回复 ──┘
```

## 和其他方案有什么不同

| | 常见方案 | 本项目 |
|---|---|---|
| 大脑 | 新装一个谁都不认识的通用 agent | **你已有的个人 agent 本人**（人设、长期记忆、关系上下文现成） |
| 架构 | agent 常驻进程里轮询 | **门铃式**：笨守护进程（不烧 token）＋按需唤醒全量 agent |
| 媒体 | 多为文字，或依赖框架附带能力 | 图片/文件/视频收发自研补完；**入站语音本地转写**（Whisper，全离线） |
| 边界 | 只列功能清单 | 实测钉死的「已知限制」一并公开（见下） |
| 定位 | 遥控编程 agent / 客服机器人 | 个人 agent 的微信入口 |

## 功能矩阵（均为实测结论，不是计划）

| 能力 | 状态 | 备注 |
|---|---|---|
| 收/发文字 | ✅ | 长轮询，秒级到达 |
| 发图片 | ✅ | 自行实现协议 §8.2：上传 URL→AES 加密→CDN（5xx 需同参数重试） |
| 收图片 | ✅ | 守护进程自动下载解密到本地，大脑可直接查看 |
| 发文件 | ✅ | mp3、文档均实测送达可打开 |
| 发视频 | ✅ | 静态图＋TTS 合成的"开口说话"视频可正常播放 |
| 收语音→转文字 | ✅ | pilk 解 SILK＋faster-whisper 本地转写，写入收件记录 |
| 发语音条 | ❌ | **平台对 bot 账号不投递**：API 返回成功、客户端永不显示，MP3 与原生 SILK 均实测如此；官方 TS 实现同样没有语音发送 |
| 群聊 | ❌ | iLink bot 协议仅私聊 |
| 电脑版微信显示会话 | ❌ | 手机端正常；PC 客户端不呈现 bot 会话（桥侧日志健康，原因未获官方说明） |

## 快速开始

前置条件：一台常开的 Linux 机器（VPS / 家用小主机均可）；一个个人微信号。
注意：iLink bot 授权目前仍是灰度，个别账号扫码可能不通过，属平台放量问题。

```bash
git clone https://github.com/scvzz1/-wechat-doorbell.git wechat-doorbell
cd wechat-doorbell
python3 -m venv venv && venv/bin/pip install -r requirements.txt

# 1) 登录：运行后终端显示二维码，用微信扫码确认（二维码约 1 分钟有效）
venv/bin/python login_once.py        # 凭证保存到 ~/.weixin-bot/

# 2) 启动守护进程（前台调试）
venv/bin/python bridge_bot.py
#    生产运行可用 systemd：编辑 wechat-bridge.service 里的路径后安装启用

# 3) 选一个"大脑"（见下节），启动它
```

## 三种大脑接法

桥与大脑的约定只有两条，任何能读 JSON、能执行命令的程序都能接：

- **新消息**：守护进程追加写入 `inbox.jsonl`（含媒体的本地路径与语音转写）；
- **回复**：运行 `send_reply.py <消息id> <回复文件>`（或 `send_image.py` /
  `send_file.py` / `send_video.py`），发送回执记入 `sent.jsonl`。

现成适配器（`brains/`）：

1. **裸 LLM API**：`brain_api.py`——任何 OpenAI 兼容接口，带人设文件与
   滚动历史，开箱即跑。配置见 `.env.example`，人设模板 `brains/persona.example.md`。
2. **任意 agent CLI / 命令**：`brain_command.py`——把消息 JSON 喂给
   `BRAIN_COMMAND` 的 stdin，stdout 即回复（适用于 Claude Code、Codex、
   Gemini CLI 等，用一层薄 shell 包装即可）。
3. **Muse 平台 hook（作者本人的接法）**：`examples/muse-hook/`——
   轮询脚本＋看门狗＋唤醒提示词模板。守护进程兼做心跳，登录过期时
   唤醒 agent 向主聊天告警、提示重新扫码。

## 已知限制（实测）

- **一号一活 bot**：同一微信号每次授权都会生成新的 bot 身份，但同时
  只能存活一个，后授权会顶掉先授权。想挂两个 agent，请用两个微信号。
- **语音条发不出**（见功能矩阵）；要让 agent"开口"，用视频版或音频文件附件。
- **唤醒有延迟**：门铃轮询（示例 5s）＋ agent 冷启动，回复通常在
  十几到几十秒量级。这是"按需唤醒全量 agent"的固有代价。
- **协议在灰度期**：腾讯调整协议或登录策略即可使本桥失效，请有预期。
- 非常驻机器勿用：守护进程掉线期间的消息无法补收。

## 运维

- 登录凭证：`~/.weixin-bot/credentials.json`（权限 600），**永不进仓库**
  （`.gitignore` 已写死收件箱、日志、媒体、凭证等全部运行时数据）。
- 守护进程写 `status.json` 心跳（凭证被服务端清除＝登录过期）。
- systemd 模板自带 `Restart=on-failure`；示例 hook 还附带看门狗与
  登录过期告警，可按需裁剪。

## 风险提示

- 本项目基于腾讯官方 iLink bot 协议（与官方 `@tencent-weixin/openclaw-weixin`
  插件同协议），非模拟登录、非 iPad 协议；但"用程序长期挂接个人号"
  本身仍处平台灰度地带，封号/停用风险请自行评估，重要账号谨慎使用。
- 与你对话的是你自己的 agent，它知道你的事——部署机器的安全等级
  请按"装着你半个数字生活"来对待。

## 致谢与许可

- 协议客户端与协议文档 vendored 自 [epiral/weixin-bot](https://github.com/epiral/weixin-bot)
  （MIT)，详见 `NOTICE.md`；本仓库其余代码 MIT（见 `LICENSE`）。
- 本项目与腾讯无隶属关系；WeChat/微信为腾讯商标。
