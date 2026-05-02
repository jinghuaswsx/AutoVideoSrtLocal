"""Helpers for AV sentence rewrite state updates."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AvComposeOutputs:
    result: dict
    exports: dict
    artifacts: dict
    preview_files: dict
    tos_uploads: dict
    variant_result: dict
    variant_exports: dict
    variant_artifacts: dict
    variant_preview_files: dict


def clear_av_compose_outputs(
    task: dict,
    variant_state: dict,
    variant: str = "av",
) -> AvComposeOutputs:
    result = dict(task.get("result") or {})
    exports = dict(task.get("exports") or {})
    artifacts = dict(task.get("artifacts") or {})
    preview_files = dict(task.get("preview_files") or {})
    tos_uploads = dict(task.get("tos_uploads") or {})

    result.pop("hard_video", None)
    exports.pop("capcut_archive", None)
    exports.pop("capcut_project", None)
    exports.pop("jianying_project_dir", None)
    artifacts.pop("compose", None)
    artifacts.pop("export", None)
    preview_files.pop("hard_video", None)

    for key, payload in list(tos_uploads.items()):
        payload_variant = payload.get("variant") if isinstance(payload, dict) else None
        if key.startswith(f"{variant}:") or payload_variant == variant:
            tos_uploads.pop(key, None)

    variant_result = dict(variant_state.get("result") or {})
    variant_exports = dict(variant_state.get("exports") or {})
    variant_artifacts = dict(variant_state.get("artifacts") or {})
    variant_preview_files = dict(variant_state.get("preview_files") or {})

    variant_result.clear()
    variant_exports.clear()
    variant_artifacts.pop("compose", None)
    variant_artifacts.pop("export", None)
    variant_preview_files.pop("hard_video", None)

    return AvComposeOutputs(
        result=result,
        exports=exports,
        artifacts=artifacts,
        preview_files=preview_files,
        tos_uploads=tos_uploads,
        variant_result=variant_result,
        variant_exports=variant_exports,
        variant_artifacts=variant_artifacts,
        variant_preview_files=variant_preview_files,
    )
