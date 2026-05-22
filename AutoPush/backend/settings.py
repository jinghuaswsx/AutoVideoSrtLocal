"""AutoPush 运行时配置。从 .env 或环境变量读取。"""
from __future__ import annotations

from functools import lru_cache
from os import getenv
from pathlib import Path
import sys

from dotenv import load_dotenv


load_dotenv()

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from server_config import SERVER_BASE_URL


class Settings:
    def __init__(self) -> None:
        self.autovideo_base_url = getenv(
            "AUTOVIDEO_BASE_URL",
            SERVER_BASE_URL,
        ).rstrip("/")
        self.autovideo_api_key = getenv(
            "AUTOVIDEO_API_KEY",
            "",
        )
        self.push_medias_target = getenv(
            "PUSH_MEDIAS_TARGET",
            "http://172.17.254.77:22400/dify/shopify/medias",
        )
        self.push_localized_texts_base_url = getenv(
            "PUSH_LOCALIZED_TEXTS_BASE_URL",
            "https://os.wedev.vip",
        ).rstrip("/")
        self.push_localized_texts_authorization = getenv(
            "PUSH_LOCALIZED_TEXTS_AUTHORIZATION",
            "",
        ).strip()
        self.push_localized_texts_use_chrome_auth = getenv(
            "PUSH_LOCALIZED_TEXTS_USE_CHROME_AUTH",
            "1",
        ).strip().lower() not in {"0", "false", "no", "off"}
        self.port = int(getenv("AUTOPUSH_PORT", "8787"))


@lru_cache
def get_settings() -> Settings:
    return Settings()
