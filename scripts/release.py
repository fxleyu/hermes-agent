#!/usr/bin/env python3
"""Hermes Agent 发布脚本

生成变更日志并创建带有 CalVer 标签的 GitHub 发布。

用法:
    # 预览变更日志（试运行）
    python scripts/release.py

    # 使用语义版本号升级预览
    python scripts/release.py --bump minor

    # 创建发布
    python scripts/release.py --bump minor --publish

    # 首次发布（无先前标签）
    python scripts/release.py --bump minor --publish --first-release

    # 覆盖 CalVer 日期（例如延迟发布的情况）
    python scripts/release.py --bump minor --publish --date 2026.3.15
"""

import argparse
import re
import shutil
import subprocess
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
VERSION_FILE = REPO_ROOT / "hermes_cli" / "__init__.py"
PYPROJECT_FILE = REPO_ROOT / "pyproject.toml"

# ──────────────────────────────────────────────────────────────────────
# Git 邮箱 → GitHub 用户名映射
# ──────────────────────────────────────────────────────────────────────

# 从 noreply 邮箱自动提取 + 手动覆盖
AUTHOR_MAP = {
    # teknium（多个邮箱地址）
    "teknium1@gmail.com": "teknium1",
    "teknium@nousresearch.com": "teknium1",
    "127238744+teknium1@users.noreply.github.com": "teknium1",
    # 贡献者（从 noreply 模式提取）
    "35742124+0xbyt4@users.noreply.github.com": "0xbyt4",
    "82637225+kshitijk4poor@users.noreply.github.com": "kshitijk4poor",
    "kshitijk4poor@users.noreply.github.com": "kshitijk4poor",
    "16443023+stablegenius49@users.noreply.github.com": "stablegenius49",
    "185121704+stablegenius49@users.noreply.github.com": "stablegenius49",
    "101283333+batuhankocyigit@users.noreply.github.com": "batuhankocyigit",
    "126368201+vilkasdev@users.noreply.github.com": "vilkasdev",
    "137614867+cutepawss@users.noreply.github.com": "cutepawss",
    "96793918+memosr@users.noreply.github.com": "memosr",
    "milkoor@users.noreply.github.com": "milkoor",
    "xuerui911@gmail.com": "Fatty911",
    "131039422+SHL0MS@users.noreply.github.com": "SHL0MS",
    "77628552+raulvidis@users.noreply.github.com": "raulvidis",
    "145567217+Aum08Desai@users.noreply.github.com": "Aum08Desai",
    "256820943+kshitij-eliza@users.noreply.github.com": "kshitij-eliza",
    "44278268+shitcoinsherpa@users.noreply.github.com": "shitcoinsherpa",
    "104278804+Sertug17@users.noreply.github.com": "Sertug17",
    "112503481+caentzminger@users.noreply.github.com": "caentzminger",
    "258577966+voidborne-d@users.noreply.github.com": "voidborne-d",
    "70424851+insecurejezza@users.noreply.github.com": "insecurejezza",
    "259807879+Bartok9@users.noreply.github.com": "Bartok9",
    "241404605+MestreY0d4-Uninter@users.noreply.github.com": "MestreY0d4-Uninter",
    "268667990+Roy-oss1@users.noreply.github.com": "Roy-oss1",
    "27917469+nosleepcassette@users.noreply.github.com": "nosleepcassette",
    "241404605+MestreY0d4-Uninter@users.noreply.github.com": "MestreY0d4-Uninter",
    "109555139+davetist@users.noreply.github.com": "davetist",
    # 贡献者（从 git 名称手动映射）
    "ahmedsherif95@gmail.com": "asheriif",
    "dmayhem93@gmail.com": "dmahan93",
    "samherring99@gmail.com": "samherring99",
    "desaiaum08@gmail.com": "Aum08Desai",
    "shannon.sands.1979@gmail.com": "shannonsands",
    "shannon@nousresearch.com": "shannonsands",
    "eri@plasticlabs.ai": "Erosika",
    "hjcpuro@gmail.com": "hjc-puro",
    "xaydinoktay@gmail.com": "aydnOktay",
    "abdullahfarukozden@gmail.com": "Farukest",
    "lovre.pesut@gmail.com": "rovle",
    "kevinskysunny@gmail.com": "kevinskysunny",
    "hakanerten02@hotmail.com": "teyrebaz33",
    "ruzzgarcn@gmail.com": "Ruzzgar",
    "alireza78.crypto@gmail.com": "alireza78a",
    "brooklyn.bb.nicholson@gmail.com": "brooklynnicholson",
    "4317663+helix4u@users.noreply.github.com": "helix4u",
    "331214+counterposition@users.noreply.github.com": "counterposition",
    "blspear@gmail.com": "BrennerSpear",
    "239876380+handsdiff@users.noreply.github.com": "handsdiff",
    "gpickett00@gmail.com": "gpickett00",
    "mcosma@gmail.com": "wakamex",
    "clawdia.nash@proton.me": "clawdia-nash",
    "pickett.austin@gmail.com": "austinpickett",
    "jaisehgal11299@gmail.com": "jaisup",
    "percydikec@gmail.com": "PercyDikec",
    "dean.kerr@gmail.com": "deankerr",
    "socrates1024@gmail.com": "socrates1024",
    "satelerd@gmail.com": "satelerd",
    "numman.ali@gmail.com": "nummanali",
    "0xNyk@users.noreply.github.com": "0xNyk",
    "0xnykcd@googlemail.com": "0xNyk",
    "buraysandro9@gmail.com": "buray",
    "contact@jomar.fr": "joshmartinelle",
    "camilo@tekelala.com": "tekelala",
    "vincentcharlebois@gmail.com": "vincentcharlebois",
    "aryan@synvoid.com": "aryansingh",
    "johnsonblake1@gmail.com": "blakejohnson",
    "greer.guthrie@gmail.com": "g-guthrie",
    "kennyx102@gmail.com": "bobashopcashier",
    "shokatalishaikh95@gmail.com": "areu01or00",
    "bryan@intertwinesys.com": "bryanyoung",
    "christo.mitov@gmail.com": "christomitov",
    "hermes@nousresearch.com": "NousResearch",
    "chinmingcock@gmail.com": "ChimingLiu",
    "openclaw@sparklab.ai": "openclaw",
    "semihcvlk53@gmail.com": "Himess",
    "erenkar950@gmail.com": "erenkarakus",
    "adavyasharma@gmail.com": "adavyas",
    "acaayush1111@gmail.com": "aayushchaudhary",
    "jason@outland.art": "jasonoutland",
    "mrflu1918@proton.me": "SPANISHFLU",
    "morganemoss@gmai.com": "mormio",
    "kopjop926@gmail.com": "cesareth",
    "fuleinist@gmail.com": "fuleinist",
    "jack.47@gmail.com": "JackTheGit",
    "dalvidjr2022@gmail.com": "Jr-kenny",
    "m@statecraft.systems": "mbierling",
    "balyan.sid@gmail.com": "balyansid",
    "oluwadareab12@gmail.com": "bennytimz",
    "simon@simonmarcus.org": "simon-marcus",
    "xowiekk@gmail.com": "Xowiek",
    "1243352777@qq.com": "zons-zhaozhy",
    # ── 批量添加: 通过 API、PR 内容、noreply 交叉比对和 GH 贡献者列表
    #    匹配解析了 75 个邮箱（2026 年 4 月审计） ──
    "1115117931@qq.com": "aaronagent",
    "1506751656@qq.com": "hqhq1025",
    "364939526@qq.com": "luyao618",
    "aaronwong1999@icloud.com": "AaronWong1999",
    "agents@kylefrench.dev": "DeployFaith",
    "angelos@oikos.lan.home.malaiwah.com": "angelos",
    "aptx4561@gmail.com": "cokemine",
    "arilotter@gmail.com": "ethernet8023",
    "ben@nousresearch.com": "benbarclay",
    "birdiegyal@gmail.com": "yyovil",
    "boschi1997@gmail.com": "nicoloboschi",
    "chef.ya@gmail.com": "cherifya",
    "chlqhdtn98@gmail.com": "BongSuCHOI",
    "coffeemjj@gmail.com": "Cafexss",
    "dalianmao0107@gmail.com": "dalianmao000",
    "der@konsi.org": "konsisumer",
    "dgrieco@redhat.com": "DomGrieco",
    "dhicham.pro@gmail.com": "spideystreet",
    "dipp.who@gmail.com": "dippwho",
    "don.rhm@gmail.com": "donrhmexe",
    "dorukardahan@hotmail.com": "dorukardahan",
    "dsocolobsky@gmail.com": "dsocolobsky",
    "duerzy@gmail.com": "duerzy",
    "emozilla@nousresearch.com": "emozilla",
    "fancydirty@gmail.com": "fancydirty",
    "floptopbot33@gmail.com": "flobo3",
    "fontana.pedro93@gmail.com": "pefontana",
    "francis.x.fitzpatrick@gmail.com": "fxfitz",
    "frank@helmschrott.de": "Helmi",
    "gaixg94@gmail.com": "gaixianggeng",
    "geoff.wellman@gmail.com": "geoffwellman",
    "han.shan@live.cn": "jamesarch",
    "haolong@microsoft.com": "LongOddCode",
    "hata1234@gmail.com": "hata1234",
    "hmbown@gmail.com": "Hmbown",
    "iacobs@m0n5t3r.info": "m0n5t3r",
    "jiayuw794@gmail.com": "JiayuuWang",
    "jonny@nousresearch.com": "jquesnelle",
    "juan.ovalle@mistral.ai": "jjovalle99",
    "julien.talbot@ergonomia.re": "Julientalbot",
    "kagura.chen28@gmail.com": "kagura-agent",
    "kamil@gwozdz.me": "kamil-gwozdz",
    "karamusti912@gmail.com": "MustafaKara7",
    "kira@ariaki.me": "kira-ariaki",
    "knopki@duck.com": "knopki",
    "limars874@gmail.com": "limars874",
    "lisicheng168@gmail.com": "lesterli",
    "mingjwan@microsoft.com": "MagicRay1217",
    "orangeko@gmail.com": "GenKoKo",
    "82095453+iacker@users.noreply.github.com": "iacker",
    "sontianye@users.noreply.github.com": "sontianye",
    "jackjin1997@users.noreply.github.com": "jackjin1997",
    "danieldoderlein@users.noreply.github.com": "danieldoderlein",
    "lrawnsley@users.noreply.github.com": "lrawnsley",
    "taeuk178@users.noreply.github.com": "taeuk178",
    "ogzerber@users.noreply.github.com": "ogzerber",
    "cola-runner@users.noreply.github.com": "cola-runner",
    "ygd58@users.noreply.github.com": "ygd58",
    "vominh1919@users.noreply.github.com": "vominh1919",
    "trevmanthony@gmail.com": "trevthefoolish",
    "ziliangpeng@users.noreply.github.com": "ziliangpeng",
    "centripetal-star@users.noreply.github.com": "centripetal-star",
    "LeonSGP43@users.noreply.github.com": "LeonSGP43",
    "Lubrsy706@users.noreply.github.com": "Lubrsy706",
    "niyant@spicefi.xyz": "spniyant",
    "olafthiele@gmail.com": "olafthiele",
    "oncuevtv@gmail.com": "sprmn24",
    "programming@olafthiele.com": "olafthiele",
    "r2668940489@gmail.com": "r266-tech",
    "s5460703@gmail.com": "BlackishGreen33",
    "saul.jj.wu@gmail.com": "SaulJWu",
    "shenhaocheng19990111@gmail.com": "hcshen0111",
    "sjtuwbh@gmail.com": "Cygra",
    "srhtsrht17@gmail.com": "Sertug17",
    "stephenschoettler@gmail.com": "stephenschoettler",
    "tanishq231003@gmail.com": "yyovil",
    "tesseracttars@gmail.com": "tesseracttars-creator",
    "tianliangjay@gmail.com": "xingkongliang",
    "tranquil_flow@protonmail.com": "Tranquil-Flow",
    "unayung@gmail.com": "Unayung",
    "vorvul.danylo@gmail.com": "WorldInnovationsDepartment",
    "win4r@outlook.com": "win4r",
    "xush@xush.org": "KUSH42",
    "yangzhi.see@gmail.com": "SeeYangZhi",
    "yongtenglei@gmail.com": "yongtenglei",
    "young@YoungdeMacBook-Pro.local": "YoungYang963",
    "ysfalweshcan@gmail.com": "Junass1",
    "ysfwaxlycan@gmail.com": "WAXLYY",
    "yusufalweshdemir@gmail.com": "Dusk1e",
    "zhouboli@gmail.com": "zhouboli",
    "zqiao@microsoft.com": "tomqiaozc",
    "zzn+pa@zzn.im": "xinbenlv",
    "zaynjarvis@gmail.com": "ZaynJarvis",
    "zhiheng.liu@bytedance.com": "ZaynJarvis",
    "mbelleau@Michels-MacBook-Pro.local": "malaiwah",
    "dhandhalyabhavik@gmail.com": "v1k22",
}


def git(*args, cwd=None):
    """运行 git 命令并返回 stdout 输出。"""
    result = subprocess.run(
        ["git"] + list(args),
        capture_output=True, text=True,
        cwd=cwd or str(REPO_ROOT),
    )
    if result.returncode != 0:
        print(f"git {' '.join(args)} failed: {result.stderr}", file=sys.stderr)
        return ""
    return result.stdout.strip()


def git_result(*args, cwd=None):
    """运行 git 命令并返回完整的 CompletedProcess 对象。"""
    return subprocess.run(
        ["git"] + list(args),
        capture_output=True,
        text=True,
        cwd=cwd or str(REPO_ROOT),
    )


def get_last_tag():
    """获取最近的 CalVer 标签。"""
    tags = git("tag", "--list", "v20*", "--sort=-v:refname")
    if tags:
        return tags.split("\n")[0]
    return None


def next_available_tag(base_tag: str) -> tuple[str, str]:
    """返回标签/CalVer 对，同一天多次发布时添加后缀。"""
    if not git("tag", "--list", base_tag):
        return base_tag, base_tag.removeprefix("v")

    suffix = 2
    while git("tag", "--list", f"{base_tag}.{suffix}"):
        suffix += 1
    tag_name = f"{base_tag}.{suffix}"
    return tag_name, tag_name.removeprefix("v")


def get_current_version():
    """从 __init__.py 中读取当前语义版本号。"""
    content = VERSION_FILE.read_text()
    match = re.search(r'__version__\s*=\s*"([^"]+)"', content)
    return match.group(1) if match else "0.0.0"


def bump_version(current: str, part: str) -> str:
    """升级语义版本号字符串。"""
    parts = current.split(".")
    if len(parts) != 3:
        parts = ["0", "0", "0"]
    major, minor, patch = int(parts[0]), int(parts[1]), int(parts[2])

    if part == "major":
        major += 1
        minor = 0
        patch = 0
    elif part == "minor":
        minor += 1
        patch = 0
    elif part == "patch":
        patch += 1
    else:
        raise ValueError(f"Unknown bump part: {part}")

    return f"{major}.{minor}.{patch}"


def update_version_files(semver: str, calver_date: str):
    """更新源文件中的版本号字符串。"""
    # 更新 __init__.py
    content = VERSION_FILE.read_text()
    content = re.sub(
        r'__version__\s*=\s*"[^"]+"',
        f'__version__ = "{semver}"',
        content,
    )
    content = re.sub(
        r'__release_date__\s*=\s*"[^"]+"',
        f'__release_date__ = "{calver_date}"',
        content,
    )
    VERSION_FILE.write_text(content)

    # 更新 pyproject.toml
    pyproject = PYPROJECT_FILE.read_text()
    pyproject = re.sub(
        r'^version\s*=\s*"[^"]+"',
        f'version = "{semver}"',
        pyproject,
        flags=re.MULTILINE,
    )
    PYPROJECT_FILE.write_text(pyproject)


def build_release_artifacts(semver: str) -> list[Path]:
    """为当前发布构建 sdist/wheel 制品。

    当本地环境有 ``python -m build`` 可用时返回制品路径。
    如果构建工具缺失或构建失败，返回空列表并让发布继续进行，
    不附带 Python 制品。
    """
    dist_dir = REPO_ROOT / "dist"
    shutil.rmtree(dist_dir, ignore_errors=True)

    result = subprocess.run(
        [sys.executable, "-m", "build", "--sdist", "--wheel"],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print("  ⚠ Could not build Python release artifacts.")
        stderr = result.stderr.strip()
        stdout = result.stdout.strip()
        if stderr:
            print(f"    {stderr.splitlines()[-1]}")
        elif stdout:
            print(f"    {stdout.splitlines()[-1]}")
        print("    Install the 'build' package to attach semver-named sdist/wheel assets.")
        return []

    artifacts = sorted(p for p in dist_dir.iterdir() if p.is_file())
    matching = [p for p in artifacts if semver in p.name]
    if not matching:
        print("  ⚠ Built artifacts did not match the expected release version.")
        return []
    return matching


def resolve_author(name: str, email: str) -> str:
    """将 git 作者解析为 GitHub @mention。"""
    # 先尝试邮箱查找
    gh_user = AUTHOR_MAP.get(email)
    if gh_user:
        return f"@{gh_user}"

    # 尝试 noreply 模式
    noreply_match = re.match(r"(\d+)\+(.+)@users\.noreply\.github\.com", email)
    if noreply_match:
        return f"@{noreply_match.group(2)}"

    # 尝试 username@users.noreply.github.com 格式
    noreply_match2 = re.match(r"(.+)@users\.noreply\.github\.com", email)
    if noreply_match2:
        return f"@{noreply_match2.group(1)}"

    # 回退使用 git 名称
    return name


def categorize_commit(subject: str) -> str:
    """按约定式提交前缀对提交进行分类。"""
    subject_lower = subject.lower()

    # 匹配约定式提交模式
    patterns = {
        "breaking": [r"^breaking[\s:(]", r"^!:", r"BREAKING CHANGE"],
        "features": [r"^feat[\s:(]", r"^feature[\s:(]", r"^add[\s:(]"],
        "fixes": [r"^fix[\s:(]", r"^bugfix[\s:(]", r"^bug[\s:(]", r"^hotfix[\s:(]"],
        "improvements": [r"^improve[\s:(]", r"^perf[\s:(]", r"^enhance[\s:(]",
                         r"^refactor[\s:(]", r"^cleanup[\s:(]", r"^clean[\s:(]",
                         r"^update[\s:(]", r"^optimize[\s:(]"],
        "docs": [r"^doc[\s:(]", r"^docs[\s:(]"],
        "tests": [r"^test[\s:(]", r"^tests[\s:(]"],
        "chore": [r"^chore[\s:(]", r"^ci[\s:(]", r"^build[\s:(]",
                  r"^deps[\s:(]", r"^bump[\s:(]"],
    }

    for category, regexes in patterns.items():
        for regex in regexes:
            if re.match(regex, subject_lower):
                return category

    # 启发式回退规则
    if any(w in subject_lower for w in ["add ", "new ", "implement", "support "]):
        return "features"
    if any(w in subject_lower for w in ["fix ", "fixed ", "resolve", "patch "]):
        return "fixes"
    if any(w in subject_lower for w in ["refactor", "cleanup", "improve", "update "]):
        return "improvements"

    return "other"


def clean_subject(subject: str) -> str:
    """清理提交标题以便显示。"""
    # 移除约定式提交前缀
    cleaned = re.sub(r"^(feat|fix|docs|chore|refactor|test|perf|ci|build|improve|add|update|cleanup|hotfix|breaking|enhance|optimize|bugfix|bug|feature|tests|deps|bump)[\s:(!]+\s*", "", subject, flags=re.IGNORECASE)
    # 移除与 PR 链接重复的尾部 issue 引用
    cleaned = cleaned.strip()
    # 首字母大写
    if cleaned:
        cleaned = cleaned[0].upper() + cleaned[1:]
    return cleaned


def parse_coauthors(body: str) -> list:
    """从提交消息正文中提取 Co-authored-by 尾注。

    返回 {'name': ..., 'email': ...} 字典列表。
    过滤掉 AI 助手和机器人（Claude、Copilot、Cursor 等）。
    """
    if not body:
        return []
    # 需要忽略的 AI/机器人 co-author 尾注中的邮箱
    _ignored_emails = {"noreply@anthropic.com", "noreply@github.com",
                       "cursoragent@cursor.com", "hermes@nousresearch.com"}
    _ignored_names = re.compile(r"^(Claude|Copilot|Cursor Agent|GitHub Actions?|dependabot|renovate)", re.IGNORECASE)
    pattern = re.compile(r"Co-authored-by:\s*(.+?)\s*<([^>]+)>", re.IGNORECASE)
    results = []
    for m in pattern.finditer(body):
        name, email = m.group(1).strip(), m.group(2).strip()
        if email in _ignored_emails or _ignored_names.match(name):
            continue
        results.append({"name": name, "email": email})
    return results


def get_commits(since_tag=None):
    """获取指定标签之后的提交记录（如果为 None 则获取所有提交）。"""
    if since_tag:
        range_spec = f"{since_tag}..HEAD"
    else:
        range_spec = "HEAD"

    # 格式: hash|author_name|author_email|subject\0body
    # 使用 %x00（空字节）作为 subject 和 body 之间的分隔符
    log = git(
        "log", range_spec,
        "--format=%H|%an|%ae|%s%x00%b%x00",
        "--no-merges",
    )

    if not log:
        return []

    commits = []
    # 按双空字节分割以获取每个提交条目，因为 body 以 \0 结尾
    # 且格式也以 \0 结尾，所以每条记录之间有 \0\0
    for entry in log.split("\0\0"):
        entry = entry.strip()
        if not entry:
            continue
        # 按首个空字节分割，将 "hash|name|email|subject" 与 "body" 分开
        if "\0" in entry:
            header, body = entry.split("\0", 1)
            body = body.strip()
        else:
            header = entry
            body = ""
        parts = header.split("|", 3)
        if len(parts) != 4:
            continue
        sha, name, email, subject = parts
        coauthor_info = parse_coauthors(body)
        coauthors = [resolve_author(ca["name"], ca["email"]) for ca in coauthor_info]
        commits.append({
            "sha": sha,
            "short_sha": sha[:8],
            "author_name": name,
            "author_email": email,
            "subject": subject,
            "category": categorize_commit(subject),
            "github_author": resolve_author(name, email),
            "coauthors": coauthors,
        })

    return commits


def get_pr_number(subject: str) -> str:
    """从提交标题中提取 PR 编号（如果存在）。"""
    match = re.search(r"#(\d+)", subject)
    if match:
        return match.group(1)
    return None


def generate_changelog(commits, tag_name, semver, repo_url="https://github.com/NousResearch/hermes-agent",
                       prev_tag=None, first_release=False):
    """从分类后的提交生成 Markdown 变更日志。"""
    lines = []

    # 标题头部
    now = datetime.now()
    date_str = now.strftime("%B %d, %Y")
    lines.append(f"# Hermes Agent v{semver} ({tag_name})")
    lines.append("")
    lines.append(f"**Release Date:** {date_str}")
    lines.append("")

    if first_release:
        lines.append("> 🎉 **First official release!** This marks the beginning of regular weekly releases")
        lines.append("> for Hermes Agent. See below for everything included in this initial release.")
        lines.append("")

    # 按类别分组提交
    categories = defaultdict(list)
    all_authors = set()
    teknium_aliases = {"@teknium1"}

    for commit in commits:
        categories[commit["category"]].append(commit)
        author = commit["github_author"]
        if author not in teknium_aliases:
            all_authors.add(author)
        for coauthor in commit.get("coauthors", []):
            if coauthor not in teknium_aliases:
                all_authors.add(coauthor)

    # 类别显示顺序和图标
    category_order = [
        ("breaking", "⚠️ Breaking Changes"),
        ("features", "✨ Features"),
        ("improvements", "🔧 Improvements"),
        ("fixes", "🐛 Bug Fixes"),
        ("docs", "📚 Documentation"),
        ("tests", "🧪 Tests"),
        ("chore", "🏗️ Infrastructure"),
        ("other", "📦 Other Changes"),
    ]

    for cat_key, cat_title in category_order:
        cat_commits = categories.get(cat_key, [])
        if not cat_commits:
            continue

        lines.append(f"## {cat_title}")
        lines.append("")

        for commit in cat_commits:
            subject = clean_subject(commit["subject"])
            pr_num = get_pr_number(commit["subject"])
            author = commit["github_author"]

            # 构建每行内容
            parts = [f"- {subject}"]
            if pr_num:
                parts.append(f"([#{pr_num}]({repo_url}/pull/{pr_num}))")
            else:
                parts.append(f"([`{commit['short_sha']}`]({repo_url}/commit/{commit['sha']}))")

            if author not in teknium_aliases:
                parts.append(f"— {author}")

            lines.append(" ".join(parts))

        lines.append("")

    # 贡献者部分
    if all_authors:
        # 按提交数量排序贡献者
        author_counts = defaultdict(int)
        for commit in commits:
            author = commit["github_author"]
            if author not in teknium_aliases:
                author_counts[author] += 1
            for coauthor in commit.get("coauthors", []):
                if coauthor not in teknium_aliases:
                    author_counts[coauthor] += 1

        sorted_authors = sorted(author_counts.items(), key=lambda x: -x[1])

        lines.append("## 👥 Contributors")
        lines.append("")
        lines.append("Thank you to everyone who contributed to this release!")
        lines.append("")
        for author, count in sorted_authors:
            commit_word = "commit" if count == 1 else "commits"
            lines.append(f"- {author} ({count} {commit_word})")
        lines.append("")

    # 完整变更日志链接
    if prev_tag:
        lines.append(f"**Full Changelog**: [{prev_tag}...{tag_name}]({repo_url}/compare/{prev_tag}...{tag_name})")
    else:
        lines.append(f"**Full Changelog**: [{tag_name}]({repo_url}/commits/{tag_name})")
    lines.append("")

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Hermes Agent Release Tool")
    parser.add_argument("--bump", choices=["major", "minor", "patch"],
                        help="Which semver component to bump")
    parser.add_argument("--publish", action="store_true",
                        help="Actually create the tag and GitHub release (otherwise dry run)")
    parser.add_argument("--date", type=str,
                        help="Override CalVer date (format: YYYY.M.D)")
    parser.add_argument("--first-release", action="store_true",
                        help="Mark as first release (no previous tag expected)")
    parser.add_argument("--output", type=str,
                        help="Write changelog to file instead of stdout")
    args = parser.parse_args()

    # 确定 CalVer 日期
    if args.date:
        calver_date = args.date
    else:
        now = datetime.now()
        calver_date = f"{now.year}.{now.month}.{now.day}"

    base_tag = f"v{calver_date}"
    tag_name, calver_date = next_available_tag(base_tag)
    if tag_name != base_tag:
        print(f"Note: Tag {base_tag} already exists, using {tag_name}")

    # 确定语义版本号
    current_version = get_current_version()
    if args.bump:
        new_version = bump_version(current_version, args.bump)
    else:
        new_version = current_version

    # 获取上一个标签
    prev_tag = get_last_tag()
    if not prev_tag and not args.first_release:
        print("No previous tags found. Use --first-release for the initial release.")
        print(f"Would create tag: {tag_name}")
        print(f"Would set version: {new_version}")

    # 获取提交记录
    commits = get_commits(since_tag=prev_tag)
    if not commits:
        print("No new commits since last tag.")
        if not args.first_release:
            return

    print(f"{'='*60}")
    print(f"  Hermes Agent Release Preview")
    print(f"{'='*60}")
    print(f"  CalVer tag:      {tag_name}")
    print(f"  SemVer:          v{current_version} → v{new_version}")
    print(f"  Previous tag:    {prev_tag or '(none — first release)'}")
    print(f"  Commits:         {len(commits)}")
    print(f"  Unique authors:  {len(set(c['github_author'] for c in commits))}")
    print(f"  Mode:            {'PUBLISH' if args.publish else 'DRY RUN'}")
    print(f"{'='*60}")
    print()

    # 生成变更日志
    changelog = generate_changelog(
        commits, tag_name, new_version,
        prev_tag=prev_tag,
        first_release=args.first_release,
    )

    if args.output:
        Path(args.output).write_text(changelog)
        print(f"Changelog written to {args.output}")
    else:
        print(changelog)

    if args.publish:
        print(f"\n{'='*60}")
        print("  Publishing release...")
        print(f"{'='*60}")

        # 更新版本号文件
        if args.bump:
            update_version_files(new_version, calver_date)
            print(f"  ✓ Updated version files to v{new_version} ({calver_date})")

            # 提交版本号升级
            add_result = git_result("add", str(VERSION_FILE), str(PYPROJECT_FILE))
            if add_result.returncode != 0:
                print(f"  ✗ Failed to stage version files: {add_result.stderr.strip()}")
                return

            commit_result = git_result(
                "commit", "-m", f"chore: bump version to v{new_version} ({calver_date})"
            )
            if commit_result.returncode != 0:
                print(f"  ✗ Failed to commit version bump: {commit_result.stderr.strip()}")
                return
            print(f"  ✓ Committed version bump")

        # 创建带注释的标签
        tag_result = git_result(
            "tag", "-a", tag_name, "-m",
            f"Hermes Agent v{new_version} ({calver_date})\n\nWeekly release"
        )
        if tag_result.returncode != 0:
            print(f"  ✗ Failed to create tag {tag_name}: {tag_result.stderr.strip()}")
            return
        print(f"  ✓ Created tag {tag_name}")

        # 推送到远程仓库
        push_result = git_result("push", "origin", "HEAD", "--tags")
        if push_result.returncode == 0:
            print(f"  ✓ Pushed to origin")
        else:
            print(f"  ✗ Failed to push to origin: {push_result.stderr.strip()}")
            print("    Continue manually after fixing access:")
            print("    git push origin HEAD --tags")

        # 构建带语义版本号命名的 Python 制品，以便下游打包工具
        # （如 Homebrew）可以通过它们来定位，而不依赖 CalVer 标签名。
        artifacts = build_release_artifacts(new_version)
        if artifacts:
            print("  ✓ Built release artifacts:")
            for artifact in artifacts:
                print(f"    - {artifact.relative_to(REPO_ROOT)}")

        # 创建 GitHub 发布
        changelog_file = REPO_ROOT / ".release_notes.md"
        changelog_file.write_text(changelog)

        gh_cmd = [
            "gh", "release", "create", tag_name,
            "--title", f"Hermes Agent v{new_version} ({calver_date})",
            "--notes-file", str(changelog_file),
        ]
        gh_cmd.extend(str(path) for path in artifacts)

        gh_bin = shutil.which("gh")
        if gh_bin:
            result = subprocess.run(
                gh_cmd,
                capture_output=True, text=True,
                cwd=str(REPO_ROOT),
            )
        else:
            result = None

        if result and result.returncode == 0:
            changelog_file.unlink(missing_ok=True)
            print(f"  ✓ GitHub release created: {result.stdout.strip()}")
            print(f"\n  🎉 Release v{new_version} ({tag_name}) published!")
        else:
            if result is None:
                print("  ✗ GitHub release skipped: `gh` CLI not found.")
            else:
                print(f"  ✗ GitHub release failed: {result.stderr.strip()}")
            print(f"    Release notes kept at: {changelog_file}")
            print(f"    Tag was created locally. Create the release manually:")
            print(
                f"    gh release create {tag_name} --title 'Hermes Agent v{new_version} ({calver_date})' "
                f"--notes-file .release_notes.md {' '.join(str(path) for path in artifacts)}"
            )
            print(f"\n  ✓ Release artifacts prepared for manual publish: v{new_version} ({tag_name})")
    else:
        print(f"\n{'='*60}")
        print(f"  Dry run complete. To publish, add --publish")
        print(f"  Example: python scripts/release.py --bump minor --publish")
        print(f"{'='*60}")


if __name__ == "__main__":
    main()
