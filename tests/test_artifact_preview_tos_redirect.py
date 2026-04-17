"""/api/*/<task_id>/artifact/<name> 对已上传 TOS 的成品返回 302 签名 URL，
确保视频预览从 TOS 直拉，不再让 Flask 代理本地文件。"""
from __future__ import annotations

from web.services import artifact_download


def _task_with_upload(artifact_kind: str, variant: str = "normal") -> dict:
    return {
        "_user_id": 1,
        "tos_uploads": {
            f"{variant}:{artifact_kind}": {
                "tos_key": f"artifacts/1/task-xxx/{variant}/file.bin",
                "artifact_kind": artifact_kind,
                "variant": variant,
            }
        },
    }


def test_preview_redirect_for_hard_video_with_upload(monkeypatch):
    task = _task_with_upload("hard_video")
    monkeypatch.setattr(
        artifact_download.tos_clients,
        "generate_signed_download_url",
        lambda key, expires=None: f"https://tos.example/{key}",
    )

    resp = artifact_download.preview_artifact_tos_redirect(task, "hard_video")

    assert resp is not None
    assert resp.status_code in (301, 302)
    assert resp.headers["Location"] == "https://tos.example/artifacts/1/task-xxx/normal/file.bin"


def test_preview_redirect_for_soft_video_and_srt(monkeypatch):
    monkeypatch.setattr(
        artifact_download.tos_clients,
        "generate_signed_download_url",
        lambda key, expires=None: f"https://tos.example/{key}",
    )
    for kind in ("soft_video", "srt"):
        task = _task_with_upload(kind)
        resp = artifact_download.preview_artifact_tos_redirect(task, kind)
        assert resp is not None, kind
        assert "tos.example" in resp.headers["Location"]


def test_preview_redirect_respects_variant(monkeypatch):
    task = _task_with_upload("hard_video", variant="hook_cta")
    monkeypatch.setattr(
        artifact_download.tos_clients,
        "generate_signed_download_url",
        lambda key, expires=None: f"https://tos.example/{key}",
    )
    resp = artifact_download.preview_artifact_tos_redirect(task, "hard_video", variant="hook_cta")
    assert resp is not None
    assert "hook_cta/file.bin" in resp.headers["Location"]


def test_preview_redirect_returns_none_when_not_uploaded():
    task = {"_user_id": 1, "tos_uploads": {}}
    assert artifact_download.preview_artifact_tos_redirect(task, "hard_video") is None


def test_preview_redirect_ignored_for_non_uploaded_names():
    task = _task_with_upload("hard_video")
    # audio_extract / tts_full_audio 不在 TOS 上传产物之列，必须走本地
    assert artifact_download.preview_artifact_tos_redirect(task, "audio_extract") is None
    assert artifact_download.preview_artifact_tos_redirect(task, "tts_full_audio") is None


def test_preview_redirect_swallows_signed_url_errors(monkeypatch):
    def _boom(key, expires=None):
        raise RuntimeError("TOS SDK down")

    task = _task_with_upload("hard_video")
    monkeypatch.setattr(artifact_download.tos_clients, "generate_signed_download_url", _boom)

    # 退回 None，让路由 fallback 到本地 send_file，不让签名错误把预览打死
    assert artifact_download.preview_artifact_tos_redirect(task, "hard_video") is None
