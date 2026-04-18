#!/usr/bin/env python3
"""构建 Hermes 技能索引 — 所有技能的集中式 JSON 目录。

此脚本爬取每个技能来源（skills.sh、GitHub taps、official、
clawhub、lobehub、claude-marketplace），并生成一个带有解析后
GitHub 路径的 JSON 索引。该索引作为静态文件发布在文档站点上，
使 `hermes skills search/install` 可以在不调用 GitHub API 的情况下使用。

用法:
    # 本地（使用 gh CLI 或 GITHUB_TOKEN 进行认证）
    python scripts/build_skills_index.py

    # CI（将 GITHUB_TOKEN 设为 secret）
    GITHUB_TOKEN=ghp_... python scripts/build_skills_index.py

输出: website/static/api/skills-index.json
"""

import json
import os
import sys
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

# 允许从仓库根目录导入
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

# 确保 HERMES_HOME 已设置（tools/skills_hub.py 的导入需要）
os.environ.setdefault("HERMES_HOME", os.path.join(os.path.expanduser("~"), ".hermes"))

from tools.skills_hub import (
    GitHubAuth,
    GitHubSource,
    SkillsShSource,
    OptionalSkillSource,
    WellKnownSkillSource,
    ClawHubSource,
    ClaudeMarketplaceSource,
    LobeHubSource,
    SkillMeta,
)
import httpx

OUTPUT_PATH = os.path.join(REPO_ROOT, "website", "static", "api", "skills-index.json")
INDEX_VERSION = 1


def _meta_to_dict(meta: SkillMeta) -> dict:
    """将 SkillMeta 转换为可序列化的字典。"""
    return {
        "name": meta.name,
        "description": meta.description,
        "source": meta.source,
        "identifier": meta.identifier,
        "trust_level": meta.trust_level,
        "repo": meta.repo or "",
        "path": meta.path or "",
        "tags": meta.tags or [],
        "extra": meta.extra or {},
    }


def crawl_source(source, source_name: str, limit: int) -> list:
    """爬取单个来源并返回技能字典列表。"""
    print(f"  Crawling {source_name}...", flush=True)
    start = time.time()
    try:
        results = source.search("", limit=limit)
    except Exception as e:
        print(f"  Error crawling {source_name}: {e}", file=sys.stderr)
        return []
    skills = [_meta_to_dict(m) for m in results]
    elapsed = time.time() - start
    print(f"  {source_name}: {len(skills)} skills ({elapsed:.1f}s)", flush=True)
    return skills


def crawl_skills_sh(source: SkillsShSource) -> list:
    """使用热门查询词爬取 skills.sh，以获得广泛覆盖。"""
    print("  Crawling skills.sh (popular queries)...", flush=True)
    start = time.time()

    queries = [
        "",  # featured
        "react", "python", "web", "api", "database", "docker",
        "testing", "scraping", "design", "typescript", "git",
        "aws", "security", "data", "ml", "ai", "devops",
        "frontend", "backend", "mobile", "cli", "documentation",
        "kubernetes", "terraform", "rust", "go", "java",
    ]

    all_skills: dict[str, dict] = {}
    for query in queries:
        try:
            results = source.search(query, limit=50)
            for meta in results:
                entry = _meta_to_dict(meta)
                if entry["identifier"] not in all_skills:
                    all_skills[entry["identifier"]] = entry
        except Exception as e:
            print(f"    Warning: skills.sh search '{query}' failed: {e}",
                  file=sys.stderr)

    elapsed = time.time() - start
    print(f"  skills.sh: {len(all_skills)} unique skills ({elapsed:.1f}s)",
          flush=True)
    return list(all_skills.values())


def _fetch_repo_tree(repo: str, auth: GitHubAuth) -> list:
    """获取仓库的递归文件树。返回文件树条目列表。"""
    headers = auth.get_headers()
    try:
        resp = httpx.get(
            f"https://api.github.com/repos/{repo}",
            headers=headers, timeout=15, follow_redirects=True,
        )
        if resp.status_code != 200:
            return []
        branch = resp.json().get("default_branch", "main")

        resp = httpx.get(
            f"https://api.github.com/repos/{repo}/git/trees/{branch}",
            params={"recursive": "1"},
            headers=headers, timeout=30, follow_redirects=True,
        )
        if resp.status_code != 200:
            return []
        data = resp.json()
        if data.get("truncated"):
            return []
        return data.get("tree", [])
    except Exception:
        return []


def batch_resolve_paths(skills: list, auth: GitHubAuth) -> list:
    """使用批量文件树查找为 skills.sh 条目解析 GitHub 路径。

    不再逐个解析每个技能（N×M 次 API 调用），而是：
    1. 按仓库分组技能
    2. 每个仓库只获取一次文件树（每仓库 2 次 API 调用）
    3. 在文件树中查找所有 SKILL.md 文件
    4. 将技能与其解析后的路径进行匹配
    """
    # 筛选需要路径解析的 skills.sh 条目
    skills_sh = [s for s in skills if s["source"] in ("skills.sh", "skills-sh")]
    if not skills_sh:
        return skills

    print(f"  Resolving paths for {len(skills_sh)} skills.sh entries...",
          flush=True)
    start = time.time()

    # 按仓库分组
    by_repo: dict[str, list] = defaultdict(list)
    for s in skills_sh:
        repo = s.get("repo", "")
        if repo:
            by_repo[repo].append(s)

    print(f"    {len(by_repo)} unique repos to scan", flush=True)

    resolved_count = 0

    # 并行获取文件树（最多 6 个并发）
    def _resolve_repo(repo: str, entries: list):
        tree = _fetch_repo_tree(repo, auth)
        if not tree:
            return 0

        # 查找此仓库中所有 SKILL.md 路径
        skill_paths = {}  # 技能目录名 -> 完整路径
        for item in tree:
            if item.get("type") != "blob":
                continue
            path = item.get("path", "")
            if path.endswith("/SKILL.md"):
                skill_dir = path[: -len("/SKILL.md")]
                dir_name = skill_dir.split("/")[-1]
                skill_paths[dir_name.lower()] = f"{repo}/{skill_dir}"

                # 如果可以通过路径匹配，也可检查 SKILL.md 的 frontmatter name
                # 目前仅按目录名索引
            elif path == "SKILL.md":
                # 根目录下的 SKILL.md
                skill_paths["_root_"] = f"{repo}"

        count = 0
        for entry in entries:
            # 尝试将技能的名称/路径匹配到文件树条目
            skill_name = entry.get("name", "").lower()
            skill_path = entry.get("path", "").lower()
            identifier = entry.get("identifier", "")

            # 从标识符中提取技能令牌
            # 例如 "skills-sh/d4vinci/scrapling/scrapling-official" -> "scrapling-official"
            parts = identifier.replace("skills-sh/", "").replace("skills.sh/", "")
            skill_token = parts.split("/")[-1].lower() if "/" in parts else ""

            # 按匹配可能性从高到低尝试
            for candidate in [skill_token, skill_name, skill_path]:
                if not candidate:
                    continue
                matched = skill_paths.get(candidate)
                if matched:
                    entry["resolved_github_id"] = matched
                    count += 1
                    break
            else:
                # 尝试模糊匹配: 对 skill_token 进行常见变换
                for tree_name, tree_path in skill_paths.items():
                    if (skill_token and (
                        tree_name.replace("-", "") == skill_token.replace("-", "")
                        or skill_token in tree_name
                        or tree_name in skill_token
                    )):
                        entry["resolved_github_id"] = tree_path
                        count += 1
                        break

        return count

    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = {
            pool.submit(_resolve_repo, repo, entries): repo
            for repo, entries in by_repo.items()
        }
        for future in as_completed(futures):
            try:
                resolved_count += future.result()
            except Exception as e:
                repo = futures[future]
                print(f"    Warning: {repo}: {e}", file=sys.stderr)

    elapsed = time.time() - start
    print(f"  Resolved {resolved_count}/{len(skills_sh)} paths ({elapsed:.1f}s)",
          flush=True)
    return skills


def main():
    print("Building Hermes Skills Index...", flush=True)
    overall_start = time.time()

    auth = GitHubAuth()
    print(f"GitHub auth: {auth.auth_method()}")
    if auth.auth_method() == "anonymous":
        print("WARNING: No GitHub authentication — rate limit is 60/hr. "
              "Set GITHUB_TOKEN for better results.", file=sys.stderr)

    skills_sh_source = SkillsShSource(auth=auth)
    sources = {
        "official": OptionalSkillSource(),
        "well-known": WellKnownSkillSource(),
        "github": GitHubSource(auth=auth),
        "clawhub": ClawHubSource(),
        "claude-marketplace": ClaudeMarketplaceSource(auth=auth),
        "lobehub": LobeHubSource(),
    }

    all_skills: list[dict] = []

    # 爬取 skills.sh
    all_skills.extend(crawl_skills_sh(skills_sh_source))

    # 并行爬取其他来源
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {}
        for name, source in sources.items():
            futures[pool.submit(crawl_source, source, name, 500)] = name
        for future in as_completed(futures):
            try:
                all_skills.extend(future.result())
            except Exception as e:
                print(f"  Error: {e}", file=sys.stderr)

    # 批量解析 skills.sh 条目的 GitHub 路径
    all_skills = batch_resolve_paths(all_skills, auth)

    # 按标识符去重
    seen: dict[str, dict] = {}
    for skill in all_skills:
        key = skill["identifier"]
        if key not in seen:
            seen[key] = skill
    deduped = list(seen.values())

    # 排序
    source_order = {"official": 0, "skills-sh": 1, "skills.sh": 1,
                    "github": 2, "well-known": 3, "clawhub": 4,
                    "claude-marketplace": 5, "lobehub": 6}
    deduped.sort(key=lambda s: (source_order.get(s["source"], 99), s["name"]))

    # 构建索引
    index = {
        "version": INDEX_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "skill_count": len(deduped),
        "skills": deduped,
    }

    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    with open(OUTPUT_PATH, "w") as f:
        json.dump(index, f, separators=(",", ":"), ensure_ascii=False)

    elapsed = time.time() - overall_start
    file_size = os.path.getsize(OUTPUT_PATH)
    print(f"\nDone! {len(deduped)} skills indexed in {elapsed:.0f}s")
    print(f"Output: {OUTPUT_PATH} ({file_size / 1024:.0f} KB)")

    from collections import Counter
    by_source = Counter(s["source"] for s in deduped)
    for src, count in sorted(by_source.items(), key=lambda x: -x[1]):
        resolved = sum(1 for s in deduped
                       if s["source"] == src and s.get("resolved_github_id"))
        extra = f" ({resolved} resolved)" if resolved else ""
        print(f"  {src}: {count}{extra}")


if __name__ == "__main__":
    main()
