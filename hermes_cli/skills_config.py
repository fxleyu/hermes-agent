"""
Hermes Agent 的技能配置模块。
`hermes skills` 命令进入此模块。

切换单个技能或类别的启用/禁用状态，可全局或按平台设置。
配置存储在 ~/.hermes/config.yaml 中：

  skills:
    disabled: [skill-a, skill-b]          # 全局禁用列表
    platform_disabled:                    # 按平台覆盖
      telegram: [skill-c]
      cli: []
"""
from typing import List, Optional, Set

from hermes_cli.config import load_config, save_config
from hermes_cli.colors import Colors, color
from hermes_cli.platforms import PLATFORMS as _PLATFORMS

# 向后兼容的视图：{key: label_string}，使得遍历
# ``PLATFORMS.items()`` 或调用 ``PLATFORMS.get(key)`` 的
# 现有代码无需修改即可继续工作。
PLATFORMS = {k: info.label for k, info in _PLATFORMS.items() if k != "api_server"}

# ─── 配置辅助函数 ───────────────────────────────────────────────────────────

def get_disabled_skills(config: dict, platform: Optional[str] = None) -> Set[str]:
    """返回已禁用的技能名称集合。按平台查找时回退到全局列表。"""
    skills_cfg = config.get("skills", {})
    global_disabled = set(skills_cfg.get("disabled", []))
    if platform is None:
        return global_disabled
    # 尝试获取平台特定的禁用列表，不存在则回退到全局
    platform_disabled = skills_cfg.get("platform_disabled", {}).get(platform)
    if platform_disabled is None:
        return global_disabled
    return set(platform_disabled)


def save_disabled_skills(config: dict, disabled: Set[str], platform: Optional[str] = None):
    """将已禁用的技能名称持久化到配置中。"""
    config.setdefault("skills", {})
    if platform is None:
        # 保存全局禁用列表
        config["skills"]["disabled"] = sorted(disabled)
    else:
        # 保存平台特定的禁用列表
        config["skills"].setdefault("platform_disabled", {})
        config["skills"]["platform_disabled"][platform] = sorted(disabled)
    save_config(config)


# ─── 技能发现 ─────────────────────────────────────────────────────────

def _list_all_skills() -> List[dict]:
    """返回所有已安装的技能（忽略禁用状态）。"""
    try:
        from tools.skills_tool import _find_all_skills
        return _find_all_skills(skip_disabled=True)
    except Exception:
        return []


def _get_categories(skills: List[dict]) -> List[str]:
    """返回排序后的唯一类别名称（None 映射为 'uncategorized'）。"""
    return sorted({s["category"] or "uncategorized" for s in skills})


# ─── 平台选择 ──────────────────────────────────────────────────────

def _select_platform() -> Optional[str]:
    """询问用户要配置哪个平台，或选择全局配置。"""
    options = [("global", "All platforms (global default)")] + list(PLATFORMS.items())
    print()
    print(color("  Configure skills for:", Colors.BOLD))
    for i, (key, label) in enumerate(options, 1):
        print(f"  {i}. {label}")
    print()
    try:
        raw = input(color("  Select [1]: ", Colors.YELLOW)).strip()
    except (KeyboardInterrupt, EOFError):
        return None
    if not raw:
        return None  # 默认选择全局
    try:
        idx = int(raw) - 1
        if 0 <= idx < len(options):
            key = options[idx][0]
            return None if key == "global" else key
    except ValueError:
        pass
    return None


# ─── 按类别切换 ─────────────────────────────────────────────────────────

def _toggle_by_category(skills: List[dict], disabled: Set[str]) -> Set[str]:
    """按类别一次性切换该类别下所有技能的启用/禁用状态。"""
    from hermes_cli.curses_ui import curses_checklist

    categories = _get_categories(skills)
    cat_labels = []
    # 当某个类别中并非所有技能都被禁用时，该类别视为"已启用"（选中）
    pre_selected = set()
    for i, cat in enumerate(categories):
        cat_skills = [s["name"] for s in skills if (s["category"] or "uncategorized") == cat]
        cat_labels.append(f"{cat} ({len(cat_skills)} skills)")
        if not all(s in disabled for s in cat_skills):
            pre_selected.add(i)

    chosen = curses_checklist(
        "Categories — toggle entire categories",
        cat_labels, pre_selected, cancel_returns=pre_selected,
    )

    # 根据用户的选择更新禁用集合
    new_disabled = set(disabled)
    for i, cat in enumerate(categories):
        cat_skills = {s["name"] for s in skills if (s["category"] or "uncategorized") == cat}
        if i in chosen:
            new_disabled -= cat_skills  # 类别被启用 → 从禁用列表中移除
        else:
            new_disabled |= cat_skills  # 类别被禁用 → 添加到禁用列表
    return new_disabled


# ─── 入口点 ──────────────────────────────────────────────────────────

def skills_command(args=None):
    """`hermes skills` 命令的入口点。"""
    from hermes_cli.curses_ui import curses_checklist

    config = load_config()
    skills = _list_all_skills()

    if not skills:
        print(color("  No skills installed.", Colors.DIM))
        return

    # 第 1 步：选择平台
    platform = _select_platform()
    platform_label = PLATFORMS.get(platform, "All platforms") if platform else "All platforms"

    # 第 2 步：选择模式 —— 按单个技能或按类别
    print()
    print(color(f"  Configure for: {platform_label}", Colors.DIM))
    print()
    print("  1. Toggle individual skills")
    print("  2. Toggle by category")
    print()
    try:
        mode = input(color("  Select [1]: ", Colors.YELLOW)).strip() or "1"
    except (KeyboardInterrupt, EOFError):
        return

    disabled = get_disabled_skills(config, platform)

    if mode == "2":
        new_disabled = _toggle_by_category(skills, disabled)
    else:
        # 构建标签列表，并将索引映射到技能名称
        labels = [
            f"{s['name']}  ({s['category'] or 'uncategorized'})  —  {s['description'][:55]}"
            for s in skills
        ]
        # "已选中" = 已启用（不在禁用列表中）—— 与 [✓] 约定一致
        pre_selected = {i for i, s in enumerate(skills) if s["name"] not in disabled}
        chosen = curses_checklist(
            f"Skills for {platform_label}",
            labels, pre_selected, cancel_returns=pre_selected,
        )
        # 未被选中的技能即为禁用的技能
        new_disabled = {skills[i]["name"] for i in range(len(skills)) if i not in chosen}

    if new_disabled == disabled:
        print(color("  No changes.", Colors.DIM))
        return

    save_disabled_skills(config, new_disabled, platform)
    enabled_count = len(skills) - len(new_disabled)
    print(color(f"✓ Saved: {enabled_count} enabled, {len(new_disabled)} disabled ({platform_label}).", Colors.GREEN))
