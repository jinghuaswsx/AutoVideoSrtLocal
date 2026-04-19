"""火山引擎视频点播（VOD）OpenAPI 客户端。

基于 volcengine-python-sdk 的 UniversalApi，直接通过 Action + Version 调任意 VOD 接口。
AK/SK 优先使用 `VOD_ACCESS_KEY` / `VOD_SECRET_KEY`，未配置时回落到 `TOS_ACCESS_KEY` / `TOS_SECRET_KEY`
（主账号场景下两套凭证通常一致）。
"""

from __future__ import annotations

import json
import re
import threading
from typing import Any

import volcenginesdkcore

import config


def _extract_error_from_exc(exc: Exception) -> tuple[str, str] | None:
    """从 SDK 抛出的 ApiException 文本里抽出 ResponseMetadata.Error。

    SDK 在 HTTP 非 2xx 时抛 urllib3/ApiException，body 会是 VOD 标准 JSON。
    """
    text = str(exc)
    # SDK 把 HTTP response body 放在 "HTTP response body: " 之后；兜底用 ResponseMetadata 关键词定位
    marker = "HTTP response body:"
    idx = text.find(marker)
    body_text = text[idx + len(marker):].strip() if idx >= 0 else text
    # 去掉前后非 JSON 干扰
    start = body_text.find("{")
    end = body_text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        payload = json.loads(body_text[start:end + 1])
    except Exception:
        return None
    metadata = payload.get("ResponseMetadata") or {}
    error = metadata.get("Error") or {}
    code = error.get("CodeN") or error.get("Code") or ""
    msg = error.get("Message") or ""
    if code or msg:
        return str(code), str(msg)
    return None


class VodClientError(RuntimeError):
    pass


_lock = threading.Lock()
_configured = False


def _resolve_ak_sk() -> tuple[str, str]:
    ak = (getattr(config, "VOD_ACCESS_KEY", "") or config.TOS_ACCESS_KEY or "").strip()
    sk = (getattr(config, "VOD_SECRET_KEY", "") or config.TOS_SECRET_KEY or "").strip()
    if not ak or not sk:
        raise VodClientError("VOD AK/SK is not configured (set VOD_ACCESS_KEY/VOD_SECRET_KEY or TOS_ACCESS_KEY/TOS_SECRET_KEY)")
    return ak, sk


def _ensure_configured() -> None:
    global _configured
    if _configured:
        return
    with _lock:
        if _configured:
            return
        ak, sk = _resolve_ak_sk()
        region = (getattr(config, "VOD_REGION", "") or "cn-north-1").strip()
        configuration = volcenginesdkcore.Configuration()
        configuration.ak = ak
        configuration.sk = sk
        configuration.region = region
        volcenginesdkcore.Configuration.set_default(configuration)
        _configured = True


def call(
    *,
    action: str,
    version: str = "2025-01-01",
    body: Any = None,
    method: str = "POST",
    content_type: str = "application/json",
) -> dict:
    """调一次 VOD OpenAPI，返回 ResponseMetadata 之外的 Result 字段。

    action / version 对应火山 OpenAPI 的 `Action` / `Version` 查询参数。
    GET 请求时 body 作为 query 参数字典。
    """
    _ensure_configured()
    api = volcenginesdkcore.UniversalApi()
    info = volcenginesdkcore.UniversalInfo(
        method=method,
        service="vod",
        version=version,
        action=action,
        content_type=content_type,
    )
    payload: Any = body if isinstance(body, dict) else {}
    try:
        response = api.do_call(info, payload)
    except Exception as exc:  # SDK 抛出的异常类型多，这里兜一层
        err = _extract_error_from_exc(exc)
        if err:
            code, msg = err
            raise VodClientError(f"VOD {action} error: {code} - {msg}") from exc
        raise VodClientError(f"VOD {action} request failed: {exc}") from exc

    if isinstance(response, (bytes, bytearray)):
        try:
            response = json.loads(response.decode("utf-8"))
        except Exception as exc:
            raise VodClientError(f"VOD {action} response decode failed: {exc}") from exc
    if isinstance(response, str):
        try:
            response = json.loads(response)
        except Exception as exc:
            raise VodClientError(f"VOD {action} response decode failed: {exc}") from exc
    if not isinstance(response, dict):
        raise VodClientError(f"VOD {action} response is not a dict: {type(response).__name__}")

    # SDK 通常已经剥掉了 ResponseMetadata 层，直接返回 Result 内容；
    # 兜一层兼容，万一某个 Action 仍返回完整包装就从里面取。
    if "ResponseMetadata" in response:
        metadata = response.get("ResponseMetadata") or {}
        error = metadata.get("Error") if isinstance(metadata, dict) else None
        if error:
            code = error.get("CodeN") or error.get("Code") or "Unknown"
            msg = error.get("Message") or "unknown error"
            raise VodClientError(f"VOD {action} error: {code} - {msg}")
        return response.get("Result") or {}
    return response
