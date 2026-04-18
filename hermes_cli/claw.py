"""hermes claw —— OpenClaw 迁移命令。

用法:
    hermes claw migrate              # 先预览再迁移（总是先显示预览）
    hermes claw migrate --dry-run    # 仅预览，不做任何更改
    hermes claw migrate --yes        # 跳过确认提示
    hermes claw migrate --preset full --overwrite  # 完整迁移，覆盖冲突项
    hermes claw cleanup              # 归档残留的 OpenClaw 目录
    hermes claw cleanup --dry-run    # 预览将被归档的内容
"""

import importlib.util
import logging
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from hermes_cli.config import get_hermes_home, get_config_path, load_config, save_config
from hermes_constants import get_optional_skills_dir
from hermes_cli.setup import (
    Colors,
    color,
    print_header,
    print_info,
    print_success,
    print_error,
    prompt_yes_no,
)

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).parent.parent.resolve()

# 迁移脚本路径：优先查找可选技能目录中的脚本
_OPENCLAW_SCRIPT = (
    get_optional_skills_dir(PROJECT_ROOT / "optional-skills")
    / "migration"
    / "openclaw-migration"
    / "scripts"
    / "openclaw_to_hermes.py"
)

# 备选路径：用户可能已从 Hub 安装了该技能
_OPENCLAW_SCRIPT_INSTALLED = (
    get_hermes_home()
    / "skills"
    / "migration"
    / "openclaw-migration"
    / "scripts"
    / "openclaw_to_hermes.py"
)

# 已知的 OpenClaw 目录名称（当前版本 + 历史版本）
_OPENCLAW_DIR_NAMES = (".openclaw", ".clawdbot", ".moltbot")

def _detect_openclaw_processes() -> list[str]:
    """检测正在运行的 OpenClaw 进程和服务。

    返回检测到的内容的人类可读描述列表。
    空列表表示未检测到任何内容。
    """
    found: list[str] = []

    # -- systemd 服务（Linux）------------------------------------------
    if sys.platform != "win32":
        try:
            result = subprocess.run(
                ["systemctl", "--user", "is-active", "openclaw-gateway.service"],
                capture_output=True, text=True, timeout=5,
            )
            if result.stdout.strip() == "active":
                found.append("systemd service: openclaw-gateway.service")
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass

    # -- 进程扫描 ------------------------------------------------------
    if sys.platform == "win32":
        try:
            for exe in ("openclaw.exe", "clawd.exe"):
                result = subprocess.run(
                    ["tasklist", "/FI", f"IMAGENAME eq {exe}"],
                    capture_output=True, text=True, timeout=5,
                )
                if exe in result.stdout.lower():
                    found.append(f"process: {exe}")

            # Node.js 托管的 OpenClaw —— tasklist 不显示命令行参数，
            # 因此回退到 PowerShell。
            ps_cmd = (
                'Get-CimInstance Win32_Process -Filter "Name = \'node.exe\'" | '
                'Where-Object { $_.CommandLine -match "openclaw|clawd" } | '
                'Select-Object -First 1 ProcessId'
            )
            result = subprocess.run(
                ["powershell", "-NoProfile", "-Command", ps_cmd],
                capture_output=True, text=True, timeout=5,
            )
            if result.stdout.strip():
                found.append(f"node.exe process with openclaw in command line (PID {result.stdout.strip()})")
        except Exception:
            pass
    else:
        try:
            result = subprocess.run(
                ["pgrep", "-f", "openclaw"],
                capture_output=True, text=True, timeout=3,
            )
            if result.returncode == 0:
                pids = result.stdout.strip().split()
                found.append(f"openclaw process(es) (PIDs: {', '.join(pids)})")
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass

    return found


def _warn_if_openclaw_running(auto_yes: bool) -> None:
    """迁移前警告 OpenClaw 是否仍在运行。

    Telegram、Discord 和 Slack 每个 bot token 只允许一个活跃连接。
    在 OpenClaw 运行期间迁移会导致两者争夺同一个 token。
    """
    running = _detect_openclaw_processes()
    if not running:
        return

    print()
    print_error("OpenClaw appears to be running:")
    for detail in running:
        print_info(f"  * {detail}")
    print_info(
        "Messaging platforms (Telegram, Discord, Slack) only allow one "
        "active session per bot token. If you continue, both OpenClaw and "
        "Hermes may try to use the same token, causing disconnects."
    )
    print_info("Recommendation: stop OpenClaw before migrating.")
    print()
    if auto_yes:
        return
    if not sys.stdin.isatty():
        print_info("Non-interactive session — continuing to preview only.")
        return
    if not prompt_yes_no("Continue anyway?", default=False):
        print_info("Migration cancelled. Stop OpenClaw and try again.")
        sys.exit(0)


def _warn_if_gateway_running(auto_yes: bool) -> None:
    """检查 Hermes 网关是否正在运行并连接了平台。

    在网关正在轮询时迁移 bot token 会导致冲突
    （例如 Telegram 409 "terminated by other getUpdates request"）。
    警告用户并让他们决定是否继续。
    """
    from gateway.status import get_running_pid, read_runtime_status

    if not get_running_pid():
        return

    data = read_runtime_status() or {}
    platforms = data.get("platforms") or {}
    # 找出所有状态为 "connected" 的平台
    connected = [name for name, info in platforms.items()
                 if isinstance(info, dict) and info.get("state") == "connected"]
    if not connected:
        return

    print()
    print_error(
        "Hermes gateway is running with active connections: "
        + ", ".join(connected)
    )
    print_info(
        "Migrating bot tokens while the gateway is active will cause "
        "conflicts (Telegram, Discord, and Slack only allow one active "
        "session per token)."
    )
    print_info("Recommendation: stop the gateway first with 'hermes stop'.")
    print()
    if not auto_yes and not prompt_yes_no("Continue anyway?", default=False):
        print_info("Migration cancelled. Stop the gateway and try again.")
        sys.exit(0)

# OpenClaw 工作区目录中常见的状态文件 —— 清理时列出以帮助用户
# 决定是否归档
_WORKSPACE_STATE_GLOBS = (
    "*/todo.json",
    "*/sessions/*",
    "*/memory/*.json",
    "*/logs/*",
)


def _find_migration_script() -> Path | None:
    """在已知位置查找 openclaw_to_hermes.py 迁移脚本。"""
    for candidate in [_OPENCLAW_SCRIPT, _OPENCLAW_SCRIPT_INSTALLED]:
        if candidate.exists():
            return candidate
    return None


def _load_migration_module(script_path: Path):
    """以模块方式动态加载迁移脚本。"""
    spec = importlib.util.spec_from_file_location("openclaw_to_hermes", script_path)
    if spec is None or spec.loader is None:
        return None
    mod = importlib.util.module_from_spec(spec)
    # 注册到 sys.modules 以便 @dataclass 能正确解析模块
    # （Python 3.11+ 对动态加载的模块有此要求）
    sys.modules[spec.name] = mod
    try:
        spec.loader.exec_module(mod)
    except Exception:
        sys.modules.pop(spec.name, None)
        raise
    return mod


def _find_openclaw_dirs() -> list[Path]:
    """查找磁盘上所有的 OpenClaw 目录。"""
    found = []
    for name in _OPENCLAW_DIR_NAMES:
        candidate = Path.home() / name
        if candidate.is_dir():
            found.append(candidate)
    return found


def _scan_workspace_state(source_dir: Path) -> list[tuple[Path, str]]:
    """扫描 OpenClaw 目录中的工作区状态文件。

    返回 (路径, 描述) 元组列表。
    """
    findings: list[tuple[Path, str]] = []

    # 根目录中的直接状态文件
    for name in ("todo.json", "sessions", "logs"):
        candidate = source_dir / name
        if candidate.exists():
            kind = "directory" if candidate.is_dir() else "file"
            findings.append((candidate, f"Root {kind}: {name}"))

    # 工作区子目录中的状态文件
    for child in sorted(source_dir.iterdir()):
        if not child.is_dir() or child.name.startswith("."):
            continue
        # 检查类似工作区的子目录
        for state_name in ("todo.json", "sessions", "logs", "memory"):
            state_path = child / state_name
            if state_path.exists():
                kind = "directory" if state_path.is_dir() else "file"
                rel = state_path.relative_to(source_dir)
                findings.append((state_path, f"Workspace {kind}: {rel}"))

    return findings


def _archive_directory(source_dir: Path, dry_run: bool = False) -> Path:
    """将 OpenClaw 目录重命名为 .pre-migration。

    返回归档路径。
    """
    timestamp = datetime.now().strftime("%Y%m%d")
    archive_name = f"{source_dir.name}.pre-migration"
    archive_path = source_dir.parent / archive_name

    # 如果归档目录已存在，添加时间戳
    if archive_path.exists():
        archive_name = f"{source_dir.name}.pre-migration-{timestamp}"
        archive_path = source_dir.parent / archive_name

    # 如果仍然存在（同一天多次运行），添加计数器
    counter = 2
    while archive_path.exists():
        archive_name = f"{source_dir.name}.pre-migration-{timestamp}-{counter}"
        archive_path = source_dir.parent / archive_name
        counter += 1

    if not dry_run:
        source_dir.rename(archive_path)

    return archive_path


def claw_command(args):
    """路由 hermes claw 子命令。"""
    action = getattr(args, "claw_action", None)

    if action == "migrate":
        _cmd_migrate(args)
    elif action in ("cleanup", "clean"):
        _cmd_cleanup(args)
    else:
        print("Usage: hermes claw <command> [options]")
        print()
        print("Commands:")
        print("  migrate          Migrate settings from OpenClaw to Hermes")
        print("  cleanup          Archive leftover OpenClaw directories after migration")
        print()
        print("Run 'hermes claw <command> --help' for options.")


def _cmd_migrate(args):
    """执行 OpenClaw → Hermes 迁移。"""
    # 检查当前和历史的 OpenClaw 目录
    explicit_source = getattr(args, "source", None)
    if explicit_source:
        source_dir = Path(explicit_source)
    else:
        source_dir = Path.home() / ".openclaw"
        if not source_dir.is_dir():
            # 尝试历史版本的目录名
            for legacy in (".clawdbot", ".moltbot"):
                candidate = Path.home() / legacy
                if candidate.is_dir():
                    source_dir = candidate
                    break
    dry_run = getattr(args, "dry_run", False)
    preset = getattr(args, "preset", "full")
    overwrite = getattr(args, "overwrite", False)
    migrate_secrets = getattr(args, "migrate_secrets", False)
    workspace_target = getattr(args, "workspace_target", None)
    skill_conflict = getattr(args, "skill_conflict", "skip")

    # 使用 "full" 预设时，默认包含密钥迁移
    if preset == "full":
        migrate_secrets = True

    print()
    print(
        color(
            "┌─────────────────────────────────────────────────────────┐",
            Colors.MAGENTA,
        )
    )
    print(
        color(
            "│          ⚕ Hermes — OpenClaw Migration                 │",
            Colors.MAGENTA,
        )
    )
    print(
        color(
            "└─────────────────────────────────────────────────────────┘",
            Colors.MAGENTA,
        )
    )

    # 检查源目录是否存在
    if not source_dir.is_dir():
        print()
        print_error(f"OpenClaw directory not found: {source_dir}")
        print_info("Make sure your OpenClaw installation is at the expected path.")
        print_info("You can specify a custom path: hermes claw migrate --source /path/to/.openclaw")
        return

    # 查找迁移脚本
    script_path = _find_migration_script()
    if not script_path:
        print()
        print_error("Migration script not found.")
        print_info("Expected at one of:")
        print_info(f"  {_OPENCLAW_SCRIPT}")
        print_info(f"  {_OPENCLAW_SCRIPT_INSTALLED}")
        print_info("Make sure the openclaw-migration skill is installed.")
        return

    # 显示迁移设置
    hermes_home = get_hermes_home()
    auto_yes = getattr(args, "yes", False)
    print()
    print_header("Migration Settings")
    print_info(f"Source:      {source_dir}")
    print_info(f"Target:      {hermes_home}")
    print_info(f"Preset:      {preset}")
    print_info(f"Overwrite:   {'yes' if overwrite else 'no (skip conflicts)'}")
    print_info(f"Secrets:     {'yes (allowlisted only)' if migrate_secrets else 'no'}")
    if skill_conflict != "skip":
        print_info(f"Skill conflicts: {skill_conflict}")
    if workspace_target:
        print_info(f"Workspace:   {workspace_target}")
    print()

    # 检查 OpenClaw 是否仍在运行 —— 在两者都活跃时迁移 token
    # 会导致冲突（例如 Telegram 409）
    _warn_if_openclaw_running(auto_yes)

    # 检查 Hermes 网关是否正在运行并连接了平台
    _warn_if_gateway_running(auto_yes)

    # 确保 config.yaml 在迁移脚本尝试读取之前已存在
    config_path = get_config_path()
    if not config_path.exists():
        save_config(load_config())

    # 加载迁移模块
    try:
        mod = _load_migration_module(script_path)
        if mod is None:
            print_error("Could not load migration script.")
            return
    except Exception as e:
        print()
        print_error(f"Could not load migration script: {e}")
        logger.debug("OpenClaw migration error", exc_info=True)
        return

    selected = mod.resolve_selected_options(None, None, preset=preset)
    ws_target = Path(workspace_target).resolve() if workspace_target else None

    # ── 阶段 1：始终先预览 ──────────────────────────
    try:
        preview = mod.Migrator(
            source_root=source_dir.resolve(),
            target_root=hermes_home.resolve(),
            execute=False,
            workspace_target=ws_target,
            overwrite=overwrite,
            migrate_secrets=migrate_secrets,
            output_dir=None,
            selected_options=selected,
            preset_name=preset,
            skill_conflict_mode=skill_conflict,
        )
        preview_report = preview.migrate()
    except Exception as e:
        print()
        print_error(f"Migration preview failed: {e}")
        logger.debug("OpenClaw migration preview error", exc_info=True)
        return

    preview_summary = preview_report.get("summary", {})
    preview_count = preview_summary.get("migrated", 0)

    if preview_count == 0:
        print()
        print_info("Nothing to migrate from OpenClaw.")
        _print_migration_report(preview_report, dry_run=True)
        return

    print()
    print_header(f"Migration Preview — {preview_count} item(s) would be imported")
    print_info("No changes have been made yet. Review the list below:")
    _print_migration_report(preview_report, dry_run=True)

    # 如果是 --dry-run 模式，到此结束
    if dry_run:
        return

    # ── 阶段 2：确认并执行 ───────────────────────────
    print()
    if not auto_yes:
        if not sys.stdin.isatty():
            print_info("Non-interactive session — preview only.")
            print_info("To execute, re-run with: hermes claw migrate --yes")
            return
        if not prompt_yes_no("Proceed with migration?", default=True):
            print_info("Migration cancelled.")
            return

    try:
        migrator = mod.Migrator(
            source_root=source_dir.resolve(),
            target_root=hermes_home.resolve(),
            execute=True,
            workspace_target=ws_target,
            overwrite=overwrite,
            migrate_secrets=migrate_secrets,
            output_dir=None,
            selected_options=selected,
            preset_name=preset,
            skill_conflict_mode=skill_conflict,
        )
        report = migrator.migrate()
    except Exception as e:
        print()
        print_error(f"Migration failed: {e}")
        logger.debug("OpenClaw migration error", exc_info=True)
        return

    # 打印结果
    _print_migration_report(report, dry_run=False)

    # 源目录保持不动 —— 归档不是迁移工具的职责。
    # 需要清理的用户可以单独运行 'hermes claw cleanup'。


def _cmd_cleanup(args):
    """迁移后归档残留的 OpenClaw 目录。

    扫描迁移后仍存在的 OpenClaw 目录，并提供将它们重命名
    为 .pre-migration 以释放磁盘空间的选项。
    """
    dry_run = getattr(args, "dry_run", False)
    auto_yes = getattr(args, "yes", False)
    explicit_source = getattr(args, "source", None)

    print()
    print(
        color(
            "┌─────────────────────────────────────────────────────────┐",
            Colors.MAGENTA,
        )
    )
    print(
        color(
            "│          ⚕ Hermes — OpenClaw Cleanup                   │",
            Colors.MAGENTA,
        )
    )
    print(
        color(
            "└─────────────────────────────────────────────────────────┘",
            Colors.MAGENTA,
        )
    )

    # 查找 OpenClaw 目录
    if explicit_source:
        dirs_to_check = [Path(explicit_source)]
    else:
        dirs_to_check = _find_openclaw_dirs()

    if not dirs_to_check:
        print()
        print_success("No OpenClaw directories found. Nothing to clean up.")
        return

    # 警告 OpenClaw 是否仍在运行 —— 在服务活跃时归档会导致
    # 它立即重建一个空的骨架目录（#8502）
    running = _detect_openclaw_processes()
    if running:
        print()
        print_error("OpenClaw appears to be still running:")
        for detail in running:
            print_info(f"  * {detail}")
        print_info(
            "Archiving .openclaw/ while the service is active may cause it to "
            "immediately recreate an empty skeleton directory, destroying your config."
        )
        print_info("Stop OpenClaw first: systemctl --user stop openclaw-gateway.service")
        print()
        if not auto_yes:
            if not sys.stdin.isatty():
                print_info("Non-interactive session — aborting. Stop OpenClaw and re-run.")
                return
            if not prompt_yes_no("Proceed anyway?", default=False):
                print_info("Aborted. Stop OpenClaw first, then re-run: hermes claw cleanup")
                return

    total_archived = 0

    for source_dir in dirs_to_check:
        print()
        print_header(f"Found: {source_dir}")

        # 扫描状态文件
        state_files = _scan_workspace_state(source_dir)

        # 显示目录统计信息
        try:
            workspace_dirs = [
                d for d in source_dir.iterdir()
                if d.is_dir() and not d.name.startswith(".")
                and any((d / name).exists() for name in ("todo.json", "SOUL.md", "MEMORY.md", "USER.md"))
            ]
        except OSError:
            workspace_dirs = []

        if workspace_dirs:
            print_info(f"Workspace directories: {len(workspace_dirs)}")
            for ws in workspace_dirs[:5]:
                items = []
                if (ws / "todo.json").exists():
                    items.append("todo.json")
                if (ws / "sessions").is_dir():
                    items.append("sessions/")
                if (ws / "SOUL.md").exists():
                    items.append("SOUL.md")
                if (ws / "MEMORY.md").exists():
                    items.append("MEMORY.md")
                detail = ", ".join(items) if items else "empty"
                print(f"      {ws.name}/  ({detail})")
            if len(workspace_dirs) > 5:
                print(f"      ... and {len(workspace_dirs) - 5} more")

        if state_files:
            print()
            print(color(f"  {len(state_files)} state file(s) found:", Colors.YELLOW))
            for path, desc in state_files[:8]:
                print(f"      {desc}")
            if len(state_files) > 8:
                print(f"      ... and {len(state_files) - 8} more")

        print()

        if dry_run:
            archive_path = _archive_directory(source_dir, dry_run=True)
            print_info(f"Would archive: {source_dir} → {archive_path}")
        elif not auto_yes and not sys.stdin.isatty():
            print_info(f"Non-interactive session — would archive: {source_dir}")
            print_info("To execute, re-run with: hermes claw cleanup --yes")
        else:
            if auto_yes or prompt_yes_no(f"Archive {source_dir}?", default=True):
                try:
                    archive_path = _archive_directory(source_dir)
                    print_success(f"Archived: {source_dir} → {archive_path}")
                    total_archived += 1
                except OSError as e:
                    print_error(f"Could not archive: {e}")
                    print_info(f"Try manually: mv {source_dir} {source_dir}.pre-migration")
            else:
                print_info("Skipped.")

    # 摘要
    print()
    if dry_run:
        print_info(f"Dry run complete. {len(dirs_to_check)} directory(ies) would be archived.")
        print_info("Run without --dry-run to archive them.")
    elif total_archived:
        print_success(f"Cleaned up {total_archived} OpenClaw directory(ies).")
        print_info("Directories were renamed, not deleted. You can undo by renaming them back.")
    else:
        print_info("No directories were archived.")


def _print_migration_report(report: dict, dry_run: bool):
    """打印格式化的迁移报告。"""
    summary = report.get("summary", {})
    migrated = summary.get("migrated", 0)
    skipped = summary.get("skipped", 0)
    conflicts = summary.get("conflict", 0)
    errors = summary.get("error", 0)

    print()
    if dry_run:
        print_header("Dry Run Results")
        print_info("No files were modified. This is a preview of what would happen.")
    else:
        print_header("Migration Results")

    print()

    # 详细条目
    items = report.get("items", [])
    if items:
        # 按状态分组
        migrated_items = [i for i in items if i.get("status") == "migrated"]
        skipped_items = [i for i in items if i.get("status") == "skipped"]
        conflict_items = [i for i in items if i.get("status") == "conflict"]
        error_items = [i for i in items if i.get("status") == "error"]

        if migrated_items:
            label = "Would migrate" if dry_run else "Migrated"
            print(color(f"  ✓ {label}:", Colors.GREEN))
            for item in migrated_items:
                kind = item.get("kind", "unknown")
                dest = item.get("destination", "")
                if dest:
                    dest_short = str(dest).replace(str(Path.home()), "~")
                    print(f"      {kind:<22s} → {dest_short}")
                else:
                    print(f"      {kind}")
            print()

        if conflict_items:
            print(color("  ⚠ Conflicts (skipped — use --overwrite to force):", Colors.YELLOW))
            for item in conflict_items:
                kind = item.get("kind", "unknown")
                reason = item.get("reason", "already exists")
                print(f"      {kind:<22s}  {reason}")
            print()

        if skipped_items:
            print(color("  ─ Skipped:", Colors.DIM))
            for item in skipped_items:
                kind = item.get("kind", "unknown")
                reason = item.get("reason", "")
                print(f"      {kind:<22s}  {reason}")
            print()

        if error_items:
            print(color("  ✗ Errors:", Colors.RED))
            for item in error_items:
                kind = item.get("kind", "unknown")
                reason = item.get("reason", "unknown error")
                print(f"      {kind:<22s}  {reason}")
            print()

    # 汇总行
    parts = []
    if migrated:
        action = "would migrate" if dry_run else "migrated"
        parts.append(f"{migrated} {action}")
    if conflicts:
        parts.append(f"{conflicts} conflict(s)")
    if skipped:
        parts.append(f"{skipped} skipped")
    if errors:
        parts.append(f"{errors} error(s)")

    if parts:
        print_info(f"Summary: {', '.join(parts)}")
    else:
        print_info("Nothing to migrate.")

    # 输出目录
    output_dir = report.get("output_dir")
    if output_dir:
        print_info(f"Full report saved to: {output_dir}")

    if dry_run:
        print()
        print_info("To execute the migration, run without --dry-run:")
        print_info(f"  hermes claw migrate --preset {report.get('preset', 'full')}")
    elif migrated:
        print()
        print_success("Migration complete!")
        # 如果 API 密钥被跳过（未启用密钥迁移），发出警告
        skipped_keys = [
            i for i in report.get("items", [])
            if i.get("kind") == "provider-keys" and i.get("status") == "skipped"
        ]
        if skipped_keys:
            print()
            print(color("  ⚠ API keys were NOT migrated (secrets migration is disabled by default).", Colors.YELLOW))
            print(color("  Your OPENROUTER_API_KEY and other provider keys must be added manually.", Colors.YELLOW))
            print()
            print_info("To migrate API keys, re-run with:")
            print_info("  hermes claw migrate --migrate-secrets")
            print()
            print_info("Or add your key manually:")
            print_info("  hermes config set OPENROUTER_API_KEY sk-or-v1-...")
