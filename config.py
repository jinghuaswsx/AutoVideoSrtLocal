import os
from pathlib import Path

from dotenv import load_dotenv

if os.getenv("AUTOVIDEOSRT_DISABLE_DOTENV") != "1":
    load_dotenv()


BASE_DIR = Path(__file__).resolve().parent


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def _path(name: str, default: str) -> str:
    value = _env(name, default)
    if os.path.isabs(value):
        return value
    return str((BASE_DIR / value).resolve())


def _optional_path(name: str) -> str:
    value = _env(name)
    if not value:
        return ""
    if os.path.isabs(value):
        return value
    return str((BASE_DIR / value).resolve())


# 火山引擎豆包 ASR
VOLC_API_KEY = _env("VOLC_API_KEY")
VOLC_RESOURCE_ID = _env("VOLC_RESOURCE_ID", "volc.seedasr.auc")
VOLC_ASR_SUBMIT_URL = "https://openspeech.bytedance.com/api/v3/auc/bigmodel/submit"
VOLC_ASR_QUERY_URL = "https://openspeech.bytedance.com/api/v3/auc/bigmodel/query"

# Flask 服务的外部可访问地址
LOCAL_SERVER_BASE_URL = _env("LOCAL_SERVER_BASE_URL", "http://127.0.0.1:5000")

# 火山引擎 TOS
TOS_ACCESS_KEY = _env("TOS_ACCESS_KEY")
TOS_SECRET_KEY = _env("TOS_SECRET_KEY")
TOS_REGION = _env("TOS_REGION", "cn-shanghai")
TOS_BUCKET = _env("TOS_BUCKET", "auto-video-srt")
TOS_ENDPOINT = _env("TOS_ENDPOINT", "tos-cn-shanghai.volces.com")
TOS_PUBLIC_ENDPOINT = _env("TOS_PUBLIC_ENDPOINT", TOS_ENDPOINT or "tos-cn-shanghai.volces.com")
TOS_PRIVATE_ENDPOINT = _env("TOS_PRIVATE_ENDPOINT", "tos-cn-shanghai.ivolces.com")
TOS_USE_PRIVATE_ENDPOINT = _env("TOS_USE_PRIVATE_ENDPOINT", "false").lower() in {"1", "true", "yes", "on"}
TOS_PREFIX = _env("TOS_PREFIX", "asr-audio/")
TOS_BROWSER_UPLOAD_PREFIX = _env("TOS_BROWSER_UPLOAD_PREFIX", "uploads/")
TOS_FINAL_ARTIFACT_PREFIX = _env("TOS_FINAL_ARTIFACT_PREFIX", "artifacts/")
TOS_SIGNED_URL_EXPIRES = int(_env("TOS_SIGNED_URL_EXPIRES", "3600"))
TOS_PRIVATE_PROBE_TTL = int(_env("TOS_PRIVATE_PROBE_TTL", "60"))
TOS_UPLOAD_CLEANUP_MAX_AGE_SECONDS = int(_env("TOS_UPLOAD_CLEANUP_MAX_AGE_SECONDS", str(48 * 3600)))

# OpenRouter Claude
OPENROUTER_API_KEY = _env("OPENROUTER_API_KEY")
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
CLAUDE_MODEL = _env("CLAUDE_MODEL", "anthropic/claude-sonnet-4-5")

# 豆包翻译 (火山引擎 ARK)
DOUBAO_LLM_API_KEY = _env("DOUBAO_LLM_API_KEY") or _env("VOLC_API_KEY")
if DOUBAO_LLM_API_KEY and not os.environ.get("DOUBAO_LLM_API_KEY"):
    os.environ["DOUBAO_LLM_API_KEY"] = DOUBAO_LLM_API_KEY
DOUBAO_LLM_BASE_URL = "https://ark.cn-beijing.volces.com/api/v3"
DOUBAO_LLM_MODEL = _env("DOUBAO_LLM_MODEL", "doubao-seed-2-0-pro-260215")

# Seedance 视频生成（默认复用火山引擎 ARK 同一个 key）
SEEDANCE_API_KEY = _env("SEEDANCE_API_KEY") or DOUBAO_LLM_API_KEY
if SEEDANCE_API_KEY and not os.environ.get("SEEDANCE_API_KEY"):
    os.environ["SEEDANCE_API_KEY"] = SEEDANCE_API_KEY

# ElevenLabs
ELEVENLABS_API_KEY = _env("ELEVENLABS_API_KEY")
ELEVENLABS_BASE_URL = "https://api.elevenlabs.io/v1"

# 路径
OUTPUT_DIR = _path("OUTPUT_DIR", "output")
UPLOAD_DIR = _path("UPLOAD_DIR", "uploads")
VOICES_FILE = _path("VOICES_FILE", "voices/voices.json")
CAPCUT_TEMPLATE_DIR = _path("CAPCUT_TEMPLATE_DIR", "capcut_example")
JIANYING_PROJECT_DIR = _optional_path("JIANYING_PROJECT_DIR")

# 字幕配置
SUBTITLE_MAX_CHARS_PER_LINE = int(_env("SUBTITLE_MAX_CHARS_PER_LINE", "42"))
SUBTITLE_MAX_LINES = int(_env("SUBTITLE_MAX_LINES", "2"))

REQUIRED_CREDENTIALS = {
    "VOLC_API_KEY": VOLC_API_KEY,
    "TOS_ACCESS_KEY": TOS_ACCESS_KEY,
    "TOS_SECRET_KEY": TOS_SECRET_KEY,
    "OPENROUTER_API_KEY": OPENROUTER_API_KEY,
    "ELEVENLABS_API_KEY": ELEVENLABS_API_KEY,
}


# MySQL
DB_HOST = _env("DB_HOST", "127.0.0.1")
DB_PORT = int(_env("DB_PORT", "3306"))
DB_NAME = _env("DB_NAME", "auto_video")
DB_USER = _env("DB_USER", "root")
DB_PASSWORD = _env("DB_PASSWORD")


def validate_runtime_config(required_keys=None):
    keys = required_keys or list(REQUIRED_CREDENTIALS.keys())
    missing = [key for key in keys if not globals().get(key)]
    if missing:
        joined = ", ".join(missing)
        raise RuntimeError(
            f"Missing required environment variables: {joined}. "
            "Copy .env.example to .env and fill in your own credentials."
        )
