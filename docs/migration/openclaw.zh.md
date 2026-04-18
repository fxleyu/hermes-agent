# 从 OpenClaw 迁移到 Hermes Agent

本指南介绍如何将您的 OpenClaw 设置、记忆、技能和 API 密钥导入到 Hermes Agent 中。

## 三种迁移方式

### 1. 自动迁移（首次设置时）

当您首次运行 `hermes setup` 且 Hermes 检测到 `~/.openclaw` 时，它会在配置开始前自动提示导入您的 OpenClaw 数据。只需接受提示，一切都会自动处理。

### 2. CLI 命令（快速、可脚本化）

```bash
hermes claw migrate                      # 预览后迁移（始终先显示预览）
hermes claw migrate --dry-run            # 仅预览，不做更改
hermes claw migrate --preset user-data   # 迁移但不包含 API 密钥/密钥
hermes claw migrate --yes                # 跳过确认提示
```

迁移始终会在进行任何更改之前显示将要导入内容的完整预览。您在确认之前审查预览，之后才会写入任何内容。

**所有选项：**

| 标志 | 描述 |
|------|------|
| `--source PATH` | OpenClaw 目录路径（默认：`~/.openclaw`） |
| `--dry-run` | 仅预览——不修改任何文件 |
| `--preset {user-data,full}` | 迁移预设（默认：`full`）。`user-data` 排除密钥 |
| `--overwrite` | 覆盖现有文件（默认：跳过冲突） |
| `--migrate-secrets` | 包含允许列表中的密钥（`full` 预设自动启用） |
| `--workspace-target PATH` | 将工作区指令（AGENTS.md）复制到此绝对路径 |
| `--skill-conflict {skip,overwrite,rename}` | 如何处理技能名称冲突（默认：`skip`） |
| `--yes`, `-y` | 跳过确认提示 |

### 3. 代理引导式（交互式，带预览）

让代理为您运行迁移：

```
> 将我的 OpenClaw 设置迁移到 Hermes
```

代理将使用 `openclaw-migration` 技能：
1. 首先运行预览以显示将要更改的内容
2. 询问冲突解决方案（SOUL.md、技能等）
3. 让您在 `user-data` 和 `full` 预设之间选择
4. 按您的选择执行迁移
5. 打印迁移内容的详细摘要

## 迁移内容

### `user-data` 预设
| 项目 | 来源 | 目标 |
|------|------|------|
| SOUL.md | `~/.openclaw/workspace/SOUL.md` | `~/.hermes/SOUL.md` |
| 记忆条目 | `~/.openclaw/workspace/MEMORY.md` | `~/.hermes/memories/MEMORY.md` |
| 用户配置文件 | `~/.openclaw/workspace/USER.md` | `~/.hermes/memories/USER.md` |
| 技能 | `~/.openclaw/workspace/skills/` | `~/.hermes/skills/openclaw-imports/` |
| 命令允许列表 | `~/.openclaw/workspace/exec_approval_patterns.yaml` | 合并到 `~/.hermes/config.yaml` |
| 消息设置 | `~/.openclaw/config.yaml`（TELEGRAM_ALLOWED_USERS、MESSAGING_CWD） | `~/.hermes/.env` |
| TTS 资源 | `~/.openclaw/workspace/tts/` | `~/.hermes/tts/` |

工作区文件也会检查 `workspace.default/` 和 `workspace-main/` 作为备用路径（OpenClaw 在最近版本中将 `workspace/` 重命名为 `workspace-main/`）。

### `full` 预设（在 `user-data` 基础上添加）
| 项目 | 来源 | 目标 |
|------|------|------|
| Telegram 机器人令牌 | `openclaw.json` channels 配置 | `~/.hermes/.env` |
| OpenRouter API 密钥 | `.env`、`openclaw.json` 或 `openclaw.json["env"]` | `~/.hermes/.env` |
| OpenAI API 密钥 | `.env`、`openclaw.json` 或 `openclaw.json["env"]` | `~/.hermes/.env` |
| Anthropic API 密钥 | `.env`、`openclaw.json` 或 `openclaw.json["env"]` | `~/.hermes/.env` |
| ElevenLabs API 密钥 | `.env`、`openclaw.json` 或 `openclaw.json["env"]` | `~/.hermes/.env` |

API 密钥从四个来源搜索：内联配置值、`~/.openclaw/.env`、`openclaw.json` 的 `"env"` 子对象以及每代理的认证配置文件。

只有允许列表中的密钥才会被导入。其他凭据会被跳过并报告。

## OpenClaw 架构兼容性

迁移处理新旧两种 OpenClaw 配置布局：

- **频道令牌**：从扁平路径（`channels.telegram.botToken`）和较新的 `accounts.default` 布局（`channels.telegram.accounts.default.botToken`）读取
- **TTS 提供商**：OpenClaw 将 "edge" 重命名为 "microsoft"——两者都被识别并映射到 Hermes 的 "edge"
- **提供商 API 类型**：短格式（`openai`、`anthropic`）和连字符格式（`openai-completions`、`anthropic-messages`、`google-generative-ai`）值都能正确映射
- **thinkingDefault**：所有枚举值都能处理，包括较新的值（`minimal`、`xhigh`、`adaptive`）
- **Matrix**：使用 `accessToken` 字段（而非 `botToken`）
- **SecretRef 格式**：纯字符串、env 模板（`${VAR}`）和 `source: "env"` SecretRef 都能解析。`source: "file"` 和 `source: "exec"` SecretRef 会产生警告——迁移后手动添加这些密钥。

## 冲突处理

默认情况下，迁移 **不会覆盖** 现有的 Hermes 数据：

- **SOUL.md** — 如果 `~/.hermes/` 中已存在则跳过
- **记忆条目** — 如果记忆已存在则跳过（避免重复）
- **技能** — 如果同名技能已存在则跳过
- **API 密钥** — 如果密钥已在 `~/.hermes/.env` 中设置则跳过

要覆盖冲突，使用 `--overwrite`。迁移会在覆盖前创建备份。

对于技能，您还可以使用 `--skill-conflict rename` 以新名称导入冲突的技能（例如 `skill-name-imported`）。

## 迁移报告

每次迁移都会生成一个报告，显示：
- **已迁移项目** — 成功导入的内容
- **冲突** — 因已存在而跳过的项目
- **跳过的项目** — 在源中未找到的项目
- **错误** — 导入失败的项目

对于已执行的迁移，完整报告保存到 `~/.hermes/migration/openclaw/<timestamp>/`。

## 迁移后注意事项

- **技能需要新会话** — 导入的技能在重启代理或开始新聊天后生效。
- **WhatsApp 需要重新配对** — WhatsApp 使用二维码配对，而非基于令牌的认证。运行 `hermes whatsapp` 进行配对。
- **归档清理** — 迁移后，系统会提示将 `~/.openclaw/` 重命名为 `.openclaw.pre-migration/` 以防止状态混淆。您也可以稍后运行 `hermes claw cleanup`。

## 故障排除

### "OpenClaw directory not found"
迁移默认查找 `~/.openclaw`，然后尝试 `~/.clawdbot` 和 `~/.moltbot`。如果您的 OpenClaw 安装在其他位置，使用 `--source`：
```bash
hermes claw migrate --source /path/to/.openclaw
```

### "Migration script not found"
迁移脚本随 Hermes Agent 一起发布。如果您通过 pip 安装（而非 git clone），`optional-skills/` 目录可能不存在。从技能中心安装该技能：
```bash
hermes skills install openclaw-migration
```

### 记忆溢出
如果您的 OpenClaw MEMORY.md 或 USER.md 超过 Hermes 的字符限制，多余条目将导出到迁移报告目录中的溢出文件。您可以手动审查并添加最重要的条目。

### 找不到 API 密钥
密钥可能根据您的 OpenClaw 设置存储在不同位置：
- `~/.openclaw/.env` 文件
- `openclaw.json` 中 `models.providers.*.apiKey` 的内联值
- `openclaw.json` 中 `"env"` 或 `"env.vars"` 子对象内
- `~/.openclaw/agents/main/agent/auth-profiles.json` 中

迁移会检查所有四个位置。如果密钥使用 `source: "file"` 或 `source: "exec"` SecretRef，它们无法自动解析——通过 `hermes config set` 添加。
