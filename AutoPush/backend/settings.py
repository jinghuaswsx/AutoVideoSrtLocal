"""AutoPush 运行时配置。从 .env 或环境变量读取。"""
from __future__ import annotations

from functools import lru_cache
from os import getenv

from dotenv import load_dotenv


load_dotenv()


class Settings:
    def __init__(self) -> None:
        self.autovideo_base_url = getenv(
            "AUTOVIDEO_BASE_URL",
            "http://14.103.220.208:8888",
        ).rstrip("/")
        self.autovideo_api_key = getenv(
            "AUTOVIDEO_API_KEY",
            "autovideosrt-materials-openapi",
        )
        self.push_medias_target = getenv(
            "PUSH_MEDIAS_TARGET",
            "http://172.17.254.77:22400/dify/shopify/medias",
        )
        self.push_localized_texts_base_url = getenv(
            "PUSH_LOCALIZED_TEXTS_BASE_URL",
            "https://os.wedev.vip",
        ).rstrip("/")
        self.port = int(getenv("AUTOPUSH_PORT", "8787"))


@lru_cache
def get_settings() -> Settings:
    return Settings()
