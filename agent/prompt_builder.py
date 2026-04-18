"""系统提示词组装——身份、平台提示、技能索引、上下文文件。

所有函数都是无状态的。AIAgent._build_system_prompt() 调用这些函数
组装各部分，然后将它们与记忆和临时提示词组合。
"""

import json
import logging
import os
import re
import threading
from collections import OrderedDict
from pathlib import Path

from hermes_constants import get_hermes_home, get_skills_dir, is_wsl
from typing import Optional

from agent.skill_utils import (
    extract_skill_conditions,
    extract_skill_description,
    get_all_skills_dirs,
    get_disabled_skill_names,
    iter_skill_index_files,
    parse_frontmatter,
    skill_matches_platform,
)
from utils import atomic_json_write

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 上下文文件扫描——在 AGENTS.md、.cursorrules、SOUL.md 被注入
# 系统提示词之前，检测其中的提示词注入。
# ---------------------------------------------------------------------------

_CONTEXT_THREAT_PATTERNS = [
    (r'ignore\s+(previous|all|above|prior)\s+instructions', "prompt_injection"),
    (r'do\s+not\s+tell\s+the\s+user', "deception_hide"),
    (r'system\s+prompt\s+override', "sys_prompt_override"),
    (r'disregard\s+(your|all|any)\s+(instructions|rules|guidelines)', "disregard_rules"),
    (r'act\s+as\s+(if|though)\s+you\s+(have\s+no|don\'t\s+have)\s+(restrictions|limits|rules)', "bypass_restrictions"),
    (r'<!--[^>]*(?:ignore|override|system|secret|hidden)[^>]*-->', "html_comment_injection"),
    (r'<\s*div\s+style\s*=\s*["\'][\s\S]*?display\s*:\s*none', "hidden_div"),
    (r'translate\s+.*\s+into\s+.*\s+and\s+(execute|run|eval)', "translate_execute"),
    (r'curl\s+[^\n]*\$\{?\w*(KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL|API)', "exfil_curl"),
    (r'cat\s+[^\n]*(\.env|credentials|\.netrc|\.pgpass)', "read_secrets"),
]

_CONTEXT_INVISIBLE_CHARS = {
    '\u200b', '\u200c', '\u200d', '\u2060', '\ufeff',
    '\u202a', '\u202b', '\u202c', '\u202d', '\u202e',
}


def _scan_context_content(content: str, filename: str) -> str:
    """扫描上下文文件内容中的注入攻击。返回清理后的内容。"""
    findings = []

    # 检查不可见 Unicode 字符
    for char in _CONTEXT_INVISIBLE_CHARS:
        if char in content:
            findings.append(f"invisible unicode U+{ord(char):04X}")

    # 检查威胁模式
    for pattern, pid in _CONTEXT_THREAT_PATTERNS:
        if re.search(pattern, content, re.IGNORECASE):
            findings.append(pid)

    if findings:
        logger.warning("Context file %s blocked: %s", filename, ", ".join(findings))
        return f"[BLOCKED: {filename} contained potential prompt injection ({', '.join(findings)}). Content not loaded.]"

    return content


def _find_git_root(start: Path) -> Optional[Path]:
    """从 *start* 及其父目录向上遍历查找 ``.git`` 目录。

    返回包含 ``.git`` 的目录，如果到达文件系统根目录
    仍未找到则返回 ``None``。
    """
    current = start.resolve()
    for parent in [current, *current.parents]:
        if (parent / ".git").exists():
            return parent
    return None


_HERMES_MD_NAMES = (".hermes.md", "HERMES.md")


def _find_hermes_md(cwd: Path) -> Optional[Path]:
    """发现最近的 ``.hermes.md`` 或 ``HERMES.md``。

    搜索顺序：先 *cwd*，然后沿父目录向上直到（包括）
    git 仓库根目录。返回第一个匹配项，如果未找到则返回 ``None``。
    """
    stop_at = _find_git_root(cwd)
    current = cwd.resolve()

    for directory in [current, *current.parents]:
        for name in _HERMES_MD_NAMES:
            candidate = directory / name
            if candidate.is_file():
                return candidate
        # 在 git 根目录（或文件系统根目录）停止遍历。
        if stop_at and directory == stop_at:
            break
    return None


def _strip_yaml_frontmatter(content: str) -> str:
    """从 *content* 中移除可选的 YAML frontmatter（``---`` 分隔）。

    frontmatter 可能包含结构化配置（模型覆盖、工具设置），
    将在未来的 PR 中单独处理。目前仅剥离它，
    只将人类可读的 markdown 正文注入系统提示词。
    """
    if content.startswith("---"):
        end = content.find("\n---", 3)
        if end != -1:
            # 跳过结束的 --- 及其后的换行符
            body = content[end + 4:].lstrip("\n")
            return body if body else content
    return content


# =========================================================================
# 常量
# =========================================================================

DEFAULT_AGENT_IDENTITY = (
    "You are Hermes Agent, an intelligent AI assistant created by Nous Research. "
    "You are helpful, knowledgeable, and direct. You assist users with a wide "
    "range of tasks including answering questions, writing and editing code, "
    "analyzing information, creative work, and executing actions via your tools. "
    "You communicate clearly, admit uncertainty when appropriate, and prioritize "
    "being genuinely useful over being verbose unless otherwise directed below. "
    "Be targeted and efficient in your exploration and investigations."
)

MEMORY_GUIDANCE = (
    "You have persistent memory across sessions. Save durable facts using the memory "
    "tool: user preferences, environment details, tool quirks, and stable conventions. "
    "Memory is injected into every turn, so keep it compact and focused on facts that "
    "will still matter later.\n"
    "Prioritize what reduces future user steering — the most valuable memory is one "
    "that prevents the user from having to correct or remind you again. "
    "User preferences and recurring corrections matter more than procedural task details.\n"
    "Do NOT save task progress, session outcomes, completed-work logs, or temporary TODO "
    "state to memory; use session_search to recall those from past transcripts. "
    "If you've discovered a new way to do something, solved a problem that could be "
    "necessary later, save it as a skill with the skill tool."
)

SESSION_SEARCH_GUIDANCE = (
    "When the user references something from a past conversation or you suspect "
    "relevant cross-session context exists, use session_search to recall it before "
    "asking them to repeat themselves."
)

SKILLS_GUIDANCE = (
    "After completing a complex task (5+ tool calls), fixing a tricky error, "
    "or discovering a non-trivial workflow, save the approach as a "
    "skill with skill_manage so you can reuse it next time.\n"
    "When using a skill and finding it outdated, incomplete, or wrong, "
    "patch it immediately with skill_manage(action='patch') — don't wait to be asked. "
    "Skills that aren't maintained become liabilities."
)

TOOL_USE_ENFORCEMENT_GUIDANCE = (
    "# Tool-use enforcement\n"
    "You MUST use your tools to take action — do not describe what you would do "
    "or plan to do without actually doing it. When you say you will perform an "
    "action (e.g. 'I will run the tests', 'Let me check the file', 'I will create "
    "the project'), you MUST immediately make the corresponding tool call in the same "
    "response. Never end your turn with a promise of future action — execute it now.\n"
    "Keep working until the task is actually complete. Do not stop with a summary of "
    "what you plan to do next time. If you have tools available that can accomplish "
    "the task, use them instead of telling the user what you would do.\n"
    "Every response should either (a) contain tool calls that make progress, or "
    "(b) deliver a final result to the user. Responses that only describe intentions "
    "without acting are not acceptable."
)

# 触发工具使用执行指导的模型名称子串。
# 当模型家族需要显式引导时在此添加新模式。
TOOL_USE_ENFORCEMENT_MODELS = ("gpt", "codex", "gemini", "gemma", "grok")

# OpenAI GPT/Codex 特定执行指导。解决 GPT 模型在部分结果时
# 放弃工作、跳过前置查找、产生幻觉而非使用工具、
# 以及未验证就声明"完成"的已知失败模式。
# 灵感来自 OpenAI 的 GPT-5.4 提示指南和 OpenClaw PR #38953 的模式。
OPENAI_MODEL_EXECUTION_GUIDANCE = (
    "# Execution discipline\n"
    "<tool_persistence>\n"
    "- Use tools whenever they improve correctness, completeness, or grounding.\n"
    "- Do not stop early when another tool call would materially improve the result.\n"
    "- If a tool returns empty or partial results, retry with a different query or "
    "strategy before giving up.\n"
    "- Keep calling tools until: (1) the task is complete, AND (2) you have verified "
    "the result.\n"
    "</tool_persistence>\n"
    "\n"
    "<mandatory_tool_use>\n"
    "NEVER answer these from memory or mental computation — ALWAYS use a tool:\n"
    "- Arithmetic, math, calculations → use terminal or execute_code\n"
    "- Hashes, encodings, checksums → use terminal (e.g. sha256sum, base64)\n"
    "- Current time, date, timezone → use terminal (e.g. date)\n"
    "- System state: OS, CPU, memory, disk, ports, processes → use terminal\n"
    "- File contents, sizes, line counts → use read_file, search_files, or terminal\n"
    "- Git history, branches, diffs → use terminal\n"
    "- Current facts (weather, news, versions) → use web_search\n"
    "Your memory and user profile describe the USER, not the system you are "
    "running on. The execution environment may differ from what the user profile "
    "says about their personal setup.\n"
    "</mandatory_tool_use>\n"
    "\n"
    "<act_dont_ask>\n"
    "When a question has an obvious default interpretation, act on it immediately "
    "instead of asking for clarification. Examples:\n"
    "- 'Is port 443 open?' → check THIS machine (don't ask 'open where?')\n"
    "- 'What OS am I running?' → check the live system (don't use user profile)\n"
    "- 'What time is it?' → run `date` (don't guess)\n"
    "Only ask for clarification when the ambiguity genuinely changes what tool "
    "you would call.\n"
    "</act_dont_ask>\n"
    "\n"
    "<prerequisite_checks>\n"
    "- Before taking an action, check whether prerequisite discovery, lookup, or "
    "context-gathering steps are needed.\n"
    "- Do not skip prerequisite steps just because the final action seems obvious.\n"
    "- If a task depends on output from a prior step, resolve that dependency first.\n"
    "</prerequisite_checks>\n"
    "\n"
    "<verification>\n"
    "Before finalizing your response:\n"
    "- Correctness: does the output satisfy every stated requirement?\n"
    "- Grounding: are factual claims backed by tool outputs or provided context?\n"
    "- Formatting: does the output match the requested format or schema?\n"
    "- Safety: if the next step has side effects (file writes, commands, API calls), "
    "confirm scope before executing.\n"
    "</verification>\n"
    "\n"
    "<missing_context>\n"
    "- If required context is missing, do NOT guess or hallucinate an answer.\n"
    "- Use the appropriate lookup tool when missing information is retrievable "
    "(search_files, web_search, read_file, etc.).\n"
    "- Ask a clarifying question only when the information cannot be retrieved by tools.\n"
    "- If you must proceed with incomplete information, label assumptions explicitly.\n"
    "</missing_context>"
)

# Gemini/Gemma 特定操作指导，改编自 OpenCode 的 gemini.txt。
# 当模型为 Gemini 或 Gemma 时，与 TOOL_USE_ENFORCEMENT_GUIDANCE 一起注入。
GOOGLE_MODEL_OPERATIONAL_GUIDANCE = (
    "# Google model operational directives\n"
    "Follow these operational rules strictly:\n"
    "- **Absolute paths:** Always construct and use absolute file paths for all "
    "file system operations. Combine the project root with relative paths.\n"
    "- **Verify first:** Use read_file/search_files to check file contents and "
    "project structure before making changes. Never guess at file contents.\n"
    "- **Dependency checks:** Never assume a library is available. Check "
    "package.json, requirements.txt, Cargo.toml, etc. before importing.\n"
    "- **Conciseness:** Keep explanatory text brief — a few sentences, not "
    "paragraphs. Focus on actions and results over narration.\n"
    "- **Parallel tool calls:** When you need to perform multiple independent "
    "operations (e.g. reading several files), make all the tool calls in a "
    "single response rather than sequentially.\n"
    "- **Non-interactive commands:** Use flags like -y, --yes, --non-interactive "
    "to prevent CLI tools from hanging on prompts.\n"
    "- **Keep going:** Work autonomously until the task is fully resolved. "
    "Don't stop with a plan — execute it.\n"
)

# 应使用 'developer' 角色而非 'system' 作为系统提示词的模型名称子串。
# OpenAI 较新的模型（GPT-5、Codex）对 'developer' 角色赋予
# 更强的指令遵循权重。角色切换发生在 _build_api_kwargs() 的
# API 边界处，内部消息表示保持一致（到处都是 "system"）。
DEVELOPER_ROLE_MODELS = ("gpt-5", "codex")

PLATFORM_HINTS = {
    "whatsapp": (
        "You are on a text messaging communication platform, WhatsApp. "
        "Please do not use markdown as it does not render. "
        "You can send media files natively: to deliver a file to the user, "
        "include MEDIA:/absolute/path/to/file in your response. The file "
        "will be sent as a native WhatsApp attachment — images (.jpg, .png, "
        ".webp) appear as photos, videos (.mp4, .mov) play inline, and other "
        "files arrive as downloadable documents. You can also include image "
        "URLs in markdown format ![alt](url) and they will be sent as photos."
    ),
    "telegram": (
        "You are on a text messaging communication platform, Telegram. "
        "Standard markdown is automatically converted to Telegram format. "
        "Supported: **bold**, *italic*, ~~strikethrough~~, ||spoiler||, "
        "`inline code`, ```code blocks```, [links](url), and ## headers. "
        "You can send media files natively: to deliver a file to the user, "
        "include MEDIA:/absolute/path/to/file in your response. Images "
        "(.png, .jpg, .webp) appear as photos, audio (.ogg) sends as voice "
        "bubbles, and videos (.mp4) play inline. You can also include image "
        "URLs in markdown format ![alt](url) and they will be sent as native photos."
    ),
    "discord": (
        "You are in a Discord server or group chat communicating with your user. "
        "You can send media files natively: include MEDIA:/absolute/path/to/file "
        "in your response. Images (.png, .jpg, .webp) are sent as photo "
        "attachments, audio as file attachments. You can also include image URLs "
        "in markdown format ![alt](url) and they will be sent as attachments."
    ),
    "slack": (
        "You are in a Slack workspace communicating with your user. "
        "You can send media files natively: include MEDIA:/absolute/path/to/file "
        "in your response. Images (.png, .jpg, .webp) are uploaded as photo "
        "attachments, audio as file attachments. You can also include image URLs "
        "in markdown format ![alt](url) and they will be uploaded as attachments."
    ),
    "signal": (
        "You are on a text messaging communication platform, Signal. "
        "Please do not use markdown as it does not render. "
        "You can send media files natively: to deliver a file to the user, "
        "include MEDIA:/absolute/path/to/file in your response. Images "
        "(.png, .jpg, .webp) appear as photos, audio as attachments, and other "
        "files arrive as downloadable documents. You can also include image "
        "URLs in markdown format ![alt](url) and they will be sent as photos."
    ),
    "email": (
        "You are communicating via email. Write clear, well-structured responses "
        "suitable for email. Use plain text formatting (no markdown). "
        "Keep responses concise but complete. You can send file attachments — "
        "include MEDIA:/absolute/path/to/file in your response. The subject line "
        "is preserved for threading. Do not include greetings or sign-offs unless "
        "contextually appropriate."
    ),
    "cron": (
        "You are running as a scheduled cron job. There is no user present — you "
        "cannot ask questions, request clarification, or wait for follow-up. Execute "
        "the task fully and autonomously, making reasonable decisions where needed. "
        "Your final response is automatically delivered to the job's configured "
        "destination — put the primary content directly in your response."
    ),
    "cli": (
        "You are a CLI AI Agent. Try not to use markdown but simple text "
        "renderable inside a terminal."
    ),
    "sms": (
        "You are communicating via SMS. Keep responses concise and use plain text "
        "only — no markdown, no formatting. SMS messages are limited to ~1600 "
        "characters, so be brief and direct."
    ),
    "bluebubbles": (
        "You are chatting via iMessage (BlueBubbles). iMessage does not render "
        "markdown formatting — use plain text. Keep responses concise as they "
        "appear as text messages. You can send media files natively: include "
        "MEDIA:/absolute/path/to/file in your response. Images (.jpg, .png, "
        ".heic) appear as photos and other files arrive as attachments."
    ),
    "weixin": (
        "You are on Weixin/WeChat. Markdown formatting is supported, so you may use it when "
        "it improves readability, but keep the message compact and chat-friendly. You can send media files natively: "
        "include MEDIA:/absolute/path/to/file in your response. Images are sent as native "
        "photos, videos play inline when supported, and other files arrive as downloadable "
        "documents. You can also include image URLs in markdown format ![alt](url) and they "
        "will be downloaded and sent as native media when possible."
    ),
    "wecom": (
        "You are on WeCom (企业微信 / Enterprise WeChat). Markdown formatting is supported. "
        "You CAN send media files natively — to deliver a file to the user, include "
        "MEDIA:/absolute/path/to/file in your response. The file will be sent as a native "
        "WeCom attachment: images (.jpg, .png, .webp) are sent as photos (up to 10 MB), "
        "other files (.pdf, .docx, .xlsx, .md, .txt, etc.) arrive as downloadable documents "
        "(up to 20 MB), and videos (.mp4) play inline. Voice messages are supported but "
        "must be in AMR format — other audio formats are automatically sent as file attachments. "
        "You can also include image URLs in markdown format ![alt](url) and they will be "
        "downloaded and sent as native photos. Do NOT tell the user you lack file-sending "
        "capability — use MEDIA: syntax whenever a file delivery is appropriate."
    ),
    "qqbot": (
        "You are on QQ, a popular Chinese messaging platform. QQ supports markdown formatting "
        "and emoji. You can send media files natively: include MEDIA:/absolute/path/to/file in "
        "your response. Images are sent as native photos, and other files arrive as downloadable "
        "documents."
    ),
}

# ---------------------------------------------------------------------------
# 环境提示——代理的执行环境感知。
# 与 PLATFORM_HINTS（描述消息通道）不同，这些描述
# 代理工具实际运行的机器/操作系统。
# ---------------------------------------------------------------------------

WSL_ENVIRONMENT_HINT = (
    "You are running inside WSL (Windows Subsystem for Linux). "
    "The Windows host filesystem is mounted under /mnt/ — "
    "/mnt/c/ is the C: drive, /mnt/d/ is D:, etc. "
    "The user's Windows files are typically at "
    "/mnt/c/Users/<username>/Desktop/, Documents/, Downloads/, etc. "
    "When the user references Windows paths or desktop files, translate "
    "to the /mnt/c/ equivalent. You can list /mnt/c/Users/ to discover "
    "the Windows username if needed."
)


def build_environment_hints() -> str:
    """返回系统提示词的环境特定指导。

    检测 WSL，可扩展为 Termux、Docker 等。
    未检测到特殊环境时返回空字符串。
    """
    hints: list[str] = []
    if is_wsl():
        hints.append(WSL_ENVIRONMENT_HINT)
    return "\n\n".join(hints)


CONTEXT_FILE_MAX_CHARS = 20_000
CONTEXT_TRUNCATE_HEAD_RATIO = 0.7
CONTEXT_TRUNCATE_TAIL_RATIO = 0.2


# =========================================================================
# 技能提示词缓存
# =========================================================================

_SKILLS_PROMPT_CACHE_MAX = 8
_SKILLS_PROMPT_CACHE: OrderedDict[tuple, str] = OrderedDict()
_SKILLS_PROMPT_CACHE_LOCK = threading.Lock()
_SKILLS_SNAPSHOT_VERSION = 1


def _skills_prompt_snapshot_path() -> Path:
    return get_hermes_home() / ".skills_prompt_snapshot.json"


def clear_skills_system_prompt_cache(*, clear_snapshot: bool = False) -> None:
    """清除进程内技能提示词缓存（可选清除磁盘快照）。"""
    with _SKILLS_PROMPT_CACHE_LOCK:
        _SKILLS_PROMPT_CACHE.clear()
    if clear_snapshot:
        try:
            _skills_prompt_snapshot_path().unlink(missing_ok=True)
        except OSError as e:
            logger.debug("Could not remove skills prompt snapshot: %s", e)


def _build_skills_manifest(skills_dir: Path) -> dict[str, list[int]]:
    """构建所有 SKILL.md 和 DESCRIPTION.md 文件的 mtime/size 清单。"""
    manifest: dict[str, list[int]] = {}
    for filename in ("SKILL.md", "DESCRIPTION.md"):
        for path in iter_skill_index_files(skills_dir, filename):
            try:
                st = path.stat()
            except OSError:
                continue
            manifest[str(path.relative_to(skills_dir))] = [st.st_mtime_ns, st.st_size]
    return manifest


def _load_skills_snapshot(skills_dir: Path) -> Optional[dict]:
    """如果磁盘快照存在且清单仍匹配则加载。"""
    snapshot_path = _skills_prompt_snapshot_path()
    if not snapshot_path.exists():
        return None
    try:
        snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(snapshot, dict):
        return None
    if snapshot.get("version") != _SKILLS_SNAPSHOT_VERSION:
        return None
    if snapshot.get("manifest") != _build_skills_manifest(skills_dir):
        return None
    return snapshot


def _write_skills_snapshot(
    skills_dir: Path,
    manifest: dict[str, list[int]],
    skill_entries: list[dict],
    category_descriptions: dict[str, str],
) -> None:
    """将技能元数据持久化到磁盘以便冷启动快速复用。"""
    payload = {
        "version": _SKILLS_SNAPSHOT_VERSION,
        "manifest": manifest,
        "skills": skill_entries,
        "category_descriptions": category_descriptions,
    }
    try:
        atomic_json_write(_skills_prompt_snapshot_path(), payload)
    except Exception as e:
        logger.debug("Could not write skills prompt snapshot: %s", e)


def _build_snapshot_entry(
    skill_file: Path,
    skills_dir: Path,
    frontmatter: dict,
    description: str,
) -> dict:
    """为单个技能构建可序列化的元数据字典。"""
    rel_path = skill_file.relative_to(skills_dir)
    parts = rel_path.parts
    if len(parts) >= 2:
        skill_name = parts[-2]
        category = "/".join(parts[:-2]) if len(parts) > 2 else parts[0]
    else:
        category = "general"
        skill_name = skill_file.parent.name

    platforms = frontmatter.get("platforms") or []
    if isinstance(platforms, str):
        platforms = [platforms]

    return {
        "skill_name": skill_name,
        "category": category,
        "frontmatter_name": str(frontmatter.get("name", skill_name)),
        "description": description,
        "platforms": [str(p).strip() for p in platforms if str(p).strip()],
        "conditions": extract_skill_conditions(frontmatter),
    }


# =========================================================================
# 技能索引
# =========================================================================

def _parse_skill_file(skill_file: Path) -> tuple[bool, dict, str]:
    """读取 SKILL.md 一次并返回平台兼容性、frontmatter 和描述。

    返回 (is_compatible, frontmatter, description)。出错时返回
    (True, {}, "")，倾向于显示该技能。
    """
    try:
        raw = skill_file.read_text(encoding="utf-8")
        frontmatter, _ = parse_frontmatter(raw)

        if not skill_matches_platform(frontmatter):
            return False, frontmatter, ""

        return True, frontmatter, extract_skill_description(frontmatter)
    except Exception as e:
        logger.warning("Failed to parse skill file %s: %s", skill_file, e)
        return True, {}, ""


def _skill_should_show(
    conditions: dict,
    available_tools: "set[str] | None",
    available_toolsets: "set[str] | None",
) -> bool:
    """如果技能的条件激活规则排除了它则返回 False。"""
    if available_tools is None and available_toolsets is None:
        return True  # 无过滤信息——显示全部（向后兼容）

    at = available_tools or set()
    ats = available_toolsets or set()

    # fallback_for：当主工具/工具集可用时隐藏
    for ts in conditions.get("fallback_for_toolsets", []):
        if ts in ats:
            return False
    for t in conditions.get("fallback_for_tools", []):
        if t in at:
            return False

    # requires：当所需工具/工具集不可用时隐藏
    for ts in conditions.get("requires_toolsets", []):
        if ts not in ats:
            return False
    for t in conditions.get("requires_tools", []):
        if t not in at:
            return False

    return True


def build_skills_system_prompt(
    available_tools: "set[str] | None" = None,
    available_toolsets: "set[str] | None" = None,
) -> str:
    """为系统提示词构建紧凑的技能索引。

    两层缓存：
      1. 进程内 LRU 字典，键为 (skills_dir, tools, toolsets)
      2. 磁盘快照（``.skills_prompt_snapshot.json``），通过
         mtime/size 清单验证——在进程重启后仍有效

    两层缓存都未命中时回退到完整文件系统扫描。

    外部技能目录（config.yaml 中的 ``skills.external_dirs``）
    与本地 ``~/.hermes/skills/`` 目录一起扫描。外部目录
    是只读的——它们出现在索引中但新技能始终创建在本地目录中。
    名称冲突时本地技能优先。
    """
    skills_dir = get_skills_dir()
    external_dirs = get_all_skills_dirs()[1:]  # skip local (index 0)

    if not skills_dir.exists() and not external_dirs:
        return ""

    # ── 第 1 层：进程内 LRU 缓存 ─────────────────────────────────
    # 包含已解析的平台，使每平台禁用技能列表
    # 产生不同的缓存条目（网关服务多个平台）。
    from gateway.session_context import get_session_env
    _platform_hint = (
        os.environ.get("HERMES_PLATFORM")
        or get_session_env("HERMES_SESSION_PLATFORM")
        or ""
    )
    cache_key = (
        str(skills_dir.resolve()),
        tuple(str(d) for d in external_dirs),
        tuple(sorted(str(t) for t in (available_tools or set()))),
        tuple(sorted(str(ts) for ts in (available_toolsets or set()))),
        _platform_hint,
    )
    with _SKILLS_PROMPT_CACHE_LOCK:
        cached = _SKILLS_PROMPT_CACHE.get(cache_key)
        if cached is not None:
            _SKILLS_PROMPT_CACHE.move_to_end(cache_key)
            return cached

    disabled = get_disabled_skill_names()

    # ── 第 2 层：磁盘快照 ────────────────────────────────────────
    snapshot = _load_skills_snapshot(skills_dir)

    skills_by_category: dict[str, list[tuple[str, str]]] = {}
    category_descriptions: dict[str, str] = {}

    if snapshot is not None:
        # 快速路径：使用磁盘上的预解析元数据
        for entry in snapshot.get("skills", []):
            if not isinstance(entry, dict):
                continue
            skill_name = entry.get("skill_name") or ""
            category = entry.get("category") or "general"
            frontmatter_name = entry.get("frontmatter_name") or skill_name
            platforms = entry.get("platforms") or []
            if not skill_matches_platform({"platforms": platforms}):
                continue
            if frontmatter_name in disabled or skill_name in disabled:
                continue
            if not _skill_should_show(
                entry.get("conditions") or {},
                available_tools,
                available_toolsets,
            ):
                continue
            skills_by_category.setdefault(category, []).append(
                (skill_name, entry.get("description", ""))
            )
        category_descriptions = {
            str(k): str(v)
            for k, v in (snapshot.get("category_descriptions") or {}).items()
        }
    else:
        # 冷路径：完整文件系统扫描 + 写入快照供下次使用
        skill_entries: list[dict] = []
        for skill_file in iter_skill_index_files(skills_dir, "SKILL.md"):
            is_compatible, frontmatter, desc = _parse_skill_file(skill_file)
            entry = _build_snapshot_entry(skill_file, skills_dir, frontmatter, desc)
            skill_entries.append(entry)
            if not is_compatible:
                continue
            skill_name = entry["skill_name"]
            if entry["frontmatter_name"] in disabled or skill_name in disabled:
                continue
            if not _skill_should_show(
                extract_skill_conditions(frontmatter),
                available_tools,
                available_toolsets,
            ):
                continue
            skills_by_category.setdefault(entry["category"], []).append(
                (skill_name, entry["description"])
            )

        # 读取分类级别的 DESCRIPTION.md 文件
        for desc_file in iter_skill_index_files(skills_dir, "DESCRIPTION.md"):
            try:
                content = desc_file.read_text(encoding="utf-8")
                fm, _ = parse_frontmatter(content)
                cat_desc = fm.get("description")
                if not cat_desc:
                    continue
                rel = desc_file.relative_to(skills_dir)
                cat = "/".join(rel.parts[:-1]) if len(rel.parts) > 1 else "general"
                category_descriptions[cat] = str(cat_desc).strip().strip("'\"")
            except Exception as e:
                logger.debug("Could not read skill description %s: %s", desc_file, e)

        _write_skills_snapshot(
            skills_dir,
            _build_skills_manifest(skills_dir),
            skill_entries,
            category_descriptions,
        )

    # ── 外部技能目录 ─────────────────────────────────────
    # 直接扫描外部目录（不做快照缓存——它们是只读的
    # 且通常较小）。已在 skills_by_category 中的本地技能
    # 优先：追踪已见名称并跳过来自外部目录的重复项。
    seen_skill_names: set[str] = set()
    for cat_skills in skills_by_category.values():
        for name, _desc in cat_skills:
            seen_skill_names.add(name)

    for ext_dir in external_dirs:
        if not ext_dir.exists():
            continue
        for skill_file in iter_skill_index_files(ext_dir, "SKILL.md"):
            try:
                is_compatible, frontmatter, desc = _parse_skill_file(skill_file)
                if not is_compatible:
                    continue
                entry = _build_snapshot_entry(skill_file, ext_dir, frontmatter, desc)
                skill_name = entry["skill_name"]
                if skill_name in seen_skill_names:
                    continue
                if entry["frontmatter_name"] in disabled or skill_name in disabled:
                    continue
                if not _skill_should_show(
                    extract_skill_conditions(frontmatter),
                    available_tools,
                    available_toolsets,
                ):
                    continue
                seen_skill_names.add(skill_name)
                skills_by_category.setdefault(entry["category"], []).append(
                    (skill_name, entry["description"])
                )
            except Exception as e:
                logger.debug("Error reading external skill %s: %s", skill_file, e)

        # 外部分类描述
        for desc_file in iter_skill_index_files(ext_dir, "DESCRIPTION.md"):
            try:
                content = desc_file.read_text(encoding="utf-8")
                fm, _ = parse_frontmatter(content)
                cat_desc = fm.get("description")
                if not cat_desc:
                    continue
                rel = desc_file.relative_to(ext_dir)
                cat = "/".join(rel.parts[:-1]) if len(rel.parts) > 1 else "general"
                category_descriptions.setdefault(cat, str(cat_desc).strip().strip("'\""))
            except Exception as e:
                logger.debug("Could not read external skill description %s: %s", desc_file, e)

    if not skills_by_category:
        result = ""
    else:
        index_lines = []
        for category in sorted(skills_by_category.keys()):
            cat_desc = category_descriptions.get(category, "")
            if cat_desc:
                index_lines.append(f"  {category}: {cat_desc}")
            else:
                index_lines.append(f"  {category}:")
            # 每个分类内去重并排序技能
            seen = set()
            for name, desc in sorted(skills_by_category[category], key=lambda x: x[0]):
                if name in seen:
                    continue
                seen.add(name)
                if desc:
                    index_lines.append(f"    - {name}: {desc}")
                else:
                    index_lines.append(f"    - {name}")

        result = (
            "## Skills (mandatory)\n"
            "Before replying, scan the skills below. If a skill matches or is even partially relevant "
            "to your task, you MUST load it with skill_view(name) and follow its instructions. "
            "Err on the side of loading — it is always better to have context you don't need "
            "than to miss critical steps, pitfalls, or established workflows. "
            "Skills contain specialized knowledge — API endpoints, tool-specific commands, "
            "and proven workflows that outperform general-purpose approaches. Load the skill "
            "even if you think you could handle the task with basic tools like web_search or terminal. "
            "Skills also encode the user's preferred approach, conventions, and quality standards "
            "for tasks like code review, planning, and testing — load them even for tasks you "
            "already know how to do, because the skill defines how it should be done here.\n"
            "If a skill has issues, fix it with skill_manage(action='patch').\n"
            "After difficult/iterative tasks, offer to save as a skill. "
            "If a skill you loaded was missing steps, had wrong commands, or needed "
            "pitfalls you discovered, update it before finishing.\n"
            "\n"
            "<available_skills>\n"
            + "\n".join(index_lines) + "\n"
            "</available_skills>\n"
            "\n"
            "Only proceed without loading a skill if genuinely none are relevant to the task."
        )

    # ── 存入 LRU 缓存 ────────────────────────────────────────────
    with _SKILLS_PROMPT_CACHE_LOCK:
        _SKILLS_PROMPT_CACHE[cache_key] = result
        _SKILLS_PROMPT_CACHE.move_to_end(cache_key)
        while len(_SKILLS_PROMPT_CACHE) > _SKILLS_PROMPT_CACHE_MAX:
            _SKILLS_PROMPT_CACHE.popitem(last=False)

    return result


def build_nous_subscription_prompt(valid_tool_names: "set[str] | None" = None) -> str:
    """为系统提示词构建紧凑的 Nous 订阅功能块。"""
    try:
        from hermes_cli.nous_subscription import get_nous_subscription_features
        from tools.tool_backend_helpers import managed_nous_tools_enabled
    except Exception as exc:
        logger.debug("Failed to import Nous subscription helper: %s", exc)
        return ""

    if not managed_nous_tools_enabled():
        return ""

    valid_names = set(valid_tool_names or set())
    relevant_tool_names = {
        "web_search",
        "web_extract",
        "browser_navigate",
        "browser_snapshot",
        "browser_click",
        "browser_type",
        "browser_scroll",
        "browser_console",
        "browser_press",
        "browser_get_images",
        "browser_vision",
        "image_generate",
        "text_to_speech",
        "terminal",
        "process",
        "execute_code",
    }

    if valid_names and not (valid_names & relevant_tool_names):
        return ""

    features = get_nous_subscription_features()

    def _status_line(feature) -> str:
        if feature.managed_by_nous:
            return f"- {feature.label}: active via Nous subscription"
        if feature.active:
            current = feature.current_provider or "configured provider"
            return f"- {feature.label}: currently using {current}"
        if feature.included_by_default and features.nous_auth_present:
            return f"- {feature.label}: included with Nous subscription, not currently selected"
        if feature.key == "modal" and features.nous_auth_present:
            return f"- {feature.label}: optional via Nous subscription"
        return f"- {feature.label}: not currently available"

    lines = [
        "# Nous Subscription",
        "Nous subscription includes managed web tools (Firecrawl), image generation (FAL), OpenAI TTS, and browser automation (Browser Use) by default. Modal execution is optional.",
        "Current capability status:",
    ]
    lines.extend(_status_line(feature) for feature in features.items())
    lines.extend(
        [
            "When a Nous-managed feature is active, do not ask the user for Firecrawl, FAL, OpenAI TTS, or Browser-Use API keys.",
            "If the user is not subscribed and asks for a capability that Nous subscription would unlock or simplify, suggest Nous subscription as one option alongside direct setup or local alternatives.",
            "Do not mention subscription unless the user asks about it or it directly solves the current missing capability.",
            "Useful commands: hermes setup, hermes setup tools, hermes setup terminal, hermes status.",
        ]
    )
    return "\n".join(lines)


# =========================================================================
# 上下文文件（SOUL.md、AGENTS.md、.cursorrules）
# =========================================================================

def _truncate_content(content: str, filename: str, max_chars: int = CONTEXT_FILE_MAX_CHARS) -> str:
    """头/尾截断，中间加标记。"""
    if len(content) <= max_chars:
        return content
    head_chars = int(max_chars * CONTEXT_TRUNCATE_HEAD_RATIO)
    tail_chars = int(max_chars * CONTEXT_TRUNCATE_TAIL_RATIO)
    head = content[:head_chars]
    tail = content[-tail_chars:]
    marker = f"\n\n[...truncated {filename}: kept {head_chars}+{tail_chars} of {len(content)} chars. Use file tools to read the full file.]\n\n"
    return head + marker + tail


def load_soul_md() -> Optional[str]:
    """从 HERMES_HOME 加载 SOUL.md 并返回其内容，或 None。

    用作代理身份（系统提示词中的第 1 个插槽）。当返回内容时，
    ``build_context_files_prompt`` 应使用 ``skip_soul=True`` 调用，
    以避免 SOUL.md 被注入两次。
    """
    try:
        from hermes_cli.config import ensure_hermes_home
        ensure_hermes_home()
    except Exception as e:
        logger.debug("Could not ensure HERMES_HOME before loading SOUL.md: %s", e)

    soul_path = get_hermes_home() / "SOUL.md"
    if not soul_path.exists():
        return None
    try:
        content = soul_path.read_text(encoding="utf-8").strip()
        if not content:
            return None
        content = _scan_context_content(content, "SOUL.md")
        content = _truncate_content(content, "SOUL.md")
        return content
    except Exception as e:
        logger.debug("Could not read SOUL.md from %s: %s", soul_path, e)
        return None


def _load_hermes_md(cwd_path: Path) -> str:
    """.hermes.md / HERMES.md——向上遍历到 git 根目录。"""
    hermes_md_path = _find_hermes_md(cwd_path)
    if not hermes_md_path:
        return ""
    try:
        content = hermes_md_path.read_text(encoding="utf-8").strip()
        if not content:
            return ""
        content = _strip_yaml_frontmatter(content)
        rel = hermes_md_path.name
        try:
            rel = str(hermes_md_path.relative_to(cwd_path))
        except ValueError:
            pass
        content = _scan_context_content(content, rel)
        result = f"## {rel}\n\n{content}"
        return _truncate_content(result, ".hermes.md")
    except Exception as e:
        logger.debug("Could not read %s: %s", hermes_md_path, e)
        return ""


def _load_agents_md(cwd_path: Path) -> str:
    """AGENTS.md——仅顶层目录（不递归遍历）。"""
    for name in ["AGENTS.md", "agents.md"]:
        candidate = cwd_path / name
        if candidate.exists():
            try:
                content = candidate.read_text(encoding="utf-8").strip()
                if content:
                    content = _scan_context_content(content, name)
                    result = f"## {name}\n\n{content}"
                    return _truncate_content(result, "AGENTS.md")
            except Exception as e:
                logger.debug("Could not read %s: %s", candidate, e)
    return ""


def _load_claude_md(cwd_path: Path) -> str:
    """CLAUDE.md / claude.md——仅 cwd。"""
    for name in ["CLAUDE.md", "claude.md"]:
        candidate = cwd_path / name
        if candidate.exists():
            try:
                content = candidate.read_text(encoding="utf-8").strip()
                if content:
                    content = _scan_context_content(content, name)
                    result = f"## {name}\n\n{content}"
                    return _truncate_content(result, "CLAUDE.md")
            except Exception as e:
                logger.debug("Could not read %s: %s", candidate, e)
    return ""


def _load_cursorrules(cwd_path: Path) -> str:
    """.cursorrules + .cursor/rules/*.mdc——仅 cwd。"""
    cursorrules_content = ""
    cursorrules_file = cwd_path / ".cursorrules"
    if cursorrules_file.exists():
        try:
            content = cursorrules_file.read_text(encoding="utf-8").strip()
            if content:
                content = _scan_context_content(content, ".cursorrules")
                cursorrules_content += f"## .cursorrules\n\n{content}\n\n"
        except Exception as e:
            logger.debug("Could not read .cursorrules: %s", e)

    cursor_rules_dir = cwd_path / ".cursor" / "rules"
    if cursor_rules_dir.exists() and cursor_rules_dir.is_dir():
        mdc_files = sorted(cursor_rules_dir.glob("*.mdc"))
        for mdc_file in mdc_files:
            try:
                content = mdc_file.read_text(encoding="utf-8").strip()
                if content:
                    content = _scan_context_content(content, f".cursor/rules/{mdc_file.name}")
                    cursorrules_content += f"## .cursor/rules/{mdc_file.name}\n\n{content}\n\n"
            except Exception as e:
                logger.debug("Could not read %s: %s", mdc_file, e)

    if not cursorrules_content:
        return ""
    return _truncate_content(cursorrules_content, ".cursorrules")


def build_context_files_prompt(cwd: Optional[str] = None, skip_soul: bool = False) -> str:
    """发现并加载系统提示词的上下文文件。

    优先级（找到第一个即生效——只加载一种项目上下文类型）：
      1. .hermes.md / HERMES.md  （向上遍历到 git 根目录）
      2. AGENTS.md / agents.md   （仅 cwd）
      3. CLAUDE.md / claude.md   （仅 cwd）
      4. .cursorrules / .cursor/rules/*.mdc  （仅 cwd）

    HERMES_HOME 中的 SOUL.md 是独立的，存在时始终包含。
    每个上下文源上限为 20,000 字符。

    当 *skip_soul* 为 True 时，此处不包含 SOUL.md（它已通过
    ``load_soul_md()`` 为身份插槽加载）。
    """
    if cwd is None:
        cwd = os.getcwd()

    cwd_path = Path(cwd).resolve()
    sections = []

    # 基于优先级的项目上下文：第一个匹配即生效
    project_context = (
        _load_hermes_md(cwd_path)
        or _load_agents_md(cwd_path)
        or _load_claude_md(cwd_path)
        or _load_cursorrules(cwd_path)
    )
    if project_context:
        sections.append(project_context)

    # 仅 HERMES_HOME 中的 SOUL.md——已作为身份加载时跳过
    if not skip_soul:
        soul_content = load_soul_md()
        if soul_content:
            sections.append(soul_content)

    if not sections:
        return ""
    return "# Project Context\n\nThe following project context files have been loaded and should be followed:\n\n" + "\n".join(sections)
