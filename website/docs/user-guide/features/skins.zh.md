---
sidebar_position: 10
title: "皮肤与主题"
description: "使用内置和用户自定义皮肤来定制 Hermes CLI 的外观"
---

# 皮肤与主题

皮肤控制 Hermes CLI 的**视觉呈现**：横幅颜色、加载动画的表情和动词、回复框标签、品牌文本以及工具活动前缀。

对话风格和视觉风格是两个独立的概念：

- **个性**改变智能体的语气和措辞。
- **皮肤**改变 CLI 的外观。

## 切换皮肤

```bash
/skin                # 显示当前皮肤并列出可用皮肤
/skin ares           # 切换到内置皮肤
/skin mytheme        # 切换到 ~/.hermes/skins/mytheme.yaml 中的自定义皮肤
```

或在 `~/.hermes/config.yaml` 中设置默认皮肤：

```yaml
display:
  skin: default
```

## 内置皮肤

| 皮肤 | 描述 | 智能体品牌 | 视觉特征 |
|------|------|-----------|----------|
| `default` | 经典 Hermes -- 金色与可爱风 | `Hermes Agent` | 温暖的金色边框，玉米丝色文字，加载动画中的可爱表情。经典的双蛇杖横幅。简洁而温馨。 |
| `ares` | 战神主题 -- 深红与青铜 | `Ares Agent` | 深红色边框搭配青铜色调。激进的加载动词（"锻造中"、"行军中"、"淬炼钢铁中"）。自定义剑盾 ASCII 艺术横幅。 |
| `mono` | 单色 -- 简洁灰度 | `Hermes Agent` | 全灰色调 -- 无彩色。边框为 `#555555`，文字为 `#c9d1d9`。适合极简终端设置或屏幕录制。 |
| `slate` | 冷蓝色 -- 开发者风格 | `Hermes Agent` | 皇家蓝边框（`#4169e1`），柔和蓝色文字。沉稳专业。无自定义加载动画 -- 使用默认表情。 |
| `daylight` | 亮色主题，适用于明亮终端，深色文字搭配冷蓝色调 | `Hermes Agent` | 专为白色或明亮终端设计。深石板色文字搭配蓝色边框，浅色状态面板，以及在亮色终端配置文件中保持可读性的浅色补全菜单。 |
| `warm-lightmode` | 适用于亮色终端背景的暖棕/金色文字 | `Hermes Agent` | 适用于亮色终端的暖羊皮纸色调。深棕色文字搭配鞍棕色调，奶油色状态面板。比冷色调的 daylight 主题更具泥土气息。 |
| `poseidon` | 海神主题 -- 深蓝与海泡绿 | `Poseidon Agent` | 深蓝到海泡绿的渐变。海洋主题加载动画（"测绘洋流中"、"探测深度中"）。三叉戟 ASCII 艺术横幅。 |
| `sisyphus` | 西西弗斯主题 -- 朴素灰度搭配坚韧感 | `Sisyphus Agent` | 浅灰色搭配强烈对比。巨石主题加载动画（"推石上坡中"、"重置巨石中"、"忍耐循环中"）。巨石与山丘 ASCII 艺术横幅。 |
| `charizard` | 火山主题 -- 焦橙与余烬 | `Charizard Agent` | 温暖的焦橙到余烬渐变。火焰主题加载动画（"顺风滑翔中"、"测量燃烧中"）。龙影 ASCII 艺术横幅。 |

## 可配置键的完整列表

### 颜色 (`colors:`)

控制 CLI 中所有的颜色值。值为十六进制颜色字符串。

| 键 | 描述 | 默认值（`default` 皮肤） |
|----|------|--------------------------|
| `banner_border` | 启动横幅周围的面板边框 | `#CD7F32`（青铜色） |
| `banner_title` | 横幅中的标题文字颜色 | `#FFD700`（金色） |
| `banner_accent` | 横幅中的章节标题（可用工具等） | `#FFBF00`（琥珀色） |
| `banner_dim` | 横幅中的暗淡文字（分隔符、次要标签） | `#B8860B`（暗金棒色） |
| `banner_text` | 横幅中的正文文字（工具名称、技能名称） | `#FFF8DC`（玉米丝色） |
| `ui_accent` | 通用 UI 强调色（高亮、活动元素） | `#FFBF00` |
| `ui_label` | UI 标签和标记 | `#4dd0e1`（青色） |
| `ui_ok` | 成功指示器（勾选标记、完成） | `#4caf50`（绿色） |
| `ui_error` | 错误指示器（失败、阻止） | `#ef5350`（红色） |
| `ui_warn` | 警告指示器（注意、审批提示） | `#ffa726`（橙色） |
| `prompt` | 交互式提示文字颜色 | `#FFF8DC` |
| `input_rule` | 输入区域上方的水平线 | `#CD7F32` |
| `response_border` | 智能体回复框的边框（ANSI 转义） | `#FFD700` |
| `session_label` | 会话标签颜色 | `#DAA520` |
| `session_border` | 会话 ID 暗淡边框颜色 | `#8B8682` |
| `status_bar_bg` | TUI 状态/用量栏的背景颜色 | `#1a1a2e` |
| `voice_status_bg` | 语音模式状态徽章的背景颜色 | `#1a1a2e` |
| `completion_menu_bg` | 补全菜单列表的背景颜色 | `#1a1a2e` |
| `completion_menu_current_bg` | 活动补全行的背景颜色 | `#333355` |
| `completion_menu_meta_bg` | 补全元数据列的背景颜色 | `#1a1a2e` |
| `completion_menu_meta_current_bg` | 活动补全元数据列的背景颜色 | `#333355` |

### 加载动画 (`spinner:`)

控制等待 API 响应时显示的动画加载器。

| 键 | 类型 | 描述 | 示例 |
|----|------|------|------|
| `waiting_faces` | 字符串列表 | 等待 API 响应时循环显示的表情 | `["(⚔)", "(⛨)", "(▲)"]` |
| `thinking_faces` | 字符串列表 | 模型推理时循环显示的表情 | `["(⚔)", "(⌁)", "(<>)"]` |
| `thinking_verbs` | 字符串列表 | 加载消息中显示的动词 | `["forging", "plotting", "hammering plans"]` |
| `wings` | [左, 右] 对的列表 | 加载器周围的装饰括号 | `[["⟪⚔", "⚔⟫"], ["⟪▲", "▲⟫"]]` |

当加载动画值为空时（如 `default` 和 `mono`），将使用 `display.py` 中的硬编码默认值。

### 品牌 (`branding:`)

在 CLI 界面中使用的文本字符串。

| 键 | 描述 | 默认值 |
|----|------|--------|
| `agent_name` | 显示在横幅标题和状态显示中的名称 | `Hermes Agent` |
| `welcome` | CLI 启动时显示的欢迎消息 | `Welcome to Hermes Agent! Type your message or /help for commands.` |
| `goodbye` | 退出时显示的消息 | `Goodbye! ⚕` |
| `response_label` | 回复框头部的标签 | ` ⚕ Hermes ` |
| `prompt_symbol` | 用户输入提示前的符号 | `❯ ` |
| `help_header` | `/help` 命令输出的标题文本 | `(^_^)? Available Commands` |

### 其他顶层键

| 键 | 类型 | 描述 | 默认值 |
|----|------|------|--------|
| `tool_prefix` | 字符串 | CLI 中工具输出行前缀的字符 | `┊` |
| `tool_emojis` | 字典 | 每个工具的加载器和进度表情覆盖（`{tool_name: emoji}`） | `{}` |
| `banner_logo` | 字符串 | Rich 标记 ASCII 艺术 logo（替换默认的 HERMES_AGENT 横幅） | `""` |
| `banner_hero` | 字符串 | Rich 标记英雄艺术（替换默认的双蛇杖艺术） | `""` |

## 自定义皮肤

在 `~/.hermes/skins/` 下创建 YAML 文件。用户皮肤会从内置的 `default` 皮肤继承缺失的值，因此你只需要指定想要更改的键。

### 完整自定义皮肤 YAML 模板

```yaml
# ~/.hermes/skins/mytheme.yaml
# 完整皮肤模板 -- 显示所有键。删除不需要的键；
# 缺失的值会自动从 'default' 皮肤继承。

name: mytheme
description: My custom theme

colors:
  banner_border: "#CD7F32"
  banner_title: "#FFD700"
  banner_accent: "#FFBF00"
  banner_dim: "#B8860B"
  banner_text: "#FFF8DC"
  ui_accent: "#FFBF00"
  ui_label: "#4dd0e1"
  ui_ok: "#4caf50"
  ui_error: "#ef5350"
  ui_warn: "#ffa726"
  prompt: "#FFF8DC"
  input_rule: "#CD7F32"
  response_border: "#FFD700"
  session_label: "#DAA520"
  session_border: "#8B8682"
  status_bar_bg: "#1a1a2e"
  voice_status_bg: "#1a1a2e"
  completion_menu_bg: "#1a1a2e"
  completion_menu_current_bg: "#333355"
  completion_menu_meta_bg: "#1a1a2e"
  completion_menu_meta_current_bg: "#333355"

spinner:
  waiting_faces:
    - "(⚔)"
    - "(⛨)"
    - "(▲)"
  thinking_faces:
    - "(⚔)"
    - "(⌁)"
    - "(<>)"
  thinking_verbs:
    - "processing"
    - "analyzing"
    - "computing"
    - "evaluating"
  wings:
    - ["⟪⚡", "⚡⟫"]
    - ["⟪●", "●⟫"]

branding:
  agent_name: "My Agent"
  welcome: "Welcome to My Agent! Type your message or /help for commands."
  goodbye: "See you later! ⚡"
  response_label: " ⚡ My Agent "
  prompt_symbol: "⚡ ❯ "
  help_header: "(⚡) Available Commands"

tool_prefix: "┊"

# 每个工具的表情覆盖（可选）
tool_emojis:
  terminal: "⚔"
  web_search: "🔮"
  read_file: "📄"

# 自定义 ASCII 艺术横幅（可选，支持 Rich 标记）
# banner_logo: |
#   [bold #FFD700] MY AGENT [/]
# banner_hero: |
#   [#FFD700]  Custom art here  [/]
```

### 最小自定义皮肤示例

由于所有内容都从 `default` 继承，最小皮肤只需更改不同的部分：

```yaml
name: cyberpunk
description: Neon terminal theme

colors:
  banner_border: "#FF00FF"
  banner_title: "#00FFFF"
  banner_accent: "#FF1493"

spinner:
  thinking_verbs: ["jacking in", "decrypting", "uploading"]
  wings:
    - ["⟨⚡", "⚡⟩"]

branding:
  agent_name: "Cyber Agent"
  response_label: " ⚡ Cyber "

tool_prefix: "▏"
```

## Hermes Mod -- 可视化皮肤编辑器

[Hermes Mod](https://github.com/cocktailpeanut/hermes-mod) 是一个社区开发的 Web UI，用于可视化创建和管理皮肤。无需手动编写 YAML，你可以使用点击式编辑器并实时预览。

![Hermes Mod 皮肤编辑器](https://raw.githubusercontent.com/cocktailpeanut/hermes-mod/master/nous.png)

**功能说明：**

- 列出所有内置和自定义皮肤
- 在可视化编辑器中打开任意皮肤，包含所有 Hermes 皮肤字段（颜色、加载动画、品牌、工具前缀、工具表情）
- 从文本提示生成 `banner_logo` 文字艺术
- 将上传的图片（PNG、JPG、GIF、WEBP）转换为 `banner_hero` ASCII 艺术，支持多种渲染风格（盲文、ASCII 渐变、方块、点阵）
- 直接保存到 `~/.hermes/skins/`
- 通过更新 `~/.hermes/config.yaml` 激活皮肤
- 显示生成的 YAML 和实时预览

### 安装

**方式 1 -- Pinokio（一键安装）：**

在 [pinokio.computer](https://pinokio.computer) 上找到它并一键安装。

**方式 2 -- npx（从终端最快）：**

```bash
npx -y hermes-mod
```

**方式 3 -- 手动安装：**

```bash
git clone https://github.com/cocktailpeanut/hermes-mod.git
cd hermes-mod/app
npm install
npm start
```

### 使用方法

1. 启动应用（通过 Pinokio 或终端）。
2. 打开 **Skin Studio**。
3. 选择要编辑的内置或自定义皮肤。
4. 从文本生成 logo 和/或上传图片作为英雄艺术。选择渲染风格和宽度。
5. 编辑颜色、加载动画、品牌和其他字段。
6. 点击 **Save** 将皮肤 YAML 写入 `~/.hermes/skins/`。
7. 点击 **Activate** 将其设为当前皮肤（更新 `config.yaml` 中的 `display.skin`）。

Hermes Mod 支持 `HERMES_HOME` 环境变量，因此也可以配合[配置文件](/docs/user-guide/profiles)使用。

## 操作说明

- 内置皮肤从 `hermes_cli/skin_engine.py` 加载。
- 未知皮肤会自动回退到 `default`。
- `/skin` 会立即更新当前会话的活动 CLI 主题。
- `~/.hermes/skins/` 中的用户皮肤优先于同名的内置皮肤。
- 通过 `/skin` 进行的皮肤更改仅在当前会话有效。要设为永久默认皮肤，请在 `config.yaml` 中设置。
- `banner_logo` 和 `banner_hero` 字段支持 Rich 控制台标记（例如 `[bold #FF0000]text[/]`）用于彩色 ASCII 艺术。
