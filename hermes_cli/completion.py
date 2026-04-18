"""hermes CLI 的 Shell 补全脚本生成。

遍历运行时的 argparse 解析器树，生成准确且始终最新的
补全脚本 —— 无需硬编码子命令列表，无需额外依赖。

支持 bash、zsh 和 fish。
"""

from __future__ import annotations

import argparse
from typing import Any


def _walk(parser: argparse.ArgumentParser) -> dict[str, Any]:
    """递归地从解析器中提取子命令和标志。

    使用 _SubParsersAction._choices_actions 获取规范名称（不包含别名）
    及其帮助文本。
    """
    flags: list[str] = []
    subcommands: dict[str, Any] = {}

    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            # _choices_actions 每个规范名称有一个条目；别名被省略，
            # 这使补全列表保持简洁。
            seen: set[str] = set()
            for pseudo in action._choices_actions:
                name = pseudo.dest
                if name in seen:
                    continue
                seen.add(name)
                subparser = action.choices.get(name)
                if subparser is None:
                    continue
                info = _walk(subparser)
                info["help"] = _clean(pseudo.help or "")
                subcommands[name] = info
        elif action.option_strings:
            flags.extend(o for o in action.option_strings if o.startswith("-"))

    return {"flags": flags, "subcommands": subcommands}


def _clean(text: str, maxlen: int = 60) -> str:
    """去除 Shell 不安全字符并截断。"""
    return text.replace("'", "").replace('"', "").replace("\\", "")[:maxlen]


# ---------------------------------------------------------------------------
# Bash 补全
# ---------------------------------------------------------------------------

def generate_bash(parser: argparse.ArgumentParser) -> str:
    tree = _walk(parser)
    top_cmds = " ".join(sorted(tree["subcommands"]))

    cases: list[str] = []
    for cmd in sorted(tree["subcommands"]):
        info = tree["subcommands"][cmd]
        if cmd == "profile" and info["subcommands"]:
            # Profile 子命令：补全操作，然后为接受 profile 参数的操作
            # 补全 profile 名称。
            subcmds = " ".join(sorted(info["subcommands"]))
            profile_actions = "use delete show alias rename export"
            cases.append(
                f"        profile)\n"
                f"            case \"$prev\" in\n"
                f"                profile)\n"
                f"                    COMPREPLY=($(compgen -W \"{subcmds}\" -- \"$cur\"))\n"
                f"                    return\n"
                f"                    ;;\n"
                f"                {profile_actions.replace(' ', '|')})\n"
                f"                    COMPREPLY=($(compgen -W \"$(_hermes_profiles)\" -- \"$cur\"))\n"
                f"                    return\n"
                f"                    ;;\n"
                f"            esac\n"
                f"            ;;"
            )
        elif info["subcommands"]:
            subcmds = " ".join(sorted(info["subcommands"]))
            cases.append(
                f"        {cmd})\n"
                f"            COMPREPLY=($(compgen -W \"{subcmds}\" -- \"$cur\"))\n"
                f"            return\n"
                f"            ;;"
            )
        elif info["flags"]:
            flags = " ".join(info["flags"])
            cases.append(
                f"        {cmd})\n"
                f"            COMPREPLY=($(compgen -W \"{flags}\" -- \"$cur\"))\n"
                f"            return\n"
                f"            ;;"
            )

    cases_str = "\n".join(cases)

    return f"""# Hermes Agent bash 补全
# 添加到 ~/.bashrc:
#   eval "$(hermes completion bash)"

_hermes_profiles() {{
    local profiles_dir="$HOME/.hermes/profiles"
    local profiles="default"
    if [ -d "$profiles_dir" ]; then
        profiles="$profiles $(ls "$profiles_dir" 2>/dev/null)"
    fi
    echo "$profiles"
}}

_hermes_completion() {{
    local cur prev
    COMPREPLY=()
    cur="${{COMP_WORDS[COMP_CWORD]}}"
    prev="${{COMP_WORDS[COMP_CWORD-1]}}"

    # Complete profile names after -p / --profile
    # 在 -p / --profile 后补全 profile 名称
    if [[ "$prev" == "-p" || "$prev" == "--profile" ]]; then
        COMPREPLY=($(compgen -W "$(_hermes_profiles)" -- "$cur"))
        return
    fi

    if [[ $COMP_CWORD -ge 2 ]]; then
        case "${{COMP_WORDS[1]}}" in
{cases_str}
        esac
    fi

    if [[ $COMP_CWORD -eq 1 ]]; then
        COMPREPLY=($(compgen -W "{top_cmds}" -- "$cur"))
    fi
}}

complete -F _hermes_completion hermes
"""


# ---------------------------------------------------------------------------
# Zsh 补全
# ---------------------------------------------------------------------------

def generate_zsh(parser: argparse.ArgumentParser) -> str:
    tree = _walk(parser)

    top_cmds_lines: list[str] = []
    for cmd in sorted(tree["subcommands"]):
        help_text = _clean(tree["subcommands"][cmd].get("help", ""))
        top_cmds_lines.append(f"                '{cmd}:{help_text}'")
    top_cmds_str = "\n".join(top_cmds_lines)

    sub_cases: list[str] = []
    for cmd in sorted(tree["subcommands"]):
        info = tree["subcommands"][cmd]
        if not info["subcommands"]:
            continue
        if cmd == "profile":
            # Profile 子命令：补全操作，然后为接受 profile 参数的操作
            # 补全 profile 名称。
            sub_lines: list[str] = []
            for sc in sorted(info["subcommands"]):
                sh = _clean(info["subcommands"][sc].get("help", ""))
                sub_lines.append(f"                        '{sc}:{sh}'")
            sub_str = "\n".join(sub_lines)
            sub_cases.append(
                f"                profile)\n"
                f"                    case ${{line[2]}} in\n"
                f"                        use|delete|show|alias|rename|export)\n"
                f"                            _hermes_profiles\n"
                f"                            ;;\n"
                f"                        *)\n"
                f"                            local -a profile_cmds\n"
                f"                            profile_cmds=(\n"
                f"{sub_str}\n"
                f"                            )\n"
                f"                            _describe 'profile command' profile_cmds\n"
                f"                            ;;\n"
                f"                    esac\n"
                f"                    ;;"
            )
        else:
            sub_lines = []
            for sc in sorted(info["subcommands"]):
                sh = _clean(info["subcommands"][sc].get("help", ""))
                sub_lines.append(f"                    '{sc}:{sh}'")
            sub_str = "\n".join(sub_lines)
            safe = cmd.replace("-", "_")
            sub_cases.append(
                f"                {cmd})\n"
                f"                    local -a {safe}_cmds\n"
                f"                    {safe}_cmds=(\n"
                f"{sub_str}\n"
                f"                    )\n"
                f"                    _describe '{cmd} command' {safe}_cmds\n"
                f"                    ;;"
            )
    sub_cases_str = "\n".join(sub_cases)

    return f"""#compdef hermes
# Hermes Agent zsh 补全
# 添加到 ~/.zshrc:
#   eval "$(hermes completion zsh)"

_hermes_profiles() {{
    local -a profiles
    profiles=(default)
    if [[ -d "$HOME/.hermes/profiles" ]]; then
        profiles+=("${{(@f)$(ls $HOME/.hermes/profiles 2>/dev/null)}}")
    fi
    _describe 'profile' profiles
}}

_hermes() {{
    local context state line
    typeset -A opt_args

    _arguments -C \\
        '(-h --help){{-h,--help}}[Show help and exit]' \\
        '(-V --version){{-V,--version}}[Show version and exit]' \\
        '(-p --profile){{-p,--profile}}[Profile name]:profile:_hermes_profiles' \\
        '1:command:->commands' \\
        '*::arg:->args'

    case $state in
        commands)
            local -a subcmds
            subcmds=(
{top_cmds_str}
            )
            _describe 'hermes command' subcmds
            ;;
        args)
            case ${{line[1]}} in
{sub_cases_str}
            esac
            ;;
    esac
}}

_hermes "$@"
"""


# ---------------------------------------------------------------------------
# Fish 补全
# ---------------------------------------------------------------------------

def generate_fish(parser: argparse.ArgumentParser) -> str:
    tree = _walk(parser)
    top_cmds = sorted(tree["subcommands"])
    top_cmds_str = " ".join(top_cmds)

    lines: list[str] = [
        "# Hermes Agent fish 补全",
        "# 添加到你的配置:",
        "#   hermes completion fish | source",
        "",
        "# 辅助函数：列出可用的 profiles",
        "function __hermes_profiles",
        "    echo default",
        "    if test -d $HOME/.hermes/profiles",
        "        ls $HOME/.hermes/profiles 2>/dev/null",
        "    end",
        "end",
        "",
        "# 默认禁用文件补全",
        "complete -c hermes -f",
        "",
        "# 在 -p / --profile 后补全 profile 名称",
        "complete -c hermes -f -s p -l profile"
        " -d 'Profile name' -xa '(__hermes_profiles)'",
        "",
        "# 顶层子命令",
    ]

    for cmd in top_cmds:
        info = tree["subcommands"][cmd]
        help_text = _clean(info.get("help", ""))
        lines.append(
            f"complete -c hermes -f "
            f"-n 'not __fish_seen_subcommand_from {top_cmds_str}' "
            f"-a {cmd} -d '{help_text}'"
        )

    lines.append("")
    lines.append("# 子命令补全")

    profile_name_actions = {"use", "delete", "show", "alias", "rename", "export"}

    for cmd in top_cmds:
        info = tree["subcommands"][cmd]
        if not info["subcommands"]:
            continue
        lines.append(f"# {cmd}")
        for sc in sorted(info["subcommands"]):
            sinfo = info["subcommands"][sc]
            sh = _clean(sinfo.get("help", ""))
            lines.append(
                f"complete -c hermes -f "
                f"-n '__fish_seen_subcommand_from {cmd}' "
                f"-a {sc} -d '{sh}'"
            )
        # 对于 profile 子命令，为相关操作补全 profile 名称
        if cmd == "profile":
            for action in sorted(profile_name_actions):
                lines.append(
                    f"complete -c hermes -f "
                    f"-n '__fish_seen_subcommand_from {action}; "
                    f"and __fish_seen_subcommand_from profile' "
                    f"-a '(__hermes_profiles)' -d 'Profile name'"
                )

    lines.append("")
    return "\n".join(lines)
