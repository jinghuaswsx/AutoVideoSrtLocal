from __future__ import annotations

import requests

from appcore.llm_provider_configs import (
    ProviderConfigError,
    require_provider_config,
)
from config import SUBTITLE_REMOVAL_PROVIDER_URL_DEFAULT


class SubtitleRemovalProviderError(RuntimeError):
    pass


def _provider_cfg():
    try:
        return require_provider_config("subtitle_removal")
    except ProviderConfigError as exc:
        raise SubtitleRemovalProviderError(str(exc)) from exc


def _provider_url() -> str:
    cfg = _provider_cfg()
    try:
        return cfg.require_base_url(default=SUBTITLE_REMOVAL_PROVIDER_URL_DEFAULT)
    except ProviderConfigError as exc:
        raise SubtitleRemovalProviderError(str(exc)) from exc


def _headers() -> dict[str, str]:
    cfg = _provider_cfg()
    try:
        token = cfg.require_api_key()
    except ProviderConfigError as exc:
        raise SubtitleRemovalProviderError(str(exc)) from exc
    return {"authorization": token}


def _notify_url() -> str:
    cfg = _provider_cfg()
    extra = cfg.extra_config or {}
    return (extra.get("notify_url") or extra.get("notifyUrl") or "").strip()


def _post(payload: dict) -> dict:
    try:
        response = requests.post(
            _provider_url(),
            headers=_headers(),
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


def submit_task(
    *,
    file_size_mb: float,
    duration_seconds: float,
    resolution: str,
    video_name: str,
    source_url: str,
    cover_url: str = "",
    erase_text_type: str = "subtitle",
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
        "notifyUrl": _notify_url(),
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
    data = _post(payload)
    payload_result = data.get("data")
    if isinstance(payload_result, dict) and payload_result.get("taskId"):
        return str(payload_result["taskId"])
    if isinstance(payload_result, list) and payload_result and isinstance(payload_result[0], dict) and payload_result[0].get("taskId"):
        return str(payload_result[0]["taskId"])
    if isinstance(payload_result, str) and payload_result.strip():
        return payload_result.strip()
    raise SubtitleRemovalProviderError("Provider submit response missing taskId")


def query_progress(task_id: str) -> dict:
    data = _post({"biz": "aiRemoveSubtitleProgress", "taskId": task_id})
    payload = data.get("data")
    if not isinstance(payload, list) or not payload:
        raise SubtitleRemovalProviderError("Provider progress response missing data")
    first_item = payload[0]
    if not isinstance(first_item, dict):
        raise SubtitleRemovalProviderError("Provider progress response missing data")
    return first_item
