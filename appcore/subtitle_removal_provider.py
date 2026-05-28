from __future__ import annotations

import json

import requests

import config
from appcore.llm_provider_configs import (
    ProviderConfigError,
    require_provider_config,
)
from config import SUBTITLE_REMOVAL_PROVIDER_URL_DEFAULT


class SubtitleRemovalProviderError(RuntimeError):
    pass


DEFAULT_CREDENTIAL_CODE = "subtitle_removal"
NIUMA_CREDENTIAL_CODE = "niuma_main"


def _normalize_credential_code(credential_code: str | None) -> str:
    code = (credential_code or DEFAULT_CREDENTIAL_CODE).strip().lower()
    return code or DEFAULT_CREDENTIAL_CODE


def _provider_cfg(credential_code: str = DEFAULT_CREDENTIAL_CODE):
    try:
        return require_provider_config(credential_code)
    except ProviderConfigError as exc:
        raise SubtitleRemovalProviderError(str(exc)) from exc


def _provider_url(credential_code: str = DEFAULT_CREDENTIAL_CODE) -> str:
    if _normalize_credential_code(credential_code) == NIUMA_CREDENTIAL_CODE:
        return (
            getattr(config, "NIUMA_ERASE_BASE_URL", "") or SUBTITLE_REMOVAL_PROVIDER_URL_DEFAULT
        ).strip()

    cfg = _provider_cfg(credential_code)
    try:
        return cfg.require_base_url(default=SUBTITLE_REMOVAL_PROVIDER_URL_DEFAULT)
    except ProviderConfigError as exc:
        raise SubtitleRemovalProviderError(str(exc)) from exc


def _headers(credential_code: str = DEFAULT_CREDENTIAL_CODE) -> dict[str, str]:
    if _normalize_credential_code(credential_code) == NIUMA_CREDENTIAL_CODE:
        token = (getattr(config, "NIUMA_ERASE_API_KEY", "") or "").strip()
        if not token:
            raise SubtitleRemovalProviderError(
                "缺少基础设施配置 niuma_main.api_key，请在 /settings?tab=infrastructure 填写。"
            )
        return {"authorization": token}

    cfg = _provider_cfg(credential_code)
    try:
        token = cfg.require_api_key()
    except ProviderConfigError as exc:
        raise SubtitleRemovalProviderError(str(exc)) from exc
    return {"authorization": token}


def _notify_url(credential_code: str = DEFAULT_CREDENTIAL_CODE) -> str:
    if _normalize_credential_code(credential_code) == NIUMA_CREDENTIAL_CODE:
        return ""

    cfg = _provider_cfg(credential_code)
    extra = cfg.extra_config or {}
    return (extra.get("notify_url") or extra.get("notifyUrl") or "").strip()


def _post(payload: dict, *, credential_code: str = DEFAULT_CREDENTIAL_CODE) -> dict:
    try:
        response = requests.post(
            _provider_url(credential_code),
            headers=_headers(credential_code),
            json=payload,
            timeout=30,
        )
        response.raise_for_status()
        data = response.json()
    except requests.RequestException as exc:
        raise SubtitleRemovalProviderError(str(exc) or "subtitle removal provider request failed") from exc
    except ValueError as exc:
        raise SubtitleRemovalProviderError(str(exc) or "subtitle removal provider returned invalid JSON") from exc
    if not isinstance(data, dict):
        raise SubtitleRemovalProviderError("subtitle removal provider returned invalid payload")
    if data.get("code") != 0:
        raise SubtitleRemovalProviderError(data.get("msg") or "subtitle removal provider request failed")
    return data


def _position_payload(remove_region: dict | None) -> str:
    if not remove_region:
        return ""
    try:
        payload = {
            "l": int(remove_region.get("l")),
            "t": int(remove_region.get("t")),
            "w": int(remove_region.get("w")),
            "h": int(remove_region.get("h")),
        }
    except (TypeError, ValueError) as exc:
        raise ValueError("remove_region must include integer l, t, w and h") from exc
    if payload["w"] <= 0 or payload["h"] <= 0:
        raise ValueError("remove_region width and height must be positive")
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def submit_task(
    *,
    file_size_mb: float,
    duration_seconds: float,
    resolution: str,
    video_name: str,
    source_url: str,
    cover_url: str = "",
    erase_text_type: str = "subtitle",
    credential_code: str = DEFAULT_CREDENTIAL_CODE,
    remove_region: dict | None = None,
) -> str:
    if erase_text_type not in {"subtitle", "text"}:
        raise ValueError(
            f"erase_text_type must be 'subtitle' or 'text', got {erase_text_type!r}"
        )
    payload = {
        "biz": "aiRemoveSubtitleSubmitTask",
        "fileSize": round(file_size_mb, 2),
        "duration": round(duration_seconds, 2),
        "resolution": resolution,
        "videoName": video_name,
        "coverUrl": cover_url,
        "url": source_url,
        "notifyUrl": _notify_url(credential_code),
    }
    if erase_text_type == "text":
        payload["operation"] = {
            "type": "Task",
            "task": {
                "type": "Erase",
                "erase": {
                    "mode": "Auto",
                    "auto": {"type": "Text"},
                },
            },
        }
    if _normalize_credential_code(credential_code) == NIUMA_CREDENTIAL_CODE and remove_region:
        payload["position"] = _position_payload(remove_region)
    data = _post(payload, credential_code=credential_code)
    payload_result = data.get("data")
    if isinstance(payload_result, dict) and payload_result.get("taskId"):
        return str(payload_result["taskId"])
    if isinstance(payload_result, list) and payload_result and isinstance(payload_result[0], dict) and payload_result[0].get("taskId"):
        return str(payload_result[0]["taskId"])
    if isinstance(payload_result, str) and payload_result.strip():
        return payload_result.strip()
    raise SubtitleRemovalProviderError("Provider submit response missing taskId")


def query_progress(task_id: str, *, credential_code: str = DEFAULT_CREDENTIAL_CODE) -> dict:
    data = _post(
        {"biz": "aiRemoveSubtitleProgress", "taskId": task_id},
        credential_code=credential_code,
    )
    payload = data.get("data")
    if not isinstance(payload, list) or not payload:
        raise SubtitleRemovalProviderError("Provider progress response missing data")
    first_item = payload[0]
    if not isinstance(first_item, dict):
        raise SubtitleRemovalProviderError("Provider progress response missing data")
    return first_item
