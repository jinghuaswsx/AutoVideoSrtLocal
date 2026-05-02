from __future__ import annotations

import ast
from pathlib import Path


def test_preview_artifacts_available_from_appcore_and_web_reexports():
    import appcore.preview_artifacts as appcore_preview
    import web.preview_artifacts as web_preview

    assert web_preview.build_asr_artifact is appcore_preview.build_asr_artifact
    assert web_preview.build_translate_artifact is appcore_preview.build_translate_artifact


def test_quality_assessment_available_from_appcore_and_web_reexports():
    import appcore.quality_assessment as appcore_quality
    import web.services.quality_assessment as web_quality

    assert web_quality.trigger_assessment is appcore_quality.trigger_assessment
    assert web_quality.AssessmentInProgressError is appcore_quality.AssessmentInProgressError


def test_core_runtime_preview_imports_do_not_depend_on_web_module():
    runtime_files = [
        Path("appcore/runtime.py"),
        Path("appcore/runtime_multi.py"),
        Path("appcore/runtime_de.py"),
        Path("appcore/runtime_fr.py"),
        Path("appcore/runtime_ja.py"),
        Path("appcore/runtime_omni.py"),
        Path("appcore/runtime_sentence_translate.py"),
    ]

    offenders = [
        str(path)
        for path in runtime_files
        if "web.preview_artifacts" in path.read_text(encoding="utf-8")
    ]

    assert offenders == []


def test_appcore_modules_do_not_import_web_package():
    offenders: list[str] = []

    for path in Path("appcore").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                module = node.module or ""
                if module == "web" or module.startswith("web."):
                    offenders.append(f"{path}:{node.lineno}")
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == "web" or alias.name.startswith("web."):
                        offenders.append(f"{path}:{node.lineno}")

    assert offenders == []


def test_video_creation_route_uses_project_state_helper_for_state_json_writes():
    source = Path("web/routes/video_creation.py").read_text(encoding="utf-8")

    assert "UPDATE projects SET state_json" not in source


def test_translate_voice_routes_use_project_state_helper_for_state_json_writes():
    route_files = [
        Path("web/routes/multi_translate.py"),
        Path("web/routes/omni_translate.py"),
        Path("web/routes/ja_translate.py"),
    ]
    offenders = [
        str(path)
        for path in route_files
        if "UPDATE projects SET state_json" in path.read_text(encoding="utf-8")
    ]

    assert offenders == []


def test_copywriting_translate_runtime_uses_project_state_helper_for_state_json_writes():
    source = Path("appcore/copywriting_translate_runtime.py").read_text(encoding="utf-8")

    assert "UPDATE projects SET state_json" not in source


def test_bulk_translate_recovery_uses_project_state_helper_for_state_json_writes():
    source = Path("appcore/bulk_translate_recovery.py").read_text(encoding="utf-8")

    assert "UPDATE projects SET status = %s, state_json" not in source


def test_text_translate_route_uses_project_state_helper_for_state_json_writes():
    source = Path("web/routes/text_translate.py").read_text(encoding="utf-8")

    assert "UPDATE projects SET status = 'done', display_name" not in source


def test_task_recovery_uses_project_state_helper_for_state_json_writes():
    source = Path("appcore/task_recovery.py").read_text(encoding="utf-8")

    assert "UPDATE projects SET state_json = %s, status = %s" not in source


def test_server_background_threads_use_runner_lifecycle_or_explicit_cleanup_allowlist():
    allowed_direct_thread_files = {
        "appcore/runner_lifecycle.py",
        "appcore/medias_detail_fetch_tasks.py",
        "appcore/voice_match_tasks.py",
    }
    offenders: list[str] = []

    for root in (Path("appcore"), Path("web")):
        for path in root.rglob("*.py"):
            path_key = path.as_posix()
            if path_key in allowed_direct_thread_files:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                    if node.func.attr == "Thread":
                        offenders.append(f"{path}:{node.lineno}")

    assert offenders == []


def test_direct_provider_sdk_imports_stay_in_adapter_or_legacy_files():
    allowed_paths = {
        "appcore/gemini_image.py",
        "appcore/llm_providers/gemini_aistudio_adapter.py",
        "appcore/llm_providers/gemini_vertex_adapter.py",
        "appcore/llm_providers/openrouter_adapter.py",
        "pipeline/translate.py",
        "pipeline/video_csk.py",
        "pipeline/video_review.py",
        "pipeline/video_score.py",
    }
    offenders: list[str] = []

    for root in (Path("appcore"), Path("pipeline"), Path("web"), Path("tools")):
        if not root.exists():
            continue
        for path in root.rglob("*.py"):
            path_key = path.as_posix()
            tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom):
                    module = node.module or ""
                    imports_openai = module == "openai"
                    imports_legacy_gemini = (
                        module == "appcore"
                        and any(alias.name == "gemini" for alias in node.names)
                    )
                    if (imports_openai or imports_legacy_gemini) and path_key not in allowed_paths:
                        offenders.append(f"{path}:{node.lineno}")
                elif isinstance(node, ast.Import):
                    for alias in node.names:
                        if alias.name in {"openai", "appcore.gemini"} and path_key not in allowed_paths:
                            offenders.append(f"{path}:{node.lineno}")

    assert offenders == []
