#!/usr/bin/env python3
"""Discord 语音诊断工具 — 语音频道支持的诊断工具。

检查 Discord 语音模式正常工作所需的所有依赖项、配置和机器人权限。

用法:
    python scripts/discord-voice-doctor.py
    .venv/bin/python scripts/discord-voice-doctor.py
"""

import os
import sys
import shutil
from pathlib import Path

# 解析项目根目录
SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(PROJECT_ROOT))

HERMES_HOME = Path(os.getenv("HERMES_HOME", Path.home() / ".hermes"))
ENV_FILE = HERMES_HOME / ".env"

OK = "\033[92m\u2713\033[0m"
FAIL = "\033[91m\u2717\033[0m"
WARN = "\033[93m!\033[0m"

# 跟踪 discord.py 是否可用，供后续检查使用
_discord_available = False


def mask(value):
    """遮蔽敏感值：仅显示前 4 个字符。"""
    if not value or len(value) < 8:
        return "****"
    return f"{value[:4]}{'*' * (len(value) - 4)}"


def check(label, ok, detail=""):
    symbol = OK if ok else FAIL
    msg = f"  {symbol} {label}"
    if detail:
        msg += f"  ({detail})"
    print(msg)
    return ok


def warn(label, detail=""):
    msg = f"  {WARN} {label}"
    if detail:
        msg += f"  ({detail})"
    print(msg)


def section(title):
    print(f"\n\033[1m{title}\033[0m")


def check_packages():
    """检查 Python 包依赖。如果所有关键依赖都正常则返回 True。"""
    global _discord_available
    section("Python Packages")
    ok = True

    # discord.py 包
    try:
        import discord
        _discord_available = True
        check("discord.py", True, f"v{discord.__version__}")
    except ImportError:
        check("discord.py", False, "pip install discord.py[voice]")
        ok = False

    # PyNaCl
    try:
        import nacl
        ver = getattr(nacl, "__version__", "unknown")
        try:
            import nacl.secret
            nacl.secret.Aead(bytes(32))
            check("PyNaCl", True, f"v{ver}")
        except (AttributeError, Exception):
            check("PyNaCl (Aead)", False, f"v{ver} — need >=1.5.0")
            ok = False
    except ImportError:
        check("PyNaCl", False, "pip install PyNaCl>=1.5.0")
        ok = False

    # davey (DAVE E2EE)
    try:
        import davey
        check("davey (DAVE E2EE)", True, f"v{getattr(davey, '__version__', '?')}")
    except ImportError:
        check("davey (DAVE E2EE)", False, "pip install davey")
        ok = False

    # 可选：本地语音转文字
    try:
        import faster_whisper
        check("faster-whisper (local STT)", True)
    except ImportError:
        warn("faster-whisper (local STT)", "not installed — local STT unavailable")

    # 可选：文字转语音提供者
    try:
        import edge_tts
        check("edge-tts", True)
    except ImportError:
        warn("edge-tts", "not installed — edge TTS unavailable")

    try:
        import elevenlabs
        check("elevenlabs SDK", True)
    except ImportError:
        warn("elevenlabs SDK", "not installed — premium TTS unavailable")

    return ok


def check_system_tools():
    """检查系统级工具（opus、ffmpeg）。如果全部正常则返回 True。"""
    section("System Tools")
    ok = True

    # Opus 编解码器
    if _discord_available:
        try:
            import discord
            opus_loaded = discord.opus.is_loaded()
            if not opus_loaded:
                import ctypes.util
                opus_path = ctypes.util.find_library("opus")
                if not opus_path:
                    # 特定平台的回退路径
                    candidates = [
                        "/opt/homebrew/lib/libopus.dylib",   # macOS Apple Silicon 芯片
                        "/usr/local/lib/libopus.dylib",      # macOS Intel 芯片
                        "/usr/lib/x86_64-linux-gnu/libopus.so.0",  # Debian/Ubuntu x86 架构
                        "/usr/lib/aarch64-linux-gnu/libopus.so.0", # Debian/Ubuntu ARM 架构
                        "/usr/lib/libopus.so",               # Arch Linux
                        "/usr/lib64/libopus.so",             # RHEL/Fedora
                    ]
                    for p in candidates:
                        if os.path.isfile(p):
                            opus_path = p
                            break
                if opus_path:
                    discord.opus.load_opus(opus_path)
                    opus_loaded = discord.opus.is_loaded()
            if opus_loaded:
                check("Opus codec", True)
            else:
                check("Opus codec", False, "brew install opus / apt install libopus0")
                ok = False
        except Exception as e:
            check("Opus codec", False, str(e))
            ok = False
    else:
        warn("Opus codec", "skipped — discord.py not installed")

    # ffmpeg
    ffmpeg_path = shutil.which("ffmpeg")
    if ffmpeg_path:
        check("ffmpeg", True, ffmpeg_path)
    else:
        check("ffmpeg", False, "brew install ffmpeg / apt install ffmpeg")
        ok = False

    return ok


def check_env_vars():
    """检查环境变量。返回 (ok, token, groq_key, eleven_key)。"""
    section("Environment Variables")

    # 加载 .env 文件
    try:
        from dotenv import load_dotenv
        if ENV_FILE.exists():
            load_dotenv(ENV_FILE)
    except ImportError:
        pass

    ok = True

    token = os.getenv("DISCORD_BOT_TOKEN", "")
    if token:
        check("DISCORD_BOT_TOKEN", True, mask(token))
    else:
        check("DISCORD_BOT_TOKEN", False, "not set")
        ok = False

    # 允许的用户 — 如果可能的话解析用户名
    allowed = os.getenv("DISCORD_ALLOWED_USERS", "")
    if allowed:
        users = [u.strip() for u in allowed.split(",") if u.strip()]
        user_labels = []
        for uid in users:
            label = mask(uid)
            if token and uid.isdigit():
                try:
                    import requests
                    r = requests.get(
                        f"https://discord.com/api/v10/users/{uid}",
                        headers={"Authorization": f"Bot {token}"},
                        timeout=3,
                    )
                    if r.status_code == 200:
                        label = f"{r.json().get('username', '?')} ({mask(uid)})"
                except Exception:
                    pass
            user_labels.append(label)
        check("DISCORD_ALLOWED_USERS", True, f"{len(users)} user(s): {', '.join(user_labels)}")
    else:
        warn("DISCORD_ALLOWED_USERS", "not set — all users can use voice")

    groq_key = os.getenv("GROQ_API_KEY", "")
    eleven_key = os.getenv("ELEVENLABS_API_KEY", "")

    if groq_key:
        check("GROQ_API_KEY (STT)", True, mask(groq_key))
    else:
        warn("GROQ_API_KEY", "not set — Groq STT unavailable")

    if eleven_key:
        check("ELEVENLABS_API_KEY (TTS)", True, mask(eleven_key))
    else:
        warn("ELEVENLABS_API_KEY", "not set — ElevenLabs TTS unavailable")

    return ok, token, groq_key, eleven_key


def check_config(groq_key, eleven_key):
    """检查 hermes config.yaml 配置文件。"""
    section("Configuration")

    config_path = HERMES_HOME / "config.yaml"
    if config_path.exists():
        try:
            import yaml
            with open(config_path) as f:
                cfg = yaml.safe_load(f) or {}

            stt_provider = cfg.get("stt", {}).get("provider", "local")
            tts_provider = cfg.get("tts", {}).get("provider", "edge")
            check("STT provider", True, stt_provider)
            check("TTS provider", True, tts_provider)

            if stt_provider == "groq" and not groq_key:
                warn("STT config says groq but GROQ_API_KEY is missing")
            if stt_provider == "mistral" and not os.getenv("MISTRAL_API_KEY"):
                warn("STT config says mistral but MISTRAL_API_KEY is missing")
            if tts_provider == "elevenlabs" and not eleven_key:
                warn("TTS config says elevenlabs but ELEVENLABS_API_KEY is missing")
            if tts_provider == "mistral" and not os.getenv("MISTRAL_API_KEY"):
                warn("TTS config says mistral but MISTRAL_API_KEY is missing")
        except Exception as e:
            warn("config.yaml", f"parse error: {e}")
    else:
        warn("config.yaml", "not found — using defaults")

    # 语音模式状态
    voice_mode_path = HERMES_HOME / "gateway_voice_mode.json"
    if voice_mode_path.exists():
        try:
            import json
            modes = json.loads(voice_mode_path.read_text())
            off_count = sum(1 for v in modes.values() if v == "off")
            all_count = sum(1 for v in modes.values() if v == "all")
            check("Voice mode state", True, f"{all_count} on, {off_count} off, {len(modes)} total")
        except Exception:
            warn("Voice mode state", "parse error")
    else:
        check("Voice mode state", True, "no saved state (fresh)")


def check_bot_permissions(token):
    """通过 Discord API 检查机器人权限。如果全部正常则返回 True。"""
    section("Bot Permissions")

    if not token:
        warn("Bot permissions", "no token — skipping")
        return True

    try:
        import requests
    except ImportError:
        warn("Bot permissions", "requests not installed — skipping")
        return True

    VOICE_PERMS = {
        "Priority Speaker":      8,
        "Stream":                9,
        "View Channel":         10,
        "Send Messages":        11,
        "Embed Links":          14,
        "Attach Files":         15,
        "Read Message History": 16,
        "Connect":              20,
        "Speak":                21,
        "Mute Members":         22,
        "Deafen Members":       23,
        "Move Members":         24,
        "Use VAD":              25,
        "Send Voice Messages":  46,
    }
    REQUIRED_PERMS = {"Connect", "Speak", "View Channel", "Send Messages"}
    ok = True

    try:
        headers = {"Authorization": f"Bot {token}"}
        r = requests.get("https://discord.com/api/v10/users/@me", headers=headers, timeout=5)

        if r.status_code == 401:
            check("Bot login", False, "invalid token (401)")
            return False
        if r.status_code != 200:
            check("Bot login", False, f"HTTP {r.status_code}")
            return False

        bot = r.json()
        bot_name = bot.get("username", "?")
        check("Bot login", True, f"{bot_name[:3]}{'*' * (len(bot_name) - 3)}")

        # 检查服务器
        r2 = requests.get("https://discord.com/api/v10/users/@me/guilds", headers=headers, timeout=5)
        if r2.status_code != 200:
            warn("Guilds", f"HTTP {r2.status_code}")
            return ok

        guilds = r2.json()
        check("Guilds", True, f"{len(guilds)} guild(s)")

        for g in guilds[:5]:
            perms = int(g.get("permissions", 0))
            is_admin = bool(perms & (1 << 3))

            if is_admin:
                print(f"    {OK} {g['name']}: Administrator (all permissions)")
                continue

            has = []
            missing = []
            for name, bit in sorted(VOICE_PERMS.items(), key=lambda x: x[1]):
                if perms & (1 << bit):
                    has.append(name)
                elif name in REQUIRED_PERMS:
                    missing.append(name)

            if missing:
                print(f"    {FAIL} {g['name']}: missing {', '.join(missing)}")
                ok = False
            else:
                print(f"    {OK} {g['name']}: {', '.join(has)}")

    except requests.exceptions.Timeout:
        warn("Bot permissions", "Discord API timeout")
    except requests.exceptions.ConnectionError:
        warn("Bot permissions", "cannot reach Discord API")
    except Exception as e:
        warn("Bot permissions", f"check failed: {e}")

    return ok


def main():
    print()
    print("\033[1m" + "=" * 50 + "\033[0m")
    print("\033[1m  Discord Voice Doctor\033[0m")
    print("\033[1m" + "=" * 50 + "\033[0m")

    all_ok = True

    all_ok &= check_packages()
    all_ok &= check_system_tools()
    env_ok, token, groq_key, eleven_key = check_env_vars()
    all_ok &= env_ok
    check_config(groq_key, eleven_key)
    all_ok &= check_bot_permissions(token)

    # 汇总
    print()
    print("\033[1m" + "-" * 50 + "\033[0m")
    if all_ok:
        print(f"  {OK} \033[92mAll checks passed — voice mode ready!\033[0m")
    else:
        print(f"  {FAIL} \033[91mSome checks failed — fix issues above.\033[0m")
    print()


if __name__ == "__main__":
    main()
