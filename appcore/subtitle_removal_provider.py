from __future__ import annotations

import requests

import config


class SubtitleRemovalProviderError(RuntimeError):
    pass


def _provider_url() -> str:
    url = (config.SUBTITLE_REMOVAL_PROVIDER_URL or "").strip()
    if not url:
        raise SubtitleRemovalProviderError("SUBTITLE_REMOVAL_PROVIDER_URL is not configured")
    return url


def _headers() -> dict[str, str]:
    token = (config.SUBTITLE_REMOVAL_PROVIDER_TOKEN or "").strip()
    if not token:
        raise SubtitleRemovalProviderError("SUBTITLE_REMOVAL_PROVIDER_TOKEN is not configured")
    return {"authorization": token}


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


def submit_task(*, file_size_mb: float, duration_seconds: float, resolution: str, video_name: str, source_url: str, cover_url: str = "") -> str:
    data = _post(
        {
            "biz": "aiRemoveSubtitleSubmitTask",
            "fileSize": round(file_size_mb, 2),
            "duration": round(duration_seconds, 2),
            "resolution": resolution,
            "videoName": video_name,
            "coverUrl": cover_url,
            "url": source_url,
            "notifyUrl": config.SUBTITLE_REMOVAL_NOTIFY_URL,
        }
    )
    payload = data.get("data")
    if isinstance(payload, dict) and payload.get("taskId"):
        return str(payload["taskId"])
    if isinstance(payload, list) and payload and isinstance(payload[0], dict) and payload[0].get("taskId"):
        return str(payload[0]["taskId"])
    if isinstance(payload, str) and payload.strip():
        return payload.strip()
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
