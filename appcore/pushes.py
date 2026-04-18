"""推送管理：就绪判定、状态计算、payload 组装、探活、日志写入、状态变更。"""
from __future__ import annotations

import json
import logging
from typing import Any

import requests

import config
from appcore import medias, tos_clients
from appcore.db import query, query_one, execute

log = logging.getLogger(__name__)


# ---------- 就绪判定 ----------

def compute_readiness(item: dict, product: dict) -> dict:
    """返回 4 项就绪布尔。调用方再据此判定 pushable。"""
    has_object = bool((item or {}).get("object_key"))
    has_cover = bool((item or {}).get("cover_object_key"))

    lang = (item or {}).get("lang") or "en"
    pid = (item or {}).get("product_id")
    has_copywriting = False
    if pid and lang:
        row = query_one(
            "SELECT 1 AS ok FROM media_copywritings "
            "WHERE product_id=%s AND lang=%s LIMIT 1",
            (pid, lang),
        )
        has_copywriting = bool(row)

    supported = medias.parse_ad_supported_langs((product or {}).get("ad_supported_langs"))
    lang_supported = lang in supported

    return {
        "has_object": has_object,
        "has_cover": has_cover,
        "has_copywriting": has_copywriting,
        "lang_supported": lang_supported,
    }


def is_ready(readiness: dict) -> bool:
    return all(readiness.values())
