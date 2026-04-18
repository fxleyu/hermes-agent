"""
Hermes Agent 卸载器。

提供以下选项：
- 完全卸载：移除所有内容，包括配置和数据
- 保留数据：移除代码但保留 ~/.hermes/（配置、会话、日志）
"""

import os
import shutil
import subprocess
from pathlib import Path

from hermes_constants import get_hermes_home

from hermes_cli.colors import Colors, color

def log_info(msg: str):
    """打印信息级别的消息。"""
    print(f"{color('→', Colors.CYAN)} {msg}")

def log_success(msg: str):
    """打印成功级别的消息。"""
    print(f"{color('✓', Colors.GREEN)} {msg}")

def log_warn(msg: str):
    """打印警告级别的消息。"""
    print(f"{color('⚠', Colors.YELLOW)} {msg}")

def get_project_root() -> Path:
    """获取项目安装目录。"""
    return Path(__file__).parent.parent.resolve()


def find_shell_configs() -> list:
    """查找可能包含 PATH 条目的 shell 配置文件。"""
    home = Path.home()
    configs = []

    candidates = [
        home / ".bashrc",
        home / ".bash_profile",
        home / ".profile",
        home / ".zshrc",
        home / ".zprofile",
    ]

    for config in candidates:
        if config.exists():
            configs.append(config)

    return configs


def remove_path_from_shell_configs():
    """从 shell 配置文件中移除 Hermes 的 PATH 条目。"""
    configs = find_shell_configs()
    removed_from = []

    for config_path in configs:
        try:
            content = config_path.read_text()
            original_content = content

            # 移除包含 hermes-agent 或 hermes PATH 条目的行
            new_lines = []
            skip_next = False

            for line in content.split('\n'):
                # 跳过 "# Hermes Agent" 注释及其下一行
                if '# Hermes Agent' in line or '# hermes-agent' in line:
                    skip_next = True
                    continue
                if skip_next and ('hermes' in line.lower() and 'PATH' in line):
                    skip_next = False
                    continue
                skip_next = False

                # 移除任何包含 hermes 的 PATH 行
                if 'hermes' in line.lower() and ('PATH=' in line or 'path=' in line.lower()):
                    continue

                new_lines.append(line)

            new_content = '\n'.join(new_lines)

            # 清理多余的空行
            while '\n\n\n' in new_content:
                new_content = new_content.replace('\n\n\n', '\n\n')

            if new_content != original_content:
                config_path.write_text(new_content)
                removed_from.append(config_path)

        except Exception as e:
            log_warn(f"Could not update {config_path}: {e}")

    return removed_from


def remove_wrapper_script():
    """移除 hermes 包装脚本（如果存在）。"""
    wrapper_paths = [
        Path.home() / ".local" / "bin" / "hermes",
        Path("/usr/local/bin/hermes"),
    ]

    removed = []
    for wrapper in wrapper_paths:
        if wrapper.exists():
            try:
                # 检查是否是我们的包装脚本（包含 hermes_cli 引用）
                content = wrapper.read_text()
                if 'hermes_cli' in content or 'hermes-agent' in content:
                    wrapper.unlink()
                    removed.append(wrapper)
            except Exception as e:
                log_warn(f"Could not remove {wrapper}: {e}")

    return removed


def uninstall_gateway_service():
    """停止并卸载网关服务（如果正在运行）。"""
    import platform

    if platform.system() != "Linux":
        return False

    # Termux 环境不使用 systemd
    prefix = os.getenv("PREFIX", "")
    if os.getenv("TERMUX_VERSION") or "com.termux/files/usr" in prefix:
        return False

    try:
        from hermes_cli.gateway import get_service_name
        svc_name = get_service_name()
    except Exception:
        svc_name = "hermes-gateway"

    service_file = Path.home() / ".config" / "systemd" / "user" / f"{svc_name}.service"

    if not service_file.exists():
        return False

    try:
        # 停止服务
        subprocess.run(
            ["systemctl", "--user", "stop", svc_name],
            capture_output=True,
            check=False
        )

        # 禁用服务
        subprocess.run(
            ["systemctl", "--user", "disable", svc_name],
            capture_output=True,
            check=False
        )

        # 删除服务文件
        service_file.unlink()

        # 重新加载 systemd 配置
        subprocess.run(
            ["systemctl", "--user", "daemon-reload"],
            capture_output=True,
            check=False
        )

        return True

    except Exception as e:
        log_warn(f"Could not fully remove gateway service: {e}")
        return False


def run_uninstall(args):
    """
    执行卸载流程。

    选项：
    - 完全卸载：移除代码 + ~/.hermes/（配置、数据、日志）
    - 保留数据：移除代码但保留 ~/.hermes/ 以便将来重新安装
    """
    project_root = get_project_root()
    hermes_home = get_hermes_home()

    print()
    print(color("┌─────────────────────────────────────────────────────────┐", Colors.MAGENTA, Colors.BOLD))
    print(color("│            ⚕ Hermes Agent Uninstaller                  │", Colors.MAGENTA, Colors.BOLD))
    print(color("└─────────────────────────────────────────────────────────┘", Colors.MAGENTA, Colors.BOLD))
    print()

    # 显示将受影响的内容
    print(color("Current Installation:", Colors.CYAN, Colors.BOLD))
    print(f"  Code:    {project_root}")
    print(f"  Config:  {hermes_home / 'config.yaml'}")
    print(f"  Secrets: {hermes_home / '.env'}")
    print(f"  Data:    {hermes_home / 'cron/'}, {hermes_home / 'sessions/'}, {hermes_home / 'logs/'}")
    print()

    # 请求用户确认
    print(color("Uninstall Options:", Colors.YELLOW, Colors.BOLD))
    print()
    print("  1) " + color("Keep data", Colors.GREEN) + " - Remove code only, keep configs/sessions/logs")
    print("     (Recommended - you can reinstall later with your settings intact)")
    print()
    print("  2) " + color("Full uninstall", Colors.RED) + " - Remove everything including all data")
    print("     (Warning: This deletes all configs, sessions, and logs permanently)")
    print()
    print("  3) " + color("Cancel", Colors.CYAN) + " - Don't uninstall")
    print()

    try:
        choice = input(color("Select option [1/2/3]: ", Colors.BOLD)).strip()
    except (KeyboardInterrupt, EOFError):
        print()
        print("Cancelled.")
        return

    if choice == "3" or choice.lower() in ("c", "cancel", "q", "quit", "n", "no"):
        print()
        print("Uninstall cancelled.")
        return

    full_uninstall = (choice == "2")

    # 最终确认
    print()
    if full_uninstall:
        print(color("⚠️  WARNING: This will permanently delete ALL Hermes data!", Colors.RED, Colors.BOLD))
        print(color("   Including: configs, API keys, sessions, scheduled jobs, logs", Colors.RED))
    else:
        print("This will remove the Hermes code but keep your configuration and data.")

    print()
    try:
        confirm = input(f"Type '{color('yes', Colors.YELLOW)}' to confirm: ").strip().lower()
    except (KeyboardInterrupt, EOFError):
        print()
        print("Cancelled.")
        return

    if confirm != "yes":
        print()
        print("Uninstall cancelled.")
        return

    print()
    print(color("Uninstalling...", Colors.CYAN, Colors.BOLD))
    print()

    # 1. 停止并卸载网关服务
    log_info("Checking for gateway service...")
    if uninstall_gateway_service():
        log_success("Gateway service stopped and removed")
    else:
        log_info("No gateway service found")

    # 2. 从 shell 配置中移除 PATH 条目
    log_info("Removing PATH entries from shell configs...")
    removed_configs = remove_path_from_shell_configs()
    if removed_configs:
        for config in removed_configs:
            log_success(f"Updated {config}")
    else:
        log_info("No PATH entries found to remove")

    # 3. 移除包装脚本
    log_info("Removing hermes command...")
    removed_wrappers = remove_wrapper_script()
    if removed_wrappers:
        for wrapper in removed_wrappers:
            log_success(f"Removed {wrapper}")
    else:
        log_info("No wrapper script found")

    # 4. 移除安装目录（代码）
    log_info("Removing installation directory...")

    # 检查是否从安装目录内部运行，需要小心处理
    try:
        if project_root.exists():
            # 如果安装在 ~/.hermes/ 内部，只移除 hermes-agent 子目录
            if hermes_home in project_root.parents or project_root.parent == hermes_home:
                shutil.rmtree(project_root)
                log_success(f"Removed {project_root}")
            else:
                # 安装在其他位置
                shutil.rmtree(project_root)
                log_success(f"Removed {project_root}")
    except Exception as e:
        log_warn(f"Could not fully remove {project_root}: {e}")
        log_info("You may need to manually remove it")

    # 5. 可选：移除 ~/.hermes/ 数据目录
    if full_uninstall:
        log_info("Removing configuration and data...")
        try:
            if hermes_home.exists():
                shutil.rmtree(hermes_home)
                log_success(f"Removed {hermes_home}")
        except Exception as e:
            log_warn(f"Could not fully remove {hermes_home}: {e}")
            log_info("You may need to manually remove it")
    else:
        log_info(f"Keeping configuration and data in {hermes_home}")

    # 完成
    print()
    print(color("┌─────────────────────────────────────────────────────────┐", Colors.GREEN, Colors.BOLD))
    print(color("│              ✓ Uninstall Complete!                      │", Colors.GREEN, Colors.BOLD))
    print(color("└─────────────────────────────────────────────────────────┘", Colors.GREEN, Colors.BOLD))
    print()

    if not full_uninstall:
        print(color("Your configuration and data have been preserved:", Colors.CYAN))
        print(f"  {hermes_home}/")
        print()
        print("To reinstall later with your existing settings:")
        print(color("  curl -fsSL https://raw.githubusercontent.com/NousResearch/hermes-agent/main/scripts/install.sh | bash", Colors.DIM))
        print()

    print(color("Reload your shell to complete the process:", Colors.YELLOW))
    print("  source ~/.bashrc  # or ~/.zshrc")
    print()
    print("Thank you for using Hermes Agent! ⚕")
    print()
