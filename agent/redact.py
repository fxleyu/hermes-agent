"""基于正则表达式的日志和工具输出敏感信息脱敏。

对 API 密钥、令牌和凭证进行模式匹配脱敏，
在它们到达日志文件、详细输出或网关日志之前完成处理。

短令牌（< 18 字符）完全遮盖。较长的令牌保留
前 6 个和后 4 个字符，便于调试定位。
"""

import logging
import os
import re

logger = logging.getLogger(__name__)

# 在导入时快照环境变量，防止运行时环境变量变更
# （如 LLM 生成的 `export HERMES_REDACT_SECRETS=false`）在会话中途禁用脱敏。
_REDACT_ENABLED = os.getenv("HERMES_REDACT_SECRETS", "").lower() not in ("0", "false", "no", "off")

# 已知的 API 密钥前缀——匹配前缀 + 连续的令牌字符
_PREFIX_PATTERNS = [
    r"sk-[A-Za-z0-9_-]{10,}",           # OpenAI / OpenRouter / Anthropic (sk-ant-*)
    r"ghp_[A-Za-z0-9]{10,}",            # GitHub PAT（经典版）
    r"github_pat_[A-Za-z0-9_]{10,}",    # GitHub PAT（细粒度版）
    r"gho_[A-Za-z0-9]{10,}",            # GitHub OAuth 访问令牌
    r"ghu_[A-Za-z0-9]{10,}",            # GitHub 用户到服务器令牌
    r"ghs_[A-Za-z0-9]{10,}",            # GitHub 服务器到服务器令牌
    r"ghr_[A-Za-z0-9]{10,}",            # GitHub 刷新令牌
    r"xox[baprs]-[A-Za-z0-9-]{10,}",    # Slack 令牌
    r"AIza[A-Za-z0-9_-]{30,}",          # Google API 密钥
    r"pplx-[A-Za-z0-9]{10,}",           # Perplexity
    r"fal_[A-Za-z0-9_-]{10,}",          # Fal.ai
    r"fc-[A-Za-z0-9]{10,}",             # Firecrawl
    r"bb_live_[A-Za-z0-9_-]{10,}",      # BrowserBase
    r"gAAAA[A-Za-z0-9_=-]{20,}",        # Codex 加密令牌
    r"AKIA[A-Z0-9]{16}",                # AWS Access Key ID
    r"sk_live_[A-Za-z0-9]{10,}",        # Stripe 密钥（生产环境）
    r"sk_test_[A-Za-z0-9]{10,}",        # Stripe 密钥（测试环境）
    r"rk_live_[A-Za-z0-9]{10,}",        # Stripe 受限密钥
    r"SG\.[A-Za-z0-9_-]{10,}",          # SendGrid API 密钥
    r"hf_[A-Za-z0-9]{10,}",             # HuggingFace 令牌
    r"r8_[A-Za-z0-9]{10,}",             # Replicate API 令牌
    r"npm_[A-Za-z0-9]{10,}",            # npm 访问令牌
    r"pypi-[A-Za-z0-9_-]{10,}",         # PyPI API 令牌
    r"dop_v1_[A-Za-z0-9]{10,}",         # DigitalOcean PAT
    r"doo_v1_[A-Za-z0-9]{10,}",         # DigitalOcean OAuth
    r"am_[A-Za-z0-9_-]{10,}",           # AgentMail API 密钥
    r"sk_[A-Za-z0-9_]{10,}",            # ElevenLabs TTS 密钥（sk_ 下划线，非 sk- 连字符）
    r"tvly-[A-Za-z0-9]{10,}",           # Tavily 搜索 API 密钥
    r"exa_[A-Za-z0-9]{10,}",            # Exa 搜索 API 密钥
    r"gsk_[A-Za-z0-9]{10,}",            # Groq Cloud API 密钥
    r"syt_[A-Za-z0-9]{10,}",            # Matrix 访问令牌
    r"retaindb_[A-Za-z0-9]{10,}",       # RetainDB API 密钥
    r"hsk-[A-Za-z0-9]{10,}",            # Hindsight API 密钥
    r"mem0_[A-Za-z0-9]{10,}",           # Mem0 Platform API 密钥
    r"brv_[A-Za-z0-9]{10,}",            # ByteRover API 密钥
]

# 环境变量赋值模式：KEY=value，其中 KEY 包含类似密钥的名称
_SECRET_ENV_NAMES = r"(?:API_?KEY|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIAL|AUTH)"
_ENV_ASSIGN_RE = re.compile(
    rf"([A-Z0-9_]{{0,50}}{_SECRET_ENV_NAMES}[A-Z0-9_]{{0,50}})\s*=\s*(['\"]?)(\S+)\2",
)

# JSON 字段模式："apiKey": "value"、"token": "value" 等
_JSON_KEY_NAMES = r"(?:api_?[Kk]ey|token|secret|password|access_token|refresh_token|auth_token|bearer|secret_value|raw_secret|secret_input|key_material)"
_JSON_FIELD_RE = re.compile(
    rf'("{_JSON_KEY_NAMES}")\s*:\s*"([^"]+)"',
    re.IGNORECASE,
)

# 授权头
_AUTH_HEADER_RE = re.compile(
    r"(Authorization:\s*Bearer\s+)(\S+)",
    re.IGNORECASE,
)

# Telegram 机器人令牌：bot<数字>:<令牌> 或 <数字>:<令牌>，
# 其中令牌部分限制为 [-A-Za-z0-9_] 且长度 >= 30
_TELEGRAM_RE = re.compile(
    r"(bot)?(\d{8,}):([-A-Za-z0-9_]{30,})",
)

# 私钥块：-----BEGIN RSA PRIVATE KEY----- ... -----END RSA PRIVATE KEY-----
_PRIVATE_KEY_RE = re.compile(
    r"-----BEGIN[A-Z ]*PRIVATE KEY-----[\s\S]*?-----END[A-Z ]*PRIVATE KEY-----"
)

# 数据库连接字符串：protocol://user:PASSWORD@host
# 捕获 postgres、mysql、mongodb、redis、amqp URL 并脱敏密码
_DB_CONNSTR_RE = re.compile(
    r"((?:postgres(?:ql)?|mysql|mongodb(?:\+srv)?|redis|amqp)://[^:]+:)([^@]+)(@)",
    re.IGNORECASE,
)

# JWT 令牌：header.payload[.signature]——始终以 "eyJ" 开头（"{" 的 base64 编码）
# 匹配 1 段（仅 header）、2 段（header.payload）和完整 3 段的 JWT。
_JWT_RE = re.compile(
    r"eyJ[A-Za-z0-9_-]{10,}"           # Header（始终以 eyJ 开头）
    r"(?:\.[A-Za-z0-9_=-]{4,}){0,2}"   # 可选的 payload 和/或 signature
)

# Discord 用户/角色提及：<@123456789012345678> 或 <@!123456789012345678>
# Snowflake ID 是 17-20 位整数，对应特定的 Discord 账户。
_DISCORD_MENTION_RE = re.compile(r"<@!?(\d{17,20})>")

# E.164 电话号码：+<国家代码><号码>，7-15 位数字
# 负向前瞻防止匹配十六进制字符串或标识符
_SIGNAL_PHONE_RE = re.compile(r"(\+[1-9]\d{6,14})(?![A-Za-z0-9])")

# 将已知前缀模式编译为一个交替匹配正则表达式
_PREFIX_RE = re.compile(
    r"(?<![A-Za-z0-9_-])(" + "|".join(_PREFIX_PATTERNS) + r")(?![A-Za-z0-9_-])"
)


def _mask_token(token: str) -> str:
    """遮盖令牌，对较长令牌保留前缀和后缀便于调试。"""
    if len(token) < 18:
        return "***"
    return f"{token[:6]}...{token[-4:]}"


def redact_sensitive_text(text: str) -> str:
    """对一段文本应用所有脱敏模式。

    可安全地对任意字符串调用——不匹配的文本原样通过。
    当 config.yaml 中 security.redact_secrets 为 false 时禁用。
    """
    if text is None:
        return None
    if not isinstance(text, str):
        text = str(text)
    if not text:
        return text
    if not _REDACT_ENABLED:
        return text

    # 已知前缀匹配（sk-、ghp_ 等）
    text = _PREFIX_RE.sub(lambda m: _mask_token(m.group(1)), text)

    # 环境变量赋值脱敏：OPENAI_API_KEY=sk-abc...
    def _redact_env(m):
        name, quote, value = m.group(1), m.group(2), m.group(3)
        return f"{name}={quote}{_mask_token(value)}{quote}"
    text = _ENV_ASSIGN_RE.sub(_redact_env, text)

    # JSON 字段脱敏："apiKey": "value"
    def _redact_json(m):
        key, value = m.group(1), m.group(2)
        return f'{key}: "{_mask_token(value)}"'
    text = _JSON_FIELD_RE.sub(_redact_json, text)

    # 授权头脱敏
    text = _AUTH_HEADER_RE.sub(
        lambda m: m.group(1) + _mask_token(m.group(2)),
        text,
    )

    # Telegram 机器人令牌脱敏
    def _redact_telegram(m):
        prefix = m.group(1) or ""
        digits = m.group(2)
        return f"{prefix}{digits}:***"
    text = _TELEGRAM_RE.sub(_redact_telegram, text)

    # 私钥块脱敏
    text = _PRIVATE_KEY_RE.sub("[REDACTED PRIVATE KEY]", text)

    # 数据库连接字符串密码脱敏
    text = _DB_CONNSTR_RE.sub(lambda m: f"{m.group(1)}***{m.group(3)}", text)

    # JWT 令牌脱敏（eyJ...——base64 编码的 JSON 头）
    text = _JWT_RE.sub(lambda m: _mask_token(m.group(0)), text)

    # Discord 用户/角色提及脱敏（<@snowflake_id>）
    text = _DISCORD_MENTION_RE.sub(lambda m: f"<@{'!' if '!' in m.group(0) else ''}***>", text)

    # E.164 电话号码脱敏（Signal、WhatsApp）
    def _redact_phone(m):
        phone = m.group(1)
        if len(phone) <= 8:
            return phone[:2] + "****" + phone[-2:]
        return phone[:4] + "****" + phone[-4:]
    text = _SIGNAL_PHONE_RE.sub(_redact_phone, text)

    return text


class RedactingFormatter(logging.Formatter):
    """对所有日志消息进行敏感信息脱敏的日志格式化器。"""

    def __init__(self, fmt=None, datefmt=None, style='%', **kwargs):
        super().__init__(fmt, datefmt, style, **kwargs)

    def format(self, record: logging.LogRecord) -> str:
        original = super().format(record)
        return redact_sensitive_text(original)
