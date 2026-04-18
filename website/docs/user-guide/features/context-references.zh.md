---
sidebar_position: 9
sidebar_label: "上下文引用"
title: "上下文引用"
description: "内联 @-语法，用于将文件、文件夹、git diff 和 URL 直接附加到消息中"
---

# 上下文引用

输入 `@` 后跟引用，将内容直接注入你的消息。Hermes 内联展开引用并将内容附加在 `--- Attached Context ---` 部分下。

## 支持的引用

| 语法 | 描述 |
|--------|-------------|
| `@file:path/to/file.py` | 注入文件内容 |
| `@file:path/to/file.py:10-25` | 注入特定行范围（1 索引，包含） |
| `@folder:path/to/dir` | 注入目录树列表，带文件元数据 |
| `@diff` | 注入 `git diff`（未暂存的工作树更改） |
| `@staged` | 注入 `git diff --staged`（已暂存的更改） |
| `@git:5` | 注入最近 N 次提交及补丁（最多 10） |
| `@url:https://example.com` | 获取并注入网页内容 |

## 使用示例

```text
审查 @file:src/main.py 并提出改进建议

有什么变化？@diff

比较 @file:old_config.yaml 和 @file:new_config.yaml

@folder:src/components 里有什么？

总结这篇文章 @url:https://arxiv.org/abs/2301.00001
```

单条消息中可以使用多个引用：

```text
检查 @file:main.py，还有 @file:test.py。
```

尾随标点符号（`,`、`.`、`;`、`!`、`?`）会自动从引用值中去除。

## CLI Tab 补全

在交互式 CLI 中，输入 `@` 会触发自动补全：

- `@` 显示所有引用类型（`@diff`、`@staged`、`@file:`、`@folder:`、`@git:`、`@url:`）
- `@file:` 和 `@folder:` 触发带文件大小元数据的文件系统路径补全
- 仅 `@` 后跟部分文本显示当前目录中匹配的文件和文件夹

## 行范围

`@file:` 引用支持行范围用于精确内容注入：

```text
@file:src/main.py:42        # 单行 42
@file:src/main.py:10-25     # 第 10 到 25 行（包含）
```

行从 1 开始索引。无效范围会被静默忽略（返回完整文件）。

## 大小限制

上下文引用有边界以防止淹没模型的上下文窗口：

| 阈值 | 值 | 行为 |
|-----------|-------|----------|
| 软限制 | 上下文长度的 25% | 附加警告，展开继续 |
| 硬限制 | 上下文长度的 50% | 拒绝展开，返回未更改的原始消息 |
| 文件夹条目 | 最多 200 个文件 | 多余的条目替换为 `- ...` |
| Git 提交 | 最多 10 个 | `@git:N` 限制在 [1, 10] 范围 |

## 安全

### 敏感路径阻止

这些路径始终对 `@file:` 引用阻止，以防止凭据泄露：

- SSH 密钥和配置：`~/.ssh/id_rsa`、`~/.ssh/id_ed25519`、`~/.ssh/authorized_keys`、`~/.ssh/config`
- Shell 配置文件：`~/.bashrc`、`~/.zshrc`、`~/.profile`、`~/.bash_profile`、`~/.zprofile`
- 凭据文件：`~/.netrc`、`~/.pgpass`、`~/.npmrc`、`~/.pypirc`
- Hermes env：`$HERMES_HOME/.env`

这些目录被完全阻止（其中的任何文件）：
- `~/.ssh/`、`~/.aws/`、`~/.gnupg/`、`~/.kube/`、`$HERMES_HOME/skills/.hub/`

### 路径遍历保护

所有路径相对于工作目录解析。解析到允许的工作区根目录之外的引用会被拒绝。

### 二进制文件检测

通过 MIME 类型和空字节扫描检测二进制文件。已知的文本扩展名（`.py`、`.md`、`.json`、`.yaml`、`.toml`、`.js`、`.ts` 等）跳过基于 MIME 的检测。二进制文件被拒绝并附带警告。

## 平台可用性

上下文引用主要是一个 **CLI 功能**。它们在交互式 CLI 中工作，`@` 触发 tab 补全，引用在消息发送到代理之前展开。

在**消息平台**（Telegram、Discord 等）中，`@` 语法不会被网关展开 — 消息原样传递。代理本身仍然可以通过 `read_file`、`search_files` 和 `web_extract` 工具引用文件。

## 与上下文压缩的交互

当对话上下文被压缩时，展开的引用内容包含在压缩摘要中。这意味着：

- 通过 `@file:` 注入的大文件内容贡献到上下文使用量
- 如果对话后来被压缩，文件内容会被摘要（而非逐字保留）
- 对于非常大的文件，考虑使用行范围（`@file:main.py:100-200`）仅注入相关部分

## 常见模式

```text
# 代码审查工作流
审查 @diff 并检查安全问题

# 带上下文的调试
这个测试失败了。这是测试 @file:tests/test_auth.py
和实现 @file:src/auth.py:50-80

# 项目探索
这个项目做什么？@folder:src @file:README.md

# 研究
比较 @url:https://arxiv.org/abs/2301.00001
和 @url:https://arxiv.org/abs/2301.00002 中的方法
```

## 错误处理

无效的引用产生内联警告而非失败：

| 条件 | 行为 |
|-----------|----------|
| 文件未找到 | 警告："file not found" |
| 二进制文件 | 警告："binary files are not supported" |
| 文件夹未找到 | 警告："folder not found" |
| Git 命令失败 | 带 git stderr 的警告 |
| URL 没有返回内容 | 警告："no content extracted" |
| 敏感路径 | 警告："path is a sensitive credential file" |
| 路径在工作区外 | 警告："path is outside the allowed workspace" |
