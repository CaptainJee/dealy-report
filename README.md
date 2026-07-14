# 每日 AI 图文情报

每天由 Codex 联网研究 AI 动态，生成文章式日报，并以带真实内嵌图片的飞书卡片推送到群聊。每个任务都是独立 profile，`Agent 真实项目应用`始终为必选栏目。

## 一键初始化

前置条件：Python 3.11+、已登录的 Codex CLI，以及可用的飞书群机器人和飞书应用凭证。若现有 Codex CLI 不支持结构化输出，还需要 Node.js/npm，以便项目在 `.runtime/` 中安装隔离版本。

```bash
git clone https://github.com/CaptainJee/dealy-report.git
cd dealy-report
python bootstrap.py
```

第一次运行会自动创建项目虚拟环境、安装锁定依赖并启动交互向导。向导依次完成：

- 设置 profile 名称、每日时间、IANA 时区、语言、受众和关注主题。
- 选择国内外来源比例、可选技术栏目、模型、推理强度和卡片数量。
- 验证 Codex 登录与 JSON Schema 结构化输出；不兼容时安装项目隔离版 `@openai/codex`。
- 将飞书凭证写入系统密钥库；不可用时仅在明确确认后写入仓库外的权限受限文件。
- 发送一张带真实内嵌 PNG 的连通性测试卡，并注册每五分钟唤醒一次的系统任务。

默认 profile 是 `daily-ai`，推送时间为 `08:30 Asia/Shanghai`，模型为 `gpt-5.5`，推理强度为 `high`。重复运行初始化会更新同名任务，不会创建重复调度项。

## 飞书准备

向导需要以下凭证，输入时不会显示内容，也不会写入仓库：

| 凭证 | 用途 |
| --- | --- |
| 群机器人 Webhook | 发送互动卡片到目标群 |
| 飞书应用 App ID | 获取租户访问令牌并上传图片 |
| 飞书应用 App Secret | 与 App ID 配套使用 |
| 群机器人签名密钥 | 可选，建议开启签名校验后填写 |

飞书应用需要启用机器人能力和图片资源上传权限。只有 Webhook 无法把外部图片变成卡片内嵌的 `image_key`，因此图文模式要求 App ID 和 App Secret。

## 任务管理

```bash
# 检查配置、凭证、Codex、结构化输出、调度器和飞书连通性
python bootstrap.py doctor --profile daily-ai

# 检查并再次发送内嵌图片测试卡
python bootstrap.py doctor --profile daily-ai --live

# 立即生成并推送一期，不等待计划时间
python bootstrap.py run --profile daily-ai --now

# 查看全部任务及最近状态
python bootstrap.py list

# 交互确认后删除调度项、profile 配置和对应凭证
python bootstrap.py remove --profile daily-ai
```

系统调度器每五分钟运行一次轻量 dispatcher，实际是否到期由 Python 按 profile 的 IANA 时区判断，因此能正确处理跨时区和夏令时。Windows 使用 Task Scheduler，macOS 使用 LaunchAgent，Linux 优先使用 systemd user timer，不可用时回退到带唯一标记的 cron。

## 日报结构

每期严格保存并渲染以下内容：

- `今日主稿`：导语和 3–5 个文章段落，说明事实、影响与开发者视角。
- `Agent 真实项目应用`：2–3 个可验证的产品、开源项目、企业案例或工程复盘，包含证据和可复用启发。
- `技术雷达`：按 profile 选择模型平台、开发工具、Agent 工程和评测信号。
- `今日行动`：立即试用、深入阅读、继续观察各一项。
- `真实配图`：3–4 张公开 HTTPS 图片，先安全下载并上传飞书，再以内嵌图片组件展示。

Codex 只在 `read-only`、`never`、`ephemeral` 模式中联网研究并输出严格 JSON；Python 负责验证数据、渲染 Markdown/飞书卡片、安全下载图片、上传与发送。模型不会接触飞书凭证，也不会直接拼装发送请求。

## 数据与恢复

profile 配置和运行数据保存在用户目录，不写入仓库：

| 平台 | 配置目录 | 报告、状态与日志目录 |
| --- | --- | --- |
| Windows | `%APPDATA%\dealy-report` | `%LOCALAPPDATA%\dealy-report` |
| macOS | `~/Library/Application Support/dealy-report` | 同左 |
| Linux | `$XDG_CONFIG_HOME/dealy-report` | `$XDG_DATA_HOME/dealy-report` |

每期会保留原始 JSON、Markdown 正文、发送 manifest、原子状态和脱敏日志。生成失败按 15 分钟、60 分钟间隔重试，每日最多三次；已成功卡片会记录进度。若网络结果不确定，任务停止自动重发，避免群内出现重复日报。

## 升级与迁移

```bash
git pull
python bootstrap.py
python bootstrap.py doctor --profile daily-ai
```

依赖锁发生变化时，`bootstrap.py` 会自动更新项目虚拟环境。初始化器不会读取或修改 Codex Desktop 的内部自动化格式；从已有 Desktop 自动化迁移时，先完成测试卡和 `run --now` 验收，再在 Codex Desktop 中暂停旧任务，避免双重推送。

## 故障排查

- `Codex login is required`：运行官方 `codex login`，完成后重新执行向导或 doctor。
- `structured-output smoke test failed`：确认模型可用；初始化器会尝试安装锁定的项目隔离版 Codex CLI。
- `Credential storage is unavailable`：安装并解锁系统密钥库，或在向导中明确同意使用权限受限的备用文件。
- 测试卡无图片：检查飞书应用的图片资源权限、App ID/App Secret 和机器人所在租户。
- 状态为 `uncertain`：先到群中确认是否已收到，再决定是否手动执行 `run --now`。

## 安全边界

远程图片仅接受 HTTPS；下载前解析公网 IP，实际连接锁定该 IP，同时保留原域名做 TLS SNI 与证书校验。每次重定向都会重新校验，并检查体积与 PNG/JPEG/GIF/WebP 文件签名。本地图片只允许连通性测试资产和显式授权目录。

不要把 Webhook、App Secret、访问令牌、`image_key`、生成报告或本地 manifest 提交到 Git。凭证一旦暴露，请立即在飞书中轮换。

## 开发验证

```bash
python -m pip install -r requirements.lock
python -m unittest discover -v
python -m compileall bootstrap.py dealy_report scripts assets tests
```
