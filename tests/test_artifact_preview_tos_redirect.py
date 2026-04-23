from __future__ import annotations

from web.services import artifact_download


def _task_with_legacy_upload(artifact_kind: str, variant: str = "normal") -> dict:
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


def test_preview_no_longer_redirects_to_legacy_object_storage():
    task = _task_with_legacy_upload("hard_video")

    assert artifact_download.preview_artifact_tos_redirect(task, "hard_video") is None


def test_legacy_upload_record_is_only_metadata():
    task = _task_with_legacy_upload("capcut_archive", variant="hook_cta")

    assert artifact_download.get_tos_upload_record(task, "capcut_archive", "hook_cta") == {
        "tos_key": "artifacts/1/task-xxx/hook_cta/file.bin",
        "artifact_kind": "capcut_archive",
        "variant": "hook_cta",
    }
