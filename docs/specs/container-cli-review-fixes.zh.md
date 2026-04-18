# 容器感知 CLI 审查修复规范

**PR：** NousResearch/hermes-agent#7543
**审查：** cursor[bot] bugbot review (4094049442) + 两轮先前审查
**日期：** 2026-04-12
**分支：** `feat/container-aware-cli-clean`

## 审查问题摘要

三轮 bugbot 审查中共提出了六个问题。其中三个已在中间提交中修复（38277a6a、726cf90f）。本规范解决那些审查中暴露的剩余设计问题，并基于访谈决策简化实现。

| # | 问题 | 严重性 | 状态 |
|---|------|--------|------|
| 1 | `os.execvp` 重试循环不可达 | 中 | 已在 79e8cd12 中修复（切换到 subprocess.run） |
| 2 | 冗余的 `shutil.which("sudo")` | 中 | 已在 38277a6a 中修复（复用 `sudo` 变量） |
| 3 | 符号链接更新缺少 `chown -h` | 低 | 已在 38277a6a 中修复 |
| 4 | `parse_args()` 之后的容器路由 | 高 | 已在 726cf90f 中修复 |
| 5 | 硬编码 `/home/${user}` | 中 | 已在 726cf90f 中修复 |
| 6 | 组成员资格未受 `container.enable` 约束 | 低 | 已在 726cf90f 中修复 |

机械性修复已到位，但整体设计需要修订。重试循环、错误吞没和进程模型存在比 bugbot 标记的更深层次的问题。

---

## 规范：修订后的 `_exec_in_container`

### 设计原则

1. **让它崩溃。** 没有静默回退。如果 `.container-mode` 存在但出现问题，错误会自然传播（Python 回溯）。唯一跳过容器路由的情况是 `.container-mode` 不存在或 `HERMES_DEV=1`。
2. **不重试。** 探测一次 sudo，执行一次。如果失败，docker/podman 的 stderr 直接传递给用户。
3. **完全透明。** 没有错误包装，没有前缀，没有加载动画。Docker 的输出直接透传。
4. **在正常路径上使用 `os.execvp`。** 完全替换 Python 进程，这样在交互式会话期间不会有空闲的父进程。注意：`execvp` 在成功时永远不会返回（进程被替换），失败时抛出 `OSError`（不返回值）。容器进程的退出码按定义成为进程退出码——不需要显式传播。
5. **一个人类可读的"让它崩溃"例外。** sudo 探测产生的 `subprocess.TimeoutExpired` 会被特定捕获并附带可读消息，因为"您的 Docker 守护进程很慢"的原始回溯令人困惑。所有其他异常自然传播。

### 执行流程

```
1. get_container_exec_info()
   - HERMES_DEV=1 → 返回 None（跳过路由）
   - 在容器内部 → 返回 None（跳过路由）
   - .container-mode 不存在 → 返回 None（跳过路由）
   - .container-mode 存在 → 解析并返回字典
   - .container-mode 存在但格式错误/不可读 → 让它崩溃（不加 try/except）

2. _exec_in_container(container_info, sys.argv[1:])
   a. shutil.which(backend) → 如果为 None，打印 "{backend} not found on PATH" 并 sys.exit(1)
   b. Sudo 探测：subprocess.run([runtime, "inspect", "--format", "ok", container_name], timeout=15)
      - 如果成功 → needs_sudo = False
      - 如果失败 → 尝试 subprocess.run([sudo, "-n", runtime, "inspect", ...], timeout=15)
        - 如果成功 → needs_sudo = True
        - 如果失败 → 打印带 sudoers 提示的错误（包括为什么需要 -n）并 sys.exit(1)
      - 如果 TimeoutExpired → 特定捕获，打印关于守护进程缓慢的人类可读消息
   c. 构建 exec_cmd：[sudo? + runtime, "exec", tty_flags, "-u", exec_user, env_flags, container, hermes_bin, *cli_args]
   d. os.execvp(exec_cmd[0], exec_cmd)
      - 成功时：进程被替换——Python 消失，容器退出码就是进程退出码
      - OSError 时：让它崩溃（自然回溯）
```

### `hermes_cli/main.py` 的变更

#### `_exec_in_container` — 重写

移除：
- 整个重试循环（`max_retries`、`for attempt in range(...)`）
- 加载动画逻辑（`"Waiting for container..."`、圆点）
- 退出码分类（125/126/127 处理）
- exec 调用的 `subprocess.run`（仅保留用于 sudo 探测）
- 特殊的 TTY vs 非 TTY 重试计数
- `time` 导入（不再需要）

变更：
- 使用 `os.execvp(exec_cmd[0], exec_cmd)` 作为最终调用
- 仅保留 `subprocess` 导入用于 sudo 探测
- 保留 TTY 检测用于 `-it` vs `-i` 标志
- 保留环境变量转发（TERM、COLORTERM、LANG、LC_ALL）
- 保留 sudo 探测原样（它是唯一的"智能"部分）
- 将探测 `timeout` 从 5s 提升到 15s——负载较大的机器上冷启动 podman 需要额外空间
- 在两次探测调用上特定捕获 `subprocess.TimeoutExpired`——打印关于守护进程无响应的可读消息，而非原始回溯
- 扩展 sudoers 提示错误消息，解释*为什么*需要 `-n`（非交互式）：密码提示会挂起 CLI 或破坏管道命令

函数大致变为：

```python
def _exec_in_container(container_info: dict, cli_args: list):
    """将当前进程替换为在受管容器内执行的命令。

    探测是否需要 sudo（rootful 容器），然后 os.execvp
    进入容器。如果 exec 失败，OS 错误会自然传播。
    """
    import shutil
    import subprocess

    backend = container_info["backend"]
    container_name = container_info["container_name"]
    exec_user = container_info["exec_user"]
    hermes_bin = container_info["hermes_bin"]

    runtime = shutil.which(backend)
    if not runtime:
        print(f"Error: {backend} not found on PATH. Cannot route to container.",
              file=sys.stderr)
        sys.exit(1)

    # 探测是否需要 sudo 来查看 rootful 容器。
    # 超时为 15s——负载较大的机器上冷启动 podman 可能需要一段时间。
    # TimeoutExpired 被特定捕获以提供人类可读消息；
    # 所有其他异常自然传播。
    needs_sudo = False
    sudo = None
    try:
        probe = subprocess.run(
            [runtime, "inspect", "--format", "ok", container_name],
            capture_output=True, text=True, timeout=15,
        )
    except subprocess.TimeoutExpired:
        print(
            f"Error: timed out waiting for {backend} to respond.\n"
            f"The {backend} daemon may be unresponsive or starting up.",
            file=sys.stderr,
        )
        sys.exit(1)

    if probe.returncode != 0:
        sudo = shutil.which("sudo")
        if sudo:
            try:
                probe2 = subprocess.run(
                    [sudo, "-n", runtime, "inspect", "--format", "ok", container_name],
                    capture_output=True, text=True, timeout=15,
                )
            except subprocess.TimeoutExpired:
                print(
                    f"Error: timed out waiting for sudo {backend} to respond.",
                    file=sys.stderr,
                )
                sys.exit(1)

            if probe2.returncode == 0:
                needs_sudo = True
            else:
                print(
                    f"Error: container '{container_name}' not found via {backend}.\n"
                    f"\n"
                    f"The NixOS service runs the container as root. Your user cannot\n"
                    f"see it because {backend} uses per-user namespaces.\n"
                    f"\n"
                    f"Fix: grant passwordless sudo for {backend}. The -n (non-interactive)\n"
                    f"flag is required because the CLI calls sudo non-interactively —\n"
                    f"a password prompt would hang or break piped commands:\n"
                    f"\n"
                    f'  security.sudo.extraRules = [{{\n'
                    f'    users = [ "{os.getenv("USER", "your-user")}" ];\n'
                    f'    commands = [{{ command = "{runtime}"; options = [ "NOPASSWD" ]; }}];\n'
                    f'  }}];\n'
                    f"\n"
                    f"Or run: sudo hermes {' '.join(cli_args)}",
                    file=sys.stderr,
                )
                sys.exit(1)
        else:
            print(
                f"Error: container '{container_name}' not found via {backend}.\n"
                f"The container may be running under root. Try: sudo hermes {' '.join(cli_args)}",
                file=sys.stderr,
            )
            sys.exit(1)

    is_tty = sys.stdin.isatty()
    tty_flags = ["-it"] if is_tty else ["-i"]

    env_flags = []
    for var in ("TERM", "COLORTERM", "LANG", "LC_ALL"):
        val = os.environ.get(var)
        if val:
            env_flags.extend(["-e", f"{var}={val}"])

    cmd_prefix = [sudo, "-n", runtime] if needs_sudo else [runtime]
    exec_cmd = (
        cmd_prefix + ["exec"]
        + tty_flags
        + ["-u", exec_user]
        + env_flags
        + [container_name, hermes_bin]
        + cli_args
    )

    # execvp 完全替换此进程——成功时永远不会返回。
    # 失败时抛出 OSError，自然传播。
    os.execvp(exec_cmd[0], exec_cmd)
```

#### `main()` 中的容器路由调用点 — 移除 try/except

当前：
```python
try:
    from hermes_cli.config import get_container_exec_info
    container_info = get_container_exec_info()
    if container_info:
        _exec_in_container(container_info, sys.argv[1:])
        sys.exit(1)  # 如果到达这里说明 exec 失败
except SystemExit:
    raise
except Exception:
    pass  # 容器路由不可用，在本地继续
```

修订后：
```python
from hermes_cli.config import get_container_exec_info
container_info = get_container_exec_info()
if container_info:
    _exec_in_container(container_info, sys.argv[1:])
    # 不可达：os.execvp 成功时永远不返回（进程被替换）
    # 失败时抛出 OSError（作为回溯传播）。
    # 这行仅作为防御性断言存在。
    sys.exit(1)
```

没有 try/except。如果 `.container-mode` 不存在，`get_container_exec_info()` 返回 `None`，我们跳过路由。如果它存在但损坏，异常以自然回溯传播。

注意：`_exec_in_container` 之后的 `sys.exit(1)` 在所有路径中都是死代码——`os.execvp` 要么替换进程要么抛出异常。它作为带注释标记为不可达的保险断言保留，而非实际的错误处理。

### `hermes_cli/config.py` 的变更

#### `get_container_exec_info` — 移除内部 try/except

当前代码捕获 `(OSError, IOError)` 并返回 `None`。这会静默隐藏权限错误、损坏文件等。

变更：移除文件读取周围的 try/except。保留 `HERMES_DEV=1` 和 `_is_inside_container()` 的提前返回。当 `.container-mode` 不存在时 `open()` 产生的 `FileNotFoundError` 仍应返回 `None`（这是"容器模式未启用"的情况）。所有其他异常传播。

```python
def get_container_exec_info() -> Optional[dict]:
    if os.environ.get("HERMES_DEV") == "1":
        return None
    if _is_inside_container():
        return None

    container_mode_file = get_hermes_home() / ".container-mode"

    try:
        with open(container_mode_file, "r") as f:
            # ... 解析 key=value 行 ...
    except FileNotFoundError:
        return None
    # 所有其他异常（PermissionError、格式错误数据等）传播

    return { ... }
```

---

## 规范：NixOS 模块变更

### 符号链接创建 — 简化为两个分支

当前：4 个分支（符号链接存在、目录存在、其他文件、不存在）。

修订后：2 个分支。

```bash
if [ -d "${symlinkPath}" ] && [ ! -L "${symlinkPath}" ]; then
  # 真实目录——备份它，然后创建符号链接
  _backup="${symlinkPath}.bak.$(date +%s)"
  echo "hermes-agent: backing up existing ${symlinkPath} to $_backup"
  mv "${symlinkPath}" "$_backup"
fi
# 对于其他一切（符号链接、不存在等）——直接强制创建
ln -sfn "${target}" "${symlinkPath}"
chown -h ${user}:${cfg.group} "${symlinkPath}"
```

`ln -sfn` 处理：现有符号链接（替换）、不存在（创建）以及上面 `mv` 之后（创建）。唯一需要特殊处理的情况是真实目录，因为 `ln -sfn` 无法原子地替换目录。

注意：`[ -d ... ]` 检查和 `mv` 之间存在理论上的竞态条件（其间可能有东西创建/删除目录）。在实践中这是一个 NixOS 激活脚本，在 `nixos-rebuild switch` 期间以 root 运行——此时不应有其他进程接触 `~/.hermes`。不值得添加锁机制。

### Sudoers — 记录，不自动配置

不要在模块中添加 `security.sudo.extraRules`。在模块的描述/注释中以及 CLI 打印的 sudo 探测失败错误消息中记录 sudoers 要求。

### 组成员资格约束 — 保持原样

726cf90f 中的修复（`cfg.container.enable && cfg.container.hostUsers != []`）是正确的。容器模式禁用时遗留的组成员资格是无害的。不需要清理。

---

## 规范：测试重写

现有测试文件（`tests/hermes_cli/test_container_aware_cli.py`）有 16 个测试。由于简化了 exec 模型，其中几个已过时。

### 保留的测试（按需更新）

- `test_is_inside_container_dockerenv` — 不变
- `test_is_inside_container_containerenv` — 不变
- `test_is_inside_container_cgroup_docker` — 不变
- `test_is_inside_container_false_on_host` — 不变
- `test_get_container_exec_info_returns_metadata` — 不变
- `test_get_container_exec_info_none_inside_container` — 不变
- `test_get_container_exec_info_none_without_file` — 不变
- `test_get_container_exec_info_skipped_when_hermes_dev` — 不变
- `test_get_container_exec_info_not_skipped_when_hermes_dev_zero` — 不变
- `test_get_container_exec_info_defaults` — 不变
- `test_get_container_exec_info_docker_backend` — 不变

### 需要添加的测试

- `test_get_container_exec_info_crashes_on_permission_error` — 验证 `PermissionError` 传播（不静默返回 `None`）
- `test_exec_in_container_calls_execvp` — 验证 `os.execvp` 以正确参数调用（runtime、tty 标志、用户、环境、容器、二进制文件、CLI 参数）
- `test_exec_in_container_sudo_probe_sets_prefix` — 验证当第一次探测失败且 sudo 探测成功时，`os.execvp` 以 `sudo -n` 前缀调用
- `test_exec_in_container_no_runtime_hard_fails` — 保留现有，验证 `shutil.which` 返回 None 时 `sys.exit(1)`
- `test_exec_in_container_non_tty_uses_i_only` — 更新为检查 `os.execvp` 参数而非 `subprocess.run` 参数
- `test_exec_in_container_probe_timeout_prints_message` — 验证探测产生的 `subprocess.TimeoutExpired` 产生人类可读错误和 `sys.exit(1)`，而非原始回溯
- `test_exec_in_container_container_not_running_no_sudo` — 验证 runtime 存在（`shutil.which` 返回路径）但探测返回非零且没有可用 sudo 的路径。应打印"container may be running under root"错误。这与覆盖 `shutil.which` 返回 None 的 `no_runtime_hard_fails` 不同。

### 需要删除的测试

- `test_exec_in_container_tty_retries_on_container_failure` — 重试循环已移除
- `test_exec_in_container_non_tty_retries_silently_exits_126` — 重试循环已移除
- `test_exec_in_container_propagates_hermes_exit_code` — 没有 subprocess.run 可检查退出码；execvp 替换进程。注意：退出码传播仍然正确工作——当 `os.execvp` 成功时，容器的进程*变为*此进程，因此它的退出码按操作系统语义就是进程退出码。不需要应用程序代码，不需要测试。函数文档字符串中的注释为未来读者记录了此意图。

---

## 范围之外

- 在 NixOS 模块中自动配置 sudoers 规则
- 除了 try/except 收窄之外的 `get_container_exec_info` 解析逻辑变更
- `.container-mode` 文件格式变更
- `HERMES_DEV=1` 绕过的变更
- 容器检测逻辑（`_is_inside_container`）的变更
