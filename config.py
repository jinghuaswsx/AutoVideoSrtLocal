"""基础设施 + 路径 + 非供应商运行参数配置。

重要变更（2026-04-25 LLM 供应商配置数据库化）：
  - 所有模型 / API 供应商凭据、base_url、默认模型现在一律由
    `appcore.llm_provider_configs` DAO 读取，不再从 .env / 环境变量回落。
  - 本文件只保留：
      * 数据库连接（DB_*）
      * 服务端口与公网地址（LOCAL_SERVER_BASE_URL）
      * 文件存储（TOS_*）、点播（VOD_*）等对象存储基础设施
      * 本地路径（OUTPUT_DIR / UPLOAD_DIR / CAPCUT_TEMPLATE_DIR / JIANYING_PROJECT_DIR / VOICES_FILE）
      * 字幕、广告轮询行为参数（SUBTITLE_REMOVAL_POLL_*、AD_URL_*）
      * 对外共享的 API 公共端点常量（OPENROUTER_BASE_URL_DEFAULT 等非秘钥的默认
        base_url，仅作为 llm_provider_configs 行 base_url 为空时的 fallback 默认）
      * 非秘钥业务 flag（AV_LOCALIZE_FALLBACK）
      * FLASK / CSRF / 汇率常量

  - **严禁在这里读取任何供应商的 api_key / base_url / model_id**。
    新增供应商走 db/migrations/ 与 appcore/llm_provider_configs.py。
"""
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


# ---------------------------------------------------------------------------
# Flask / 服务端口 / 公网地址
# ---------------------------------------------------------------------------
LOCAL_SERVER_BASE_URL = _env("LOCAL_SERVER_BASE_URL", "http://127.0.0.1:5000")


# ---------------------------------------------------------------------------
# 火山 TOS：对象存储（非 LLM 供应商，继续走 env）
# ---------------------------------------------------------------------------
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


# ---------------------------------------------------------------------------
# 火山 VOD：视频点播 + 字幕擦除上传（对象存储级别，非 LLM 供应商）
# ---------------------------------------------------------------------------
VOD_SPACE_NAME = _env("VOD_SPACE_NAME")
VOD_REGION = _env("VOD_REGION", "cn-north-1")
VOD_ACCESS_KEY = _env("VOD_ACCESS_KEY")
VOD_SECRET_KEY = _env("VOD_SECRET_KEY")
VOD_PLAYBACK_DOMAIN = _env("VOD_PLAYBACK_DOMAIN")
VOD_ERASE_MAX_WAIT_SECONDS = int(_env("VOD_ERASE_MAX_WAIT_SECONDS", "3600"))
VOD_UPLOAD_MAX_WAIT_SECONDS = int(_env("VOD_UPLOAD_MAX_WAIT_SECONDS", "600"))


# ---------------------------------------------------------------------------
# 对外 API 端点常量（非秘钥，仅用作 llm_provider_configs.base_url 空时的默认值）
# ---------------------------------------------------------------------------
OPENROUTER_BASE_URL_DEFAULT = "https://openrouter.ai/api/v1"
DOUBAO_LLM_BASE_URL_DEFAULT = "https://ark.cn-beijing.volces.com/api/v3"
ELEVENLABS_BASE_URL_DEFAULT = "https://api.elevenlabs.io/v1"
APIMART_BASE_URL_DEFAULT = "https://api.apimart.ai"
SUBTITLE_REMOVAL_PROVIDER_URL_DEFAULT = "https://goodline.simplemokey.com/api/openAi"

# 火山 ASR（豆包）HTTP 调用端点，本身是固定的服务器地址
VOLC_ASR_SUBMIT_URL = "https://openspeech.bytedance.com/api/v3/auc/bigmodel/submit"
VOLC_ASR_QUERY_URL = "https://openspeech.bytedance.com/api/v3/auc/bigmodel/query"

USD_TO_CNY = 6.8


# ---------------------------------------------------------------------------
# 业务行为 flag / 非秘钥阈值
# ---------------------------------------------------------------------------
AV_LOCALIZE_FALLBACK = _env("AV_LOCALIZE_FALLBACK", "0") == "1"

AD_URL_TEMPLATE = _env(
    "AD_URL_TEMPLATE",
    "https://newjoyloo.com/{lang}/products/{product_code}",
)
AD_URL_PROBE_TIMEOUT = int(_env("AD_URL_PROBE_TIMEOUT", "5"))

SUBTITLE_REMOVAL_POLL_FAST_SECONDS = int(_env("SUBTITLE_REMOVAL_POLL_FAST_SECONDS", "8"))
SUBTITLE_REMOVAL_POLL_SLOW_SECONDS = int(_env("SUBTITLE_REMOVAL_POLL_SLOW_SECONDS", "15"))
SUBTITLE_REMOVAL_MAX_DURATION_SECONDS = int(_env("SUBTITLE_REMOVAL_MAX_DURATION_SECONDS", "600"))
# 字幕擦除后端选择：goodline（第三方 API）| vod（火山引擎点播）
SUBTITLE_REMOVAL_PROVIDER = _env("SUBTITLE_REMOVAL_PROVIDER", "goodline").strip().lower()


# ---------------------------------------------------------------------------
# 路径
# ---------------------------------------------------------------------------
OUTPUT_DIR = _path("OUTPUT_DIR", "output")
UPLOAD_DIR = _path("UPLOAD_DIR", "uploads")
VOICES_FILE = _path("VOICES_FILE", "voices/voices.json")
CAPCUT_TEMPLATE_DIR = _path("CAPCUT_TEMPLATE_DIR", "capcut_example")
JIANYING_PROJECT_DIR = _optional_path("JIANYING_PROJECT_DIR")


# ---------------------------------------------------------------------------
# 字幕布局（非秘钥）
# ---------------------------------------------------------------------------
SUBTITLE_MAX_CHARS_PER_LINE = int(_env("SUBTITLE_MAX_CHARS_PER_LINE", "42"))
SUBTITLE_MAX_LINES = int(_env("SUBTITLE_MAX_LINES", "2"))


# ---------------------------------------------------------------------------
# 推送（老 env 作为兼容兜底；运行时以 system_settings 为准）
# ---------------------------------------------------------------------------
PUSH_TARGET_URL = _env("PUSH_TARGET_URL", "")
PUSH_LOCALIZED_TEXTS_BASE_URL = _env("PUSH_LOCALIZED_TEXTS_BASE_URL", "https://os.wedev.vip")
PUSH_LOCALIZED_TEXTS_AUTHORIZATION = _env("PUSH_LOCALIZED_TEXTS_AUTHORIZATION", "")
PUSH_LOCALIZED_TEXTS_COOKIE = _env("PUSH_LOCALIZED_TEXTS_COOKIE", "")


# ---------------------------------------------------------------------------
# 基础设施必填项：只检查 TOS（对象存储）。其他凭据由 llm_provider_configs 运行
# 时按调用点校验，缺 key 时抛 ProviderConfigError 并定位到 /settings 页。
# ---------------------------------------------------------------------------
REQUIRED_CREDENTIALS = {
    "TOS_ACCESS_KEY": TOS_ACCESS_KEY,
    "TOS_SECRET_KEY": TOS_SECRET_KEY,
}


# ---------------------------------------------------------------------------
# MySQL
# ---------------------------------------------------------------------------
DB_HOST = _env("DB_HOST", "127.0.0.1")
DB_PORT = int(_env("DB_PORT", "3306"))
DB_NAME = _env("DB_NAME", "auto_video")
DB_USER = _env("DB_USER", "root")
DB_PASSWORD = _env("DB_PASSWORD")


def validate_runtime_config(required_keys=None):
    """基础设施启动校验：TOS 秘钥缺失直接阻止启动。

    注意：模型 / API 供应商的凭据不在这里校验，
    改由 appcore.llm_provider_configs 在调用时按 provider_code 抛错。
    """
    keys = required_keys or list(REQUIRED_CREDENTIALS.keys())
    missing = [key for key in keys if not globals().get(key)]
    if missing:
        joined = ", ".join(missing)
        raise RuntimeError(
            f"Missing required environment variables: {joined}. "
            "Copy .env.example to .env and fill in your object-storage credentials."
        )
