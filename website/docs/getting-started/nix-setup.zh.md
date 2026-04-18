---
sidebar_position: 3
title: "Nix 与 NixOS 设置"
description: "使用 Nix 安装和部署 Hermes Agent——从快速 `nix run` 到完全声明式 NixOS 模块（含容器模式）"
---

# Nix 与 NixOS 设置

Hermes Agent 提供了一个 Nix flake，包含三个集成级别：

| 级别 | 适用人群 | 提供内容 |
|-------|-------------|--------------|
| **`nix run` / `nix profile install`** | 任何 Nix 用户（macOS、Linux） | 预构建二进制文件，包含所有依赖——然后使用标准 CLI 工作流 |
| **NixOS 模块（原生）** | NixOS 服务器部署 | 声明式配置、加固的 systemd 服务、托管密钥 |
| **NixOS 模块（容器）** | 需要自我修改的智能体 | 以上所有内容，加上一个持久化的 Ubuntu 容器，智能体可以在其中 `apt`/`pip`/`npm install` |

:::info 与标准安装的区别
`curl | bash` 安装程序自行管理 Python、Node 和依赖。Nix flake 替换了所有这些——每个 Python 依赖都是由 [uv2nix](https://github.com/pyproject-nix/uv2nix) 构建的 Nix derivation，运行时工具（Node.js、git、ripgrep、ffmpeg）被包装到二进制文件的 PATH 中。没有运行时 pip，没有虚拟环境激活，没有 `npm install`。

**对于非 NixOS 用户**，这只改变了安装步骤。之后的所有内容（`hermes setup`、`hermes gateway install`、配置编辑）与标准安装完全相同。

**对于 NixOS 模块用户**，整个生命周期都不同：配置在 `configuration.nix` 中，密钥通过 sops-nix/agenix 管理，服务是 systemd 单元，CLI 配置命令被阻止。你像管理任何其他 NixOS 服务一样管理 hermes。
:::

## 先决条件

- **启用了 flakes 的 Nix** —— 推荐 [Determinate Nix](https://install.determinate.systems)（默认启用 flakes）
- 你想使用的服务的 **API 密钥**（至少：一个 OpenRouter 或 Anthropic 密钥）

---

## 快速开始（任何 Nix 用户）

无需克隆。Nix 获取、构建并运行一切：

```bash
# 直接运行（首次使用时构建，之后缓存）
nix run github:NousResearch/hermes-agent -- setup
nix run github:NousResearch/hermes-agent -- chat

# 或持久安装
nix profile install github:NousResearch/hermes-agent
hermes setup
hermes chat
```

`nix profile install` 后，`hermes`、`hermes-agent` 和 `hermes-acp` 就在你的 PATH 上了。从这里开始，工作流与[标准安装](./installation.md)完全相同——`hermes setup` 引导你选择提供商，`hermes gateway install` 设置 launchd（macOS）或 systemd 用户服务，配置保存在 `~/.hermes/`。

<details>
<summary><strong>从本地克隆构建</strong></summary>

```bash
git clone https://github.com/NousResearch/hermes-agent.git
cd hermes-agent
nix build
./result/bin/hermes setup
```

</details>

---

## NixOS 模块

该 flake 导出 `nixosModules.default`——一个完整的 NixOS 服务模块，声明式地管理用户创建、目录、配置生成、密钥、文档和服务生命周期。

:::note
此模块需要 NixOS。对于非 NixOS 系统（macOS、其他 Linux 发行版），使用 `nix profile install` 和上述标准 CLI 工作流。
:::

### 添加 Flake 输入

```nix
# /etc/nixos/flake.nix（或你的系统 flake）
{
  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    hermes-agent.url = "github:NousResearch/hermes-agent";
  };

  outputs = { nixpkgs, hermes-agent, ... }: {
    nixosConfigurations.your-host = nixpkgs.lib.nixosSystem {
      system = "x86_64-linux";
      modules = [
        hermes-agent.nixosModules.default
        ./configuration.nix
      ];
    };
  };
}
```

### 最小配置

```nix
# configuration.nix
{ config, ... }: {
  services.hermes-agent = {
    enable = true;
    settings.model.default = "anthropic/claude-sonnet-4";
    environmentFiles = [ config.sops.secrets."hermes-env".path ];
    addToSystemPackages = true;
  };
}
```

就这样。`nixos-rebuild switch` 创建 `hermes` 用户、生成 `config.yaml`、接入密钥并启动网关——一个连接智能体到消息平台（Telegram、Discord 等）并监听传入消息的长运行服务。

:::warning 密钥是必需的
上面的 `environmentFiles` 行假设你已配置了 [sops-nix](https://github.com/Mic92/sops-nix) 或 [agenix](https://github.com/ryantm/agenix)。该文件应至少包含一个 LLM 提供商密钥（例如 `OPENROUTER_API_KEY=sk-or-...`）。详见[密钥管理](#secrets-management)。如果你还没有密钥管理器，可以使用纯文本文件作为起点——只需确保它不是全局可读的：

```bash
echo "OPENROUTER_API_KEY=sk-or-your-key" | sudo install -m 0600 -o hermes /dev/stdin /var/lib/hermes/env
```

```nix
services.hermes-agent.environmentFiles = [ "/var/lib/hermes/env" ];
```
:::

:::tip addToSystemPackages
设置 `addToSystemPackages = true` 做两件事：将 `hermes` CLI 放到系统 PATH 上**并且**在系统范围设置 `HERMES_HOME`，使交互式 CLI 与网关服务共享状态（会话、技能、定时任务）。没有它，在 shell 中运行 `hermes` 会创建一个单独的 `~/.hermes/` 目录。
:::

:::info 容器感知 CLI
当 `container.enable = true` 且 `addToSystemPackages = true` 时，主机上**每个** `hermes` 命令都自动路由到托管容器中。这意味着你的交互式 CLI 会话在与网关服务相同的环境中运行——可以访问所有容器中安装的包和工具。

- 路由是透明的：`hermes chat`、`hermes sessions list`、`hermes version` 等都在底层 exec 到容器中
- 所有 CLI 标志原样转发
- 如果容器未运行，CLI 会短暂重试（交互使用时 5 秒带转圈动画，脚本使用时 10 秒静默），然后以清晰的错误失败——不会静默回退
- 对于在 hermes 代码库上工作的开发者，设置 `HERMES_DEV=1` 绕过容器路由，直接运行本地检出

设置 `container.hostUsers` 创建一个 `~/.hermes` 符号链接到服务状态目录，使主机 CLI 和容器共享会话、配置和记忆：

```nix
services.hermes-agent = {
  container.enable = true;
  container.hostUsers = [ "your-username" ];
  addToSystemPackages = true;
};
```

`hostUsers` 中列出的用户会自动添加到 `hermes` 组以获取文件权限访问。

**Podman 用户：** NixOS 服务以 root 运行容器。Docker 用户通过 `docker` 组套接字获取访问，但 Podman 的 rootful 容器需要 sudo。为你的容器运行时授予免密 sudo：

```nix
security.sudo.extraRules = [{
  users = [ "your-username" ];
  commands = [{
    command = "/run/current-system/sw/bin/podman";
    options = [ "NOPASSWD" ];
  }];
}];
```

CLI 自动检测何时需要 sudo 并透明使用。没有这个，你需要手动运行 `sudo hermes chat`。
:::

### 验证是否工作

`nixos-rebuild switch` 后，检查服务是否运行：

```bash
# 检查服务状态
systemctl status hermes-agent

# 查看日志（Ctrl+C 停止）
journalctl -u hermes-agent -f

# 如果 addToSystemPackages 为 true，测试 CLI
hermes version
hermes config       # 显示生成的配置
```

### 选择部署模式

该模块支持两种模式，由 `container.enable` 控制：

| | **原生**（默认） | **容器** |
|---|---|---|
| 运行方式 | 主机上加固的 systemd 服务 | 持久化 Ubuntu 容器，绑定挂载 `/nix/store` |
| 安全性 | `NoNewPrivileges`、`ProtectSystem=strict`、`PrivateTmp` | 容器隔离，以非特权用户在内部运行 |
| 智能体可以自行安装包 | 否——只有 Nix 提供的 PATH 上的工具 | 是——`apt`、`pip`、`npm` 安装在重启后持久化 |
| 配置界面 | 相同 | 相同 |
| 何时选择 | 标准部署、最大安全性、可重现性 | 智能体需要运行时包安装、可变环境、实验性工具 |

要启用容器模式，添加一行：

```nix
{
  services.hermes-agent = {
    enable = true;
    container.enable = true;
    # ... 其余配置相同
  };
}
```

:::info
容器模式通过 `mkDefault` 自动启用 `virtualisation.docker.enable`。如果你使用 Podman，设置 `container.backend = "podman"` 和 `virtualisation.docker.enable = false`。
:::

---

## 配置

### 声明式设置

`settings` 选项接受一个任意属性集，被渲染为 `config.yaml`。它支持跨多个模块定义的深度合并（通过 `lib.recursiveUpdate`），因此你可以将配置分散到多个文件中：

```nix
# base.nix
services.hermes-agent.settings = {
  model.default = "anthropic/claude-sonnet-4";
  toolsets = [ "all" ];
  terminal = { backend = "local"; timeout = 180; };
};

# personality.nix
services.hermes-agent.settings = {
  display = { compact = false; personality = "kawaii"; };
  memory = { memory_enabled = true; user_profile_enabled = true; };
};
```

两者在评估时深度合并。Nix 声明的键始终优先于磁盘上现有 `config.yaml` 中的键，但 **Nix 未涉及的用户添加的键会被保留**。这意味着如果智能体或手动编辑添加了 `skills.disabled` 或 `streaming.enabled` 等键，它们在 `nixos-rebuild switch` 后仍然存在。

:::note 模型命名
`settings.model.default` 使用你的提供商期望的模型标识符。使用 [OpenRouter](https://openrouter.ai)（默认）时，它们看起来像 `"anthropic/claude-sonnet-4"` 或 `"google/gemini-3-flash"`。如果你直接使用提供商（Anthropic、OpenAI），设置 `settings.model.base_url` 指向它们的 API 并使用它们原生的模型 ID（例如 `"claude-sonnet-4-20250514"`）。当没有设置 `base_url` 时，Hermes 默认使用 OpenRouter。
:::

:::tip 发现可用的配置键
运行 `nix build .#configKeys && cat result` 查看从 Python 的 `DEFAULT_CONFIG` 中提取的每个叶配置键。你可以将现有的 `config.yaml` 粘贴到 `settings` 属性集中——结构是 1:1 映射的。
:::

<details>
<summary><strong>完整示例：所有常用自定义设置</strong></summary>

```nix
{ config, ... }: {
  services.hermes-agent = {
    enable = true;
    container.enable = true;

    # ── 模型 ──────────────────────────────────────────────────────────
    settings = {
      model = {
        base_url = "https://openrouter.ai/api/v1";
        default = "anthropic/claude-opus-4.6";
      };
      toolsets = [ "all" ];
      max_turns = 100;
      terminal = { backend = "local"; cwd = "."; timeout = 180; };
      compression = {
        enabled = true;
        threshold = 0.85;
        summary_model = "google/gemini-3-flash-preview";
      };
      memory = { memory_enabled = true; user_profile_enabled = true; };
      display = { compact = false; personality = "kawaii"; };
      agent = { max_turns = 60; verbose = false; };
    };

    # ── 密钥 ────────────────────────────────────────────────────────
    environmentFiles = [ config.sops.secrets."hermes-env".path ];

    # ── 文档 ──────────────────────────────────────────────────────────
    documents = {
      "USER.md" = ./documents/USER.md;
    };

    # ── MCP 服务器 ────────────────────────────────────────────────────
    mcpServers.filesystem = {
      command = "npx";
      args = [ "-y" "@modelcontextprotocol/server-filesystem" "/data/workspace" ];
    };

    # ── 容器选项 ──────────────────────────────────────────────────────
    container = {
      image = "ubuntu:24.04";
      backend = "docker";
      hostUsers = [ "your-username" ];
      extraVolumes = [ "/home/user/projects:/projects:rw" ];
      extraOptions = [ "--gpus" "all" ];
    };

    # ── 服务调优 ─────────────────────────────────────────────────────
    addToSystemPackages = true;
    extraArgs = [ "--verbose" ];
    restart = "always";
    restartSec = 5;
  };
}
```

</details>

### 逃生舱口：自带配置

如果你更愿意完全在 Nix 之外管理 `config.yaml`，使用 `configFile`：

```nix
services.hermes-agent.configFile = /etc/hermes/config.yaml;
```

这完全绕过 `settings`——没有合并，没有生成。该文件在每次激活时原样复制到 `$HERMES_HOME/config.yaml`。

### 自定义速查表

Nix 用户最常想要自定义的内容的快速参考：

| 我想要... | 选项 | 示例 |
|---|---|---|
| 更改 LLM 模型 | `settings.model.default` | `"anthropic/claude-sonnet-4"` |
| 使用不同的提供商端点 | `settings.model.base_url` | `"https://openrouter.ai/api/v1"` |
| 添加 API 密钥 | `environmentFiles` | `[ config.sops.secrets."hermes-env".path ]` |
| 给智能体一个个性 | `${services.hermes-agent.stateDir}/.hermes/SOUL.md` | 直接管理文件 |
| 添加 MCP 工具服务器 | `mcpServers.<name>` | 见 [MCP 服务器](#mcp-servers) |
| 将主机目录挂载到容器 | `container.extraVolumes` | `[ "/data:/data:rw" ]` |
| 向容器传递 GPU 访问 | `container.extraOptions` | `[ "--gpus" "all" ]` |
| 使用 Podman 代替 Docker | `container.backend` | `"podman"` |
| 在主机 CLI 和容器之间共享状态 | `container.hostUsers` | `[ "sidbin" ]` |
| 向服务 PATH 添加工具（仅原生模式） | `extraPackages` | `[ pkgs.pandoc pkgs.imagemagick ]` |
| 使用自定义基础镜像 | `container.image` | `"ubuntu:24.04"` |
| 覆盖 hermes 包 | `package` | `inputs.hermes-agent.packages.${system}.default.override { ... }` |
| 更改状态目录 | `stateDir` | `"/opt/hermes"` |
| 设置智能体的工作目录 | `workingDirectory` | `"/home/user/projects"` |

---

## 密钥管理

:::danger 永远不要在 `settings` 或 `environment` 中放置 API 密钥
Nix 表达式中的值会出现在 `/nix/store` 中，而它是全局可读的。始终使用 `environmentFiles` 配合密钥管理器。
:::

`environment`（非密钥变量）和 `environmentFiles`（密钥文件）在激活时（`nixos-rebuild switch`）合并到 `$HERMES_HOME/.env`。Hermes 在每次启动时读取此文件，因此更改通过 `systemctl restart hermes-agent` 生效——无需重新创建容器。

### sops-nix

```nix
{
  sops = {
    defaultSopsFile = ./secrets/hermes.yaml;
    age.keyFile = "/home/user/.config/sops/age/keys.txt";
    secrets."hermes-env" = { format = "yaml"; };
  };

  services.hermes-agent.environmentFiles = [
    config.sops.secrets."hermes-env".path
  ];
}
```

密钥文件包含键值对：

```yaml
# secrets/hermes.yaml（使用 sops 加密）
hermes-env: |
    OPENROUTER_API_KEY=sk-or-...
    TELEGRAM_BOT_TOKEN=123456:ABC...
    ANTHROPIC_API_KEY=sk-ant-...
```

### agenix

```nix
{
  age.secrets.hermes-env.file = ./secrets/hermes-env.age;

  services.hermes-agent.environmentFiles = [
    config.age.secrets.hermes-env.path
  ];
}
```

### OAuth / 认证种子

对于需要 OAuth 的平台（例如 Discord），使用 `authFile` 在首次部署时种子凭据：

```nix
{
  services.hermes-agent = {
    authFile = config.sops.secrets."hermes/auth.json".path;
    # authFileForceOverwrite = true;  # 每次激活时覆盖
  };
}
```

该文件仅在 `auth.json` 不存在时复制（除非 `authFileForceOverwrite = true`）。运行时 OAuth 令牌刷新写入状态目录，并在重建间保留。

---

## 文档

`documents` 选项将文件安装到智能体的工作目录（`workingDirectory`，智能体将其作为工作区读取）。Hermes 按约定查找特定文件名：

- **`USER.md`** —— 关于智能体交互的用户的上下文。
- 你放在这里的任何其他文件作为工作区文件对智能体可见。

智能体身份文件是独立的：Hermes 从 `$HERMES_HOME/SOUL.md` 加载其主要 `SOUL.md`，在 NixOS 模块中是 `${services.hermes-agent.stateDir}/.hermes/SOUL.md`。将 `SOUL.md` 放在 `documents` 中只创建一个工作区文件，不会替换主人格文件。

```nix
{
  services.hermes-agent.documents = {
    "USER.md" = ./documents/USER.md;  # 路径引用，从 Nix store 复制
  };
}
```

值可以是内联字符串或路径引用。文件在每次 `nixos-rebuild switch` 时安装。

---

## MCP 服务器

`mcpServers` 选项声明式地配置 [MCP (Model Context Protocol)](https://modelcontextprotocol.io) 服务器。每个服务器使用 **stdio**（本地命令）或 **HTTP**（远程 URL）传输。

### Stdio 传输（本地服务器）

```nix
{
  services.hermes-agent.mcpServers = {
    filesystem = {
      command = "npx";
      args = [ "-y" "@modelcontextprotocol/server-filesystem" "/data/workspace" ];
    };
    github = {
      command = "npx";
      args = [ "-y" "@modelcontextprotocol/server-github" ];
      env.GITHUB_PERSONAL_ACCESS_TOKEN = "\${GITHUB_TOKEN}"; # 从 .env 解析
    };
  };
}
```

:::tip
`env` 值中的环境变量在运行时从 `$HERMES_HOME/.env` 解析。使用 `environmentFiles` 注入密钥——永远不要在 Nix 配置中直接放置令牌。
:::

### HTTP 传输（远程服务器）

```nix
{
  services.hermes-agent.mcpServers.remote-api = {
    url = "https://mcp.example.com/v1/mcp";
    headers.Authorization = "Bearer \${MCP_REMOTE_API_KEY}";
    timeout = 180;
  };
}
```

### 使用 OAuth 的 HTTP 传输

为使用 OAuth 2.1 的服务器设置 `auth = "oauth"`。Hermes 实现了完整的 PKCE 流程——元数据发现、动态客户端注册、令牌交换和自动刷新。

```nix
{
  services.hermes-agent.mcpServers.my-oauth-server = {
    url = "https://mcp.example.com/mcp";
    auth = "oauth";
  };
}
```

令牌存储在 `$HERMES_HOME/mcp-tokens/<server-name>.json` 中，在重启和重建间持久化。

<details>
<summary><strong>无头服务器上的初始 OAuth 授权</strong></summary>

首次 OAuth 授权需要基于浏览器的同意流程。在无头部署中，Hermes 将授权 URL 打印到标准输出/日志，而不是打开浏览器。

**选项 A：交互式引导** —— 通过 `docker exec`（容器）或 `sudo -u hermes`（原生）运行一次流程：

```bash
# 容器模式
docker exec -it hermes-agent \
  hermes mcp add my-oauth-server --url https://mcp.example.com/mcp --auth oauth

# 原生模式
sudo -u hermes HERMES_HOME=/var/lib/hermes/.hermes \
  hermes mcp add my-oauth-server --url https://mcp.example.com/mcp --auth oauth
```

容器使用 `--network=host`，因此 `127.0.0.1` 上的 OAuth 回调监听器可以从主机浏览器访问。

**选项 B：预种子令牌** —— 在工作站上完成流程，然后复制令牌：

```bash
hermes mcp add my-oauth-server --url https://mcp.example.com/mcp --auth oauth
scp ~/.hermes/mcp-tokens/my-oauth-server{,.client}.json \
    server:/var/lib/hermes/.hermes/mcp-tokens/
# 确保：chown hermes:hermes, chmod 0600
```

</details>

### 采样（服务器发起的 LLM 请求）

某些 MCP 服务器可以向智能体请求 LLM 补全：

```nix
{
  services.hermes-agent.mcpServers.analysis = {
    command = "npx";
    args = [ "-y" "analysis-server" ];
    sampling = {
      enabled = true;
      model = "google/gemini-3-flash";
      max_tokens_cap = 4096;
      timeout = 30;
      max_rpm = 10;
    };
  };
}
```

---

## 托管模式

当 hermes 通过 NixOS 模块运行时，以下 CLI 命令**被阻止**，并显示指向 `configuration.nix` 的描述性错误：

| 被阻止的命令 | 原因 |
|---|---|
| `hermes setup` | 配置是声明式的——在 Nix 配置中编辑 `settings` |
| `hermes config edit` | 配置从 `settings` 生成 |
| `hermes config set <key> <value>` | 配置从 `settings` 生成 |
| `hermes gateway install` | systemd 服务由 NixOS 管理 |
| `hermes gateway uninstall` | systemd 服务由 NixOS 管理 |

这防止了 Nix 声明的内容与磁盘上的内容之间的漂移。检测使用两个信号：

1. **`HERMES_MANAGED=true`** 环境变量——由 systemd 服务设置，对网关进程可见
2. **`.managed` 标记文件** 在 `HERMES_HOME` 中——由激活脚本设置，对交互式 shell 可见（例如 `docker exec -it hermes-agent hermes config set ...` 也会被阻止）

要更改配置，编辑你的 Nix 配置并运行 `sudo nixos-rebuild switch`。

---

## 容器架构

:::info
本节仅在你使用 `container.enable = true` 时相关。原生模式部署请跳过。
:::

启用容器模式后，hermes 在持久化 Ubuntu 容器内运行，Nix 构建的二进制文件从主机只读绑定挂载：

```
主机                                    容器
────                                    ─────────
/nix/store/...-hermes-agent-0.1.0  ──►  /nix/store/... (ro)
~/.hermes -> /var/lib/hermes/.hermes       （符号链接桥接，按 hostUsers）
/var/lib/hermes/                    ──►  /data/          (rw)
  ├── current-package -> /nix/store/...    （符号链接，每次重建更新）
  ├── .gc-root -> /nix/store/...           （防止 nix-collect-garbage）
  ├── .container-identity                  （sha256 哈希，触发重新创建）
  ├── .hermes/                             （HERMES_HOME）
  │   ├── .env                             （从 environment + environmentFiles 合并）
  │   ├── config.yaml                      （Nix 生成，每次激活深度合并）
  │   ├── .managed                         （标记文件）
  │   ├── .container-mode                  （路由元数据：backend、exec_user 等）
  │   ├── state.db, sessions/, memories/   （运行时状态）
  │   └── mcp-tokens/                      （MCP 服务器的 OAuth 令牌）
  ├── home/                                ──►  /home/hermes    (rw)
  └── workspace/                           （MESSAGING_CWD）
      ├── SOUL.md                          （来自 documents 选项）
      └── （智能体创建的文件）

容器可写层（apt/pip/npm）：   /usr, /usr/local, /tmp
```

Nix 构建的二进制文件在 Ubuntu 容器内工作是因为 `/nix/store` 被绑定挂载——它自带解释器和所有依赖，因此不依赖容器的系统库。容器入口点通过 `current-package` 符号链接解析：`/data/current-package/bin/hermes gateway run --replace`。在 `nixos-rebuild switch` 时，只有符号链接被更新——容器继续运行。

### 跨事件的持久性

| 事件 | 容器重新创建？ | `/data`（状态） | `/home/hermes` | 可写层（`apt`/`pip`/`npm`） |
|---|---|---|---|---|
| `systemctl restart hermes-agent` | 否 | 持久 | 持久 | 持久 |
| `nixos-rebuild switch`（代码变更） | 否（符号链接更新） | 持久 | 持久 | 持久 |
| 主机重启 | 否 | 持久 | 持久 | 持久 |
| `nix-collect-garbage` | 否（GC root） | 持久 | 持久 | 持久 |
| 镜像变更（`container.image`） | **是** | 持久 | 持久 | **丢失** |
| 卷/选项变更 | **是** | 持久 | 持久 | **丢失** |
| `environment`/`environmentFiles` 变更 | 否 | 持久 | 持久 | 持久 |

容器仅在其**身份哈希**变化时重新创建。哈希覆盖：模式版本、镜像、`extraVolumes`、`extraOptions` 和入口脚本。环境变量、设置、文档或 hermes 包本身的更改**不会**触发重新创建。

:::warning 可写层丢失
当身份哈希变化（镜像升级、新卷、新容器选项）时，容器被销毁并从 `container.image` 的新拉取重新创建。可写层中任何 `apt install`、`pip install` 或 `npm install` 的包都会丢失。`/data` 和 `/home/hermes` 中的状态被保留（它们是绑定挂载）。

如果智能体依赖特定包，考虑将它们烘焙到自定义镜像中（`container.image = "my-registry/hermes-base:latest"`）或在智能体的 SOUL.md 中脚本化安装。
:::

### GC Root 保护

`preStart` 脚本在 `${stateDir}/.gc-root` 创建一个 GC root 指向当前 hermes 包。这防止 `nix-collect-garbage` 删除正在运行的二进制文件。如果 GC root 意外损坏，重启服务会重新创建它。

---

## 开发

### 开发 Shell

该 flake 提供一个包含 Python 3.11、uv、Node.js 和所有运行时工具的开发 shell：

```bash
cd hermes-agent
nix develop

# Shell 提供：
#   - Python 3.11 + uv（首次进入时依赖安装到 .venv）
#   - PATH 上的 Node.js 20、ripgrep、git、openssh、ffmpeg
#   - 印记文件优化：如果依赖未变更，重新进入几乎是即时的

hermes setup
hermes chat
```

### direnv（推荐）

包含的 `.envrc` 自动激活开发 shell：

```bash
cd hermes-agent
direnv allow    # 一次性操作
# 后续进入几乎是即时的（印记文件跳过依赖安装）
```

### Flake 检查

该 flake 包含在 CI 和本地运行的构建时验证：

```bash
# 运行所有检查
nix flake check

# 单个检查
nix build .#checks.x86_64-linux.package-contents   # 二进制文件存在 + 版本
nix build .#checks.x86_64-linux.entry-points-sync  # pyproject.toml 与 Nix 包同步
nix build .#checks.x86_64-linux.cli-commands        # gateway/config 子命令
nix build .#checks.x86_64-linux.managed-guard       # HERMES_MANAGED 阻止变更
nix build .#checks.x86_64-linux.bundled-skills      # 技能存在于包中
nix build .#checks.x86_64-linux.config-roundtrip    # 合并脚本保留用户键
```

<details>
<summary><strong>每个检查验证的内容</strong></summary>

| 检查 | 测试内容 |
|---|---|
| `package-contents` | `hermes` 和 `hermes-agent` 二进制文件存在且 `hermes version` 可运行 |
| `entry-points-sync` | `pyproject.toml` 中每个 `[project.scripts]` 入口在 Nix 包中都有包装二进制文件 |
| `cli-commands` | `hermes --help` 暴露 `gateway` 和 `config` 子命令 |
| `managed-guard` | `HERMES_MANAGED=true hermes config set ...` 打印 NixOS 错误 |
| `bundled-skills` | Skills 目录存在，包含 SKILL.md 文件，包装器中设置了 `HERMES_BUNDLED_SKILLS` |
| `config-roundtrip` | 7 个合并场景：全新安装、Nix 覆盖、用户键保留、混合合并、MCP 追加合并、嵌套深度合并、幂等性 |

</details>

---

## 选项参考

### 核心

| 选项 | 类型 | 默认值 | 描述 |
|---|---|---|---|
| `enable` | `bool` | `false` | 启用 hermes-agent 服务 |
| `package` | `package` | `hermes-agent` | 要使用的 hermes-agent 包 |
| `user` | `str` | `"hermes"` | 系统用户 |
| `group` | `str` | `"hermes"` | 系统组 |
| `createUser` | `bool` | `true` | 自动创建用户/组 |
| `stateDir` | `str` | `"/var/lib/hermes"` | 状态目录（`HERMES_HOME` 父目录） |
| `workingDirectory` | `str` | `"${stateDir}/workspace"` | 智能体工作目录（`MESSAGING_CWD`） |
| `addToSystemPackages` | `bool` | `false` | 将 `hermes` CLI 添加到系统 PATH 并在系统范围设置 `HERMES_HOME` |

### 配置

| 选项 | 类型 | 默认值 | 描述 |
|---|---|---|---|
| `settings` | `attrs`（深度合并） | `{}` | 声明式配置，渲染为 `config.yaml`。支持任意嵌套；多个定义通过 `lib.recursiveUpdate` 合并 |
| `configFile` | `null` 或 `path` | `null` | 现有 `config.yaml` 的路径。如设置则完全覆盖 `settings` |

### 密钥与环境

| 选项 | 类型 | 默认值 | 描述 |
|---|---|---|---|
| `environmentFiles` | `listOf str` | `[]` | 包含密钥的环境文件路径。在激活时合并到 `$HERMES_HOME/.env` |
| `environment` | `attrsOf str` | `{}` | 非密钥环境变量。**在 Nix store 中可见**——不要在此放置密钥 |
| `authFile` | `null` 或 `path` | `null` | OAuth 凭据种子。仅在首次部署时复制 |
| `authFileForceOverwrite` | `bool` | `false` | 每次激活时始终从 `authFile` 覆盖 `auth.json` |

### 文档

| 选项 | 类型 | 默认值 | 描述 |
|---|---|---|---|
| `documents` | `attrsOf (either str path)` | `{}` | 工作区文件。键是文件名，值是内联字符串或路径。在激活时安装到 `workingDirectory` |

### MCP 服务器

| 选项 | 类型 | 默认值 | 描述 |
|---|---|---|---|
| `mcpServers` | `attrsOf submodule` | `{}` | MCP 服务器定义，合并到 `settings.mcp_servers` |
| `mcpServers.<name>.command` | `null` 或 `str` | `null` | 服务器命令（stdio 传输） |
| `mcpServers.<name>.args` | `listOf str` | `[]` | 命令参数 |
| `mcpServers.<name>.env` | `attrsOf str` | `{}` | 服务器进程的环境变量 |
| `mcpServers.<name>.url` | `null` 或 `str` | `null` | 服务器端点 URL（HTTP/StreamableHTTP 传输） |
| `mcpServers.<name>.headers` | `attrsOf str` | `{}` | HTTP 头，例如 `Authorization` |
| `mcpServers.<name>.auth` | `null` 或 `"oauth"` | `null` | 认证方法。`"oauth"` 启用 OAuth 2.1 PKCE |
| `mcpServers.<name>.enabled` | `bool` | `true` | 启用或禁用此服务器 |
| `mcpServers.<name>.timeout` | `null` 或 `int` | `null` | 工具调用超时（秒）（默认：120） |
| `mcpServers.<name>.connect_timeout` | `null` 或 `int` | `null` | 连接超时（秒）（默认：60） |
| `mcpServers.<name>.tools` | `null` 或 `submodule` | `null` | 工具过滤（`include`/`exclude` 列表） |
| `mcpServers.<name>.sampling` | `null` 或 `submodule` | `null` | 服务器发起的 LLM 请求的采样配置 |

### 服务行为

| 选项 | 类型 | 默认值 | 描述 |
|---|---|---|---|
| `extraArgs` | `listOf str` | `[]` | `hermes gateway` 的额外参数 |
| `extraPackages` | `listOf package` | `[]` | 服务 PATH 上的额外包（仅原生模式） |
| `restart` | `str` | `"always"` | systemd `Restart=` 策略 |
| `restartSec` | `int` | `5` | systemd `RestartSec=` 值 |

### 容器

| 选项 | 类型 | 默认值 | 描述 |
|---|---|---|---|
| `container.enable` | `bool` | `false` | 启用 OCI 容器模式 |
| `container.backend` | `enum ["docker" "podman"]` | `"docker"` | 容器运行时 |
| `container.image` | `str` | `"ubuntu:24.04"` | 基础镜像（运行时拉取） |
| `container.extraVolumes` | `listOf str` | `[]` | 额外卷挂载（`host:container:mode`） |
| `container.extraOptions` | `listOf str` | `[]` | 传递给 `docker create` 的额外参数 |
| `container.hostUsers` | `listOf str` | `[]` | 获得 `~/.hermes` 符号链接到服务 stateDir 的交互用户，并自动添加到 `hermes` 组 |

---

## 目录布局

### 原生模式

```
/var/lib/hermes/                     # stateDir（hermes:hermes 拥有，0750）
├── .hermes/                         # HERMES_HOME
│   ├── config.yaml                  # Nix 生成（每次重建深度合并）
│   ├── .managed                     # 标记：CLI 配置变更被阻止
│   ├── .env                         # 从 environment + environmentFiles 合并
│   ├── auth.json                    # OAuth 凭据（种子后自管理）
│   ├── gateway.pid
│   ├── state.db
│   ├── mcp-tokens/                  # MCP 服务器的 OAuth 令牌
│   ├── sessions/
│   ├── memories/
│   ├── skills/
│   ├── cron/
│   └── logs/
├── home/                            # 智能体 HOME
└── workspace/                       # MESSAGING_CWD
    ├── SOUL.md                      # 来自 documents 选项
    └── （智能体创建的文件）
```

### 容器模式

相同的布局，挂载到容器中：

| 容器路径 | 主机路径 | 模式 | 说明 |
|---|---|---|---|
| `/nix/store` | `/nix/store` | `ro` | Hermes 二进制文件 + 所有 Nix 依赖 |
| `/data` | `/var/lib/hermes` | `rw` | 所有状态、配置、工作区 |
| `/home/hermes` | `${stateDir}/home` | `rw` | 持久化智能体主目录——`pip install --user`、工具缓存 |
| `/usr`、`/usr/local`、`/tmp` | （可写层） | `rw` | `apt`/`pip`/`npm` 安装——重启后持久，重新创建后丢失 |

---

## 更新

```bash
# 更新 flake 输入
nix flake update hermes-agent --flake /etc/nixos

# 重建
sudo nixos-rebuild switch
```

在容器模式中，`current-package` 符号链接被更新，智能体在重启时获取新二进制文件。不需要重新创建容器，不会丢失已安装的包。

---

## 故障排除

:::tip Podman 用户
以下所有 `docker` 命令在 `podman` 下同样有效。如果你设置了 `container.backend = "podman"`，请相应替换。
:::

### 服务日志

```bash
# 两种模式使用相同的 systemd 单元
journalctl -u hermes-agent -f

# 容器模式：也可直接查看
docker logs -f hermes-agent
```

### 容器检查

```bash
systemctl status hermes-agent
docker ps -a --filter name=hermes-agent
docker inspect hermes-agent --format='{{.State.Status}}'
docker exec -it hermes-agent bash
docker exec hermes-agent readlink /data/current-package
docker exec hermes-agent cat /data/.container-identity
```

### 强制重新创建容器

如果你需要重置可写层（全新 Ubuntu）：

```bash
sudo systemctl stop hermes-agent
docker rm -f hermes-agent
sudo rm /var/lib/hermes/.container-identity
sudo systemctl start hermes-agent
```

### 验证密钥已加载

如果智能体启动但无法与 LLM 提供商认证，检查 `.env` 文件是否正确合并：

```bash
# 原生模式
sudo -u hermes cat /var/lib/hermes/.hermes/.env

# 容器模式
docker exec hermes-agent cat /data/.hermes/.env
```

### GC Root 验证

```bash
nix-store --query --roots $(docker exec hermes-agent readlink /data/current-package)
```

### 常见问题

| 症状 | 原因 | 修复 |
|---|---|---|
| `Cannot save configuration: managed by NixOS` | CLI 保护活跃 | 编辑 `configuration.nix` 并 `nixos-rebuild switch` |
| 容器意外重新创建 | `extraVolumes`、`extraOptions` 或 `image` 变更 | 预期行为——可写层重置。重新安装包或使用自定义镜像 |
| `hermes version` 显示旧版本 | 容器未重启 | `systemctl restart hermes-agent` |
| `/var/lib/hermes` 权限被拒绝 | 状态目录是 `0750 hermes:hermes` | 使用 `docker exec` 或 `sudo -u hermes` |
| `nix-collect-garbage` 删除了 hermes | GC root 缺失 | 重启服务（preStart 重新创建 GC root） |
| `no container with name or ID "hermes-agent"`（Podman） | Podman rootful 容器对普通用户不可见 | 为 podman 添加免密 sudo（见[容器感知 CLI](#container-aware-cli) 部分） |
| `unable to find user hermes` | 容器仍在启动（入口点尚未创建用户） | 等待几秒然后重试——CLI 自动重试 |
