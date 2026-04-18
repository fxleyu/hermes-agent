#!/usr/bin/env python3
"""贡献者审计脚本

交叉引用 git 作者、Co-authored-by 尾部标记和挽救的 PR 描述，
以查找发布说明中可能遗漏的贡献者。

用法:
    # 从某个标签开始的基本审计
    python scripts/contributor_audit.py --since-tag v2026.4.8

    # 使用自定义终点的审计
    python scripts/contributor_audit.py --since-tag v2026.4.8 --until v2026.4.13

    # 与发布说明文件进行对比
    python scripts/contributor_audit.py --since-tag v2026.4.8 --release-file RELEASE_v0.9.0.md
"""

import argparse
import json
import os
import re
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

# ---------------------------------------------------------------------------
# 从同级 release.py 模块导入 AUTHOR_MAP 和 resolve_author
# ---------------------------------------------------------------------------
SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from release import AUTHOR_MAP, resolve_author  # noqa: E402

REPO_ROOT = SCRIPT_DIR.parent

# ---------------------------------------------------------------------------
# 需要从贡献者列表中排除的 AI 助手、机器人和机器账户
# ---------------------------------------------------------------------------
IGNORED_PATTERNS = [
    re.compile(r"^Claude", re.IGNORECASE),
    re.compile(r"^Copilot$", re.IGNORECASE),
    re.compile(r"^Cursor\s+Agent$", re.IGNORECASE),
    re.compile(r"^GitHub\s*Actions?$", re.IGNORECASE),
    re.compile(r"^dependabot", re.IGNORECASE),
    re.compile(r"^renovate", re.IGNORECASE),
    re.compile(r"^Hermes\s+(Agent|Audit)$", re.IGNORECASE),
    re.compile(r"^Ubuntu$", re.IGNORECASE),
]

IGNORED_EMAILS = {
    "noreply@anthropic.com",
    "noreply@github.com",
    "cursoragent@cursor.com",
    "hermes@nousresearch.com",
    "hermes-audit@example.com",
    "hermes@habibilabs.dev",
}


def is_ignored(handle: str, email: str = "") -> bool:
    """判断该贡献者是否为机器人/AI/机器账户，返回 True 表示应忽略。"""
    if email in IGNORED_EMAILS:
        return True
    for pattern in IGNORED_PATTERNS:
        if pattern.search(handle):
            return True
    return False


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------

def git(*args, cwd=None):
    """运行 git 命令并返回标准输出。"""
    result = subprocess.run(
        ["git"] + list(args),
        capture_output=True,
        text=True,
        cwd=cwd or str(REPO_ROOT),
    )
    if result.returncode != 0:
        print(f"  [warn] git {' '.join(args)} failed: {result.stderr.strip()}", file=sys.stderr)
        return ""
    return result.stdout.strip()


def gh_pr_list():
    """使用 gh CLI 从 GitHub 获取已合并的 PR。

    返回包含 number、title、body、author 键的字典列表。
    如果 gh 不可用或调用失败，返回空列表。
    """
    try:
        result = subprocess.run(
            [
                "gh", "pr", "list",
                "--repo", "NousResearch/hermes-agent",
                "--state", "merged",
                "--json", "number,title,body,author,mergedAt",
                "--limit", "300",
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode != 0:
            print(f"  [warn] gh pr list failed: {result.stderr.strip()}", file=sys.stderr)
            return []
        return json.loads(result.stdout)
    except FileNotFoundError:
        print("  [warn] 'gh' CLI not found — skipping salvaged PR scan.", file=sys.stderr)
        return []
    except subprocess.TimeoutExpired:
        print("  [warn] gh pr list timed out — skipping salvaged PR scan.", file=sys.stderr)
        return []
    except json.JSONDecodeError:
        print("  [warn] gh pr list returned invalid JSON — skipping salvaged PR scan.", file=sys.stderr)
        return []


# ---------------------------------------------------------------------------
# 贡献者收集
# ---------------------------------------------------------------------------

# 在 PR 正文中表示挽救/cherry-pick/co-author 工作的模式
SALVAGE_PATTERNS = [
    # "Salvaged from @username" 或 "Salvaged from #123"
    re.compile(r"[Ss]alvaged\s+from\s+@(\w[\w-]*)"),
    re.compile(r"[Ss]alvaged\s+from\s+#(\d+)"),
    # "Cherry-picked from @username"（从某用户的提交中 cherry-pick）
    re.compile(r"[Cc]herry[- ]?picked\s+from\s+@(\w[\w-]*)"),
    # "Based on work by @username"（基于某用户的工作）
    re.compile(r"[Bb]ased\s+on\s+work\s+by\s+@(\w[\w-]*)"),
    # "Original PR by @username"（原始 PR 的作者）
    re.compile(r"[Oo]riginal\s+PR\s+by\s+@(\w[\w-]*)"),
    # "Co-authored with @username"（与某用户联合编写）
    re.compile(r"[Cc]o[- ]?authored\s+with\s+@(\w[\w-]*)"),
]

# 提交消息中 Co-authored-by 尾部标记的正则模式
CO_AUTHORED_RE = re.compile(
    r"Co-authored-by:\s*(.+?)\s*<([^>]+)>",
    re.IGNORECASE,
)


def collect_commit_authors(since_tag, until="HEAD"):
    """从 git 提交作者中收集贡献者。

    返回:
        contributors: 字典，映射 github_handle -> 来源标签集合
        unknown_emails: 字典，映射 email -> git 名称（不在 AUTHOR_MAP 中的邮箱）
    """
    range_spec = f"{since_tag}..{until}"
    log = git(
        "log", range_spec,
        "--format=%H|%an|%ae|%s",
        "--no-merges",
    )

    contributors = defaultdict(set)
    unknown_emails = {}

    if not log:
        return contributors, unknown_emails

    for line in log.split("\n"):
        if not line.strip():
            continue
        parts = line.split("|", 3)
        if len(parts) != 4:
            continue
        _sha, name, email, _subject = parts

        handle = resolve_author(name, email)
        # resolve_author 返回 "@handle" 或纯名称
        if handle.startswith("@"):
            contributors[handle.lstrip("@")].add("commit")
        else:
            # 无法解析 — 记录为未知
            contributors[handle].add("commit")
            unknown_emails[email] = name

    return contributors, unknown_emails


def collect_co_authors(since_tag, until="HEAD"):
    """从提交消息中的 Co-authored-by 尾部标记收集贡献者。

    返回:
        contributors: 字典，映射 github_handle -> 来源标签集合
        unknown_emails: 字典，映射 email -> git 名称
    """
    range_spec = f"{since_tag}..{until}"
    # 获取完整的提交消息以扫描尾部标记
    log = git(
        "log", range_spec,
        "--format=__COMMIT__%H%n%b",
        "--no-merges",
    )

    contributors = defaultdict(set)
    unknown_emails = {}

    if not log:
        return contributors, unknown_emails

    for line in log.split("\n"):
        match = CO_AUTHORED_RE.search(line)
        if match:
            name = match.group(1).strip()
            email = match.group(2).strip()
            handle = resolve_author(name, email)
            if handle.startswith("@"):
                contributors[handle.lstrip("@")].add("co-author")
            else:
                contributors[handle].add("co-author")
                unknown_emails[email] = name

    return contributors, unknown_emails


def collect_salvaged_contributors(since_tag, until="HEAD"):
    """扫描已合并 PR 的描述，查找挽救/cherry-pick/co-author 的贡献者归属。

    使用 gh CLI 获取 PR，然后按 since_tag..until 定义的日期范围过滤，
    并扫描 PR 正文中的挽救模式。

    返回:
        contributors: 字典，映射 github_handle -> 来源标签集合
        pr_refs: 字典，映射 github_handle -> 发现该贡献者的 PR 编号列表
    """
    contributors = defaultdict(set)
    pr_refs = defaultdict(list)

    # 从 git 标签/引用确定日期范围
    since_date = git("log", "-1", "--format=%aI", since_tag)
    if until == "HEAD":
        until_date = git("log", "-1", "--format=%aI", "HEAD")
    else:
        until_date = git("log", "-1", "--format=%aI", until)

    if not since_date:
        print(f"  [warn] Could not resolve date for {since_tag}", file=sys.stderr)
        return contributors, pr_refs

    prs = gh_pr_list()
    if not prs:
        return contributors, pr_refs

    for pr in prs:
        # 如果有合并日期则按其过滤
        merged_at = pr.get("mergedAt", "")
        if merged_at and since_date:
            if merged_at < since_date:
                continue
            if until_date and merged_at > until_date:
                continue

        body = pr.get("body") or ""
        pr_number = pr.get("number", "?")

        # 同时记录 PR 作者
        pr_author = pr.get("author", {})
        pr_author_login = pr_author.get("login", "") if isinstance(pr_author, dict) else ""

        for pattern in SALVAGE_PATTERNS:
            for match in pattern.finditer(body):
                value = match.group(1)
                # 如果是数字，则为 PR 引用 — 暂时跳过
                # （需要额外的 API 调用来解析 PR 作者）
                if value.isdigit():
                    continue
                contributors[value].add("salvage")
                pr_refs[value].append(pr_number)

    return contributors, pr_refs


# ---------------------------------------------------------------------------
# 发布文件对比
# ---------------------------------------------------------------------------

def check_release_file(release_file, all_contributors):
    """检查哪些贡献者在发布文件中被提及。

    返回:
        mentioned: 在文件中找到的用户名集合
        missing: 未在文件中找到的用户名集合
    """
    try:
        content = Path(release_file).read_text()
    except FileNotFoundError:
        print(f"  [error] Release file not found: {release_file}", file=sys.stderr)
        return set(), set(all_contributors)

    mentioned = set()
    missing = set()
    content_lower = content.lower()

    for handle in all_contributors:
        # 检查 @handle 或仅 handle（不区分大小写）
        if f"@{handle.lower()}" in content_lower or handle.lower() in content_lower:
            mentioned.add(handle)
        else:
            missing.add(handle)

    return mentioned, missing


# ---------------------------------------------------------------------------
# 主函数
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Audit contributors across git history, co-author trailers, and salvaged PRs.",
    )
    parser.add_argument(
        "--since-tag",
        required=True,
        help="Git tag to start from (e.g., v2026.4.8)",
    )
    parser.add_argument(
        "--until",
        default="HEAD",
        help="Git ref to end at (default: HEAD)",
    )
    parser.add_argument(
        "--release-file",
        default=None,
        help="Path to a release notes file to check for missing contributors",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Exit with code 1 if new unmapped emails are found (for CI)",
    )
    parser.add_argument(
        "--diff-base",
        default=None,
        help="Git ref to diff against (only flag emails from commits after this ref)",
    )
    args = parser.parse_args()

    print(f"=== Contributor Audit: {args.since_tag}..{args.until} ===")
    print()

    # ---- 1. Git 提交作者 ----
    print("[1/3] Scanning git commit authors...")
    commit_contribs, commit_unknowns = collect_commit_authors(args.since_tag, args.until)
    print(f"      Found {len(commit_contribs)} contributor(s) from commits.")

    # ---- 2. Co-authored-by 尾部标记 ----
    print("[2/3] Scanning Co-authored-by trailers...")
    coauthor_contribs, coauthor_unknowns = collect_co_authors(args.since_tag, args.until)
    print(f"      Found {len(coauthor_contribs)} contributor(s) from co-author trailers.")

    # ---- 3. 挽救的 PR ----
    print("[3/3] Scanning salvaged/cherry-picked PR descriptions...")
    salvage_contribs, salvage_pr_refs = collect_salvaged_contributors(args.since_tag, args.until)
    print(f"      Found {len(salvage_contribs)} contributor(s) from salvaged PRs.")

    # ---- 合并所有贡献者 ----
    all_contributors = defaultdict(set)
    for handle, sources in commit_contribs.items():
        all_contributors[handle].update(sources)
    for handle, sources in coauthor_contribs.items():
        all_contributors[handle].update(sources)
    for handle, sources in salvage_contribs.items():
        all_contributors[handle].update(sources)

    # 合并未知邮箱
    all_unknowns = {}
    all_unknowns.update(commit_unknowns)
    all_unknowns.update(coauthor_unknowns)

    # 过滤掉 AI 助手、机器人和机器账户
    ignored = {h for h in all_contributors if is_ignored(h)}
    for h in ignored:
        del all_contributors[h]
    # 同时按邮箱过滤未知贡献者
    all_unknowns = {e: n for e, n in all_unknowns.items() if not is_ignored(n, e)}

    # ---- 输出 ----
    print()
    print(f"=== All Contributors ({len(all_contributors)}) ===")
    print()

    # 按用户名排序，不区分大小写
    for handle in sorted(all_contributors.keys(), key=str.lower):
        sources = sorted(all_contributors[handle])
        source_str = ", ".join(sources)
        extra = ""
        if handle in salvage_pr_refs:
            pr_nums = salvage_pr_refs[handle]
            extra = f"  (PRs: {', '.join(f'#{n}' for n in pr_nums)})"
        print(f"  @{handle}  [{source_str}]{extra}")

    # ---- 未知邮箱 ----
    if all_unknowns:
        print()
        print(f"=== Unknown Emails ({len(all_unknowns)}) ===")
        print("These emails are not in AUTHOR_MAP and should be added:")
        print()
        for email, name in sorted(all_unknowns.items()):
            print(f'  "{email}": "{name}",')

    # ---- 严格模式：如果引入新的未映射邮箱则 CI 失败 ----
    if args.strict and all_unknowns:
        # 在严格模式下，检查是否有未知邮箱来自此 PR 差异范围中的提交
        # （之前不存在的新未映射邮箱）。
        # 这是 CI 门禁：已有的未知邮箱被豁免，但新提交必须在 AUTHOR_MAP 中有其作者邮箱。
        new_unknowns = {}
        if args.diff_base:
            # 仅标记 diff_base 之后提交中的邮箱
            new_commits_output = git(
                "log", f"{args.diff_base}..HEAD",
                "--format=%ae", "--no-merges",
            )
            new_emails = set(new_commits_output.splitlines()) if new_commits_output else set()
            for email, name in all_unknowns.items():
                if email in new_emails:
                    new_unknowns[email] = name
        else:
            new_unknowns = all_unknowns

        if new_unknowns:
            print()
            print(f"=== STRICT MODE FAILURE: {len(new_unknowns)} new unmapped email(s) ===")
            print("Add these to AUTHOR_MAP in scripts/release.py before merging:")
            print()
            for email, name in sorted(new_unknowns.items()):
                print(f'    "{email}": "<github-username>",')
            print()
            print("To find the GitHub username:")
            print("  gh api 'search/users?q=EMAIL+in:email' --jq '.items[0].login'")
            strict_failed = True
        else:
            strict_failed = False
    else:
        strict_failed = False

    # ---- 发布文件对比 ----
    if args.release_file:
        print()
        print(f"=== Release File Check: {args.release_file} ===")
        print()
        mentioned, missing = check_release_file(args.release_file, all_contributors.keys())
        print(f"  Mentioned in release notes: {len(mentioned)}")
        print(f"  Missing from release notes: {len(missing)}")
        if missing:
            print()
            print("  Contributors NOT mentioned in the release file:")
            for handle in sorted(missing, key=str.lower):
                sources = sorted(all_contributors[handle])
                print(f"    @{handle}  [{', '.join(sources)}]")
        else:
            print()
            print("  All contributors are mentioned in the release file!")

    print()
    print("Done.")

    if strict_failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
