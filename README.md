# Daily AI Report to Feishu

将 Codex 生成的中文 AI 日报发送为飞书互动卡片，并支持先上传图片、再以内嵌图片组件展示。项目只依赖 Python 标准库。

## 功能

- 通过飞书群自定义机器人 Webhook 发送互动卡片。
- 使用飞书自建应用上传 PNG、JPEG、GIF 或 WebP 图片。
- 在卡片 JSON 中用 `{{image:name}}` 引用已上传图片。
- 支持一份清单发送多张卡片，并明确报告发送失败原因。
- Windows 下优先读取用户级环境变量，避免长期运行进程使用旧配置。

## 配置

发送器从环境变量读取配置，不在仓库中保存凭证：

| 环境变量 | 用途 | 必需 |
| --- | --- | --- |
| `FEISHU_AI_DAILY_WEBHOOK_URL` | 飞书群机器人 Webhook | 是 |
| `FEISHU_AI_DAILY_APP_ID` | 飞书自建应用 App ID | 使用图片时 |
| `FEISHU_AI_DAILY_APP_SECRET` | 飞书自建应用 App Secret | 使用图片时 |
| `FEISHU_AI_DAILY_BOT_SECRET` | 群机器人签名密钥 | 开启签名校验时 |

Windows 用户级配置示例：

```powershell
[Environment]::SetEnvironmentVariable('FEISHU_AI_DAILY_WEBHOOK_URL', '<webhook>', 'User')
[Environment]::SetEnvironmentVariable('FEISHU_AI_DAILY_APP_ID', '<app-id>', 'User')
[Environment]::SetEnvironmentVariable('FEISHU_AI_DAILY_APP_SECRET', '<app-secret>', 'User')
```

飞书应用需要启用机器人能力，并具备图片资源上传权限。

## 使用

复制示例清单并替换其中的图片路径与日报内容：

```powershell
Copy-Item .\examples\daily-report.manifest.json .\daily-report.manifest.local.json
python .\scripts\feishu_card_sender.py .\daily-report.manifest.local.json
```

成功时输出类似：

```text
FEISHU_SEND_SUCCESS cards=1 images=1
```

定时调度由 Codex 自动化负责。当前设计适合每天生成文章式日报后，将 1 到 3 张高密度卡片交给本发送器投递。

## 安全

- 不要把 Webhook、App Secret、访问令牌或真实本地清单提交到 Git。
- 如果 Webhook 曾被公开，请在飞书中立即重新生成。
- 建议为群机器人开启签名校验，并为飞书应用授予最小权限。

