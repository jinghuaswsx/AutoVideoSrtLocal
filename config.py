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

# 火山引擎 TOS：仅用于第三方 API 公网 URL 临时交换、历史文件回填迁移。
TOS_ACCESS_KEY = _env("TOS_ACCESS_KEY")
TOS_SECRET_KEY = _env("TOS_SECRET_KEY")
TOS_REGION = _env("TOS_REGION", "cn-shanghai")
TOS_BUCKET = _env("TOS_BUCKET", "auto-video-srt")
TOS_MEDIA_BUCKET = _env("TOS_MEDIA_BUCKET", "auto-video-srt-product-video-manage")
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
OPENAPI_MEDIA_API_KEY = _env("OPENAPI_MEDIA_API_KEY")

# APIMART 图片生成
APIMART_IMAGE_API_KEY = _env("APIMART_IMAGE_API_KEY")
# 推送管理
PUSH_TARGET_URL = _env("PUSH_TARGET_URL", "")
# 小语种文案推送（运行时可在 /settings?tab=push 覆盖；env 为兜底默认）
PUSH_LOCALIZED_TEXTS_BASE_URL = _env("PUSH_LOCALIZED_TEXTS_BASE_URL", "https://os.wedev.vip")
PUSH_LOCALIZED_TEXTS_AUTHORIZATION = _env("PUSH_LOCALIZED_TEXTS_AUTHORIZATION", "")
PUSH_LOCALIZED_TEXTS_COOKIE = _env("PUSH_LOCALIZED_TEXTS_COOKIE", "")
AD_URL_TEMPLATE = _env("AD_URL_TEMPLATE",
                       "https://newjoyloo.com/{lang}/products/{product_code}")
AD_URL_PROBE_TIMEOUT = int(_env("AD_URL_PROBE_TIMEOUT", "5"))
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
CLAUDE_MODEL = _env("CLAUDE_MODEL", "anthropic/claude-sonnet-4-5")
USD_TO_CNY = 6.8

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
AV_LOCALIZE_FALLBACK = _env("AV_LOCALIZE_FALLBACK", "0") == "1"

# Google Gemini
# 后端选择：aistudio（AI Studio，默认）| cloud（Vertex AI Express Mode）
GEMINI_BACKEND = _env("GEMINI_BACKEND", "aistudio").lower()


def _parse_google_api_key_file() -> dict[str, str]:
    """解析 google_api_key 文件。支持两种格式：
    - 带标签：`AISTUDIO: AIza...` / `CLOUD: AQ.Ab8...`
    - 单行：整个文件当作 AISTUDIO key（向后兼容）
    """
    key_file = BASE_DIR / "google_api_key"
    if not key_file.exists():
        return {}
    try:
        text = key_file.read_text(encoding="utf-8")
    except Exception:
        return {}
    result: dict[str, str] = {}
    found_label = False
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" in line:
            label, _, value = line.partition(":")
            label = label.strip().upper()
            value = value.strip()
            if label in {"AISTUDIO", "CLOUD"} and value:
                result[label] = value
                found_label = True
    if not found_label:
        one_liner = text.strip()
        if one_liner:
            result["AISTUDIO"] = one_liner
    return result


def _resolve_gemini_keys() -> tuple[str, str]:
    """返回 (aistudio_key, cloud_key)。优先环境变量，其次 google_api_key 文件。"""
    file_keys = _parse_google_api_key_file()
    aistudio = _env("GEMINI_API_KEY") or _env("GOOGLE_API_KEY") or file_keys.get("AISTUDIO", "")
    cloud = _env("GEMINI_CLOUD_API_KEY") or file_keys.get("CLOUD", "")
    return aistudio, cloud


GEMINI_AISTUDIO_API_KEY, GEMINI_CLOUD_API_KEY = _resolve_gemini_keys()
GEMINI_API_KEY = GEMINI_CLOUD_API_KEY if GEMINI_BACKEND == "cloud" else GEMINI_AISTUDIO_API_KEY
GEMINI_CLOUD_PROJECT = _env("GEMINI_CLOUD_PROJECT")
GEMINI_CLOUD_LOCATION = _env("GEMINI_CLOUD_LOCATION", "global")
GEMINI_MODEL = _env("GEMINI_MODEL", "gemini-3.1-flash-lite-preview")

# 路径
OUTPUT_DIR = _path("OUTPUT_DIR", "output")
UPLOAD_DIR = _path("UPLOAD_DIR", "uploads")
VOICES_FILE = _path("VOICES_FILE", "voices/voices.json")
CAPCUT_TEMPLATE_DIR = _path("CAPCUT_TEMPLATE_DIR", "capcut_example")
JIANYING_PROJECT_DIR = _optional_path("JIANYING_PROJECT_DIR")

# 字幕配置
SUBTITLE_MAX_CHARS_PER_LINE = int(_env("SUBTITLE_MAX_CHARS_PER_LINE", "42"))
SUBTITLE_MAX_LINES = int(_env("SUBTITLE_MAX_LINES", "2"))

SUBTITLE_REMOVAL_PROVIDER_URL = _env("SUBTITLE_REMOVAL_PROVIDER_URL", "https://goodline.simplemokey.com/api/openAi")
SUBTITLE_REMOVAL_PROVIDER_TOKEN = _env("SUBTITLE_REMOVAL_PROVIDER_TOKEN")
SUBTITLE_REMOVAL_NOTIFY_URL = _env("SUBTITLE_REMOVAL_NOTIFY_URL")
SUBTITLE_REMOVAL_POLL_FAST_SECONDS = int(_env("SUBTITLE_REMOVAL_POLL_FAST_SECONDS", "8"))
SUBTITLE_REMOVAL_POLL_SLOW_SECONDS = int(_env("SUBTITLE_REMOVAL_POLL_SLOW_SECONDS", "15"))
SUBTITLE_REMOVAL_MAX_DURATION_SECONDS = int(_env("SUBTITLE_REMOVAL_MAX_DURATION_SECONDS", "600"))

# 字幕擦除 provider 选择：goodline（原第三方）| vod（火山引擎点播）
SUBTITLE_REMOVAL_PROVIDER = _env("SUBTITLE_REMOVAL_PROVIDER", "goodline").strip().lower()

# 火山引擎视频点播（VOD）- 字幕擦除依赖
VOD_SPACE_NAME = _env("VOD_SPACE_NAME")
VOD_REGION = _env("VOD_REGION", "cn-north-1")
VOD_ACCESS_KEY = _env("VOD_ACCESS_KEY")
VOD_SECRET_KEY = _env("VOD_SECRET_KEY")
VOD_PLAYBACK_DOMAIN = _env("VOD_PLAYBACK_DOMAIN")
VOD_ERASE_MAX_WAIT_SECONDS = int(_env("VOD_ERASE_MAX_WAIT_SECONDS", "3600"))
VOD_UPLOAD_MAX_WAIT_SECONDS = int(_env("VOD_UPLOAD_MAX_WAIT_SECONDS", "600"))

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
