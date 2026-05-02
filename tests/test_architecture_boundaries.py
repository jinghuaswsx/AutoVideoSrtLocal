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
        Path("appcore/runtime/__init__.py"),
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


def test_detail_image_zip_archive_construction_lives_outside_route_module():
    route_source = Path("web/routes/medias/detail_images.py").read_text(encoding="utf-8")

    assert "import zipfile" not in route_source
    assert "TemporaryDirectory(prefix=\"detail_images_zip_\")" not in route_source
    assert "TemporaryDirectory(prefix=\"localized_detail_images_zip_\")" not in route_source
    assert Path("web/services/media_detail_archives.py").exists()


def test_detail_image_upload_validation_lives_outside_route_module():
    route_source = Path("web/routes/medias/detail_images.py").read_text(encoding="utf-8")

    assert "files[{idx}]" not in route_source
    assert "images[{idx}]" not in route_source
    assert "object missing:" not in route_source
    assert Path("web/services/media_detail_uploads.py").exists()


def test_detail_image_translate_payload_construction_lives_outside_route_module():
    route_source = Path("web/routes/medias/detail_images.py").read_text(encoding="utf-8")

    assert "source_detail_image_id" not in route_source
    assert "source_detail_image_ids" not in route_source
    assert "auto_apply_detail_images" not in route_source
    assert Path("web/services/media_detail_translation.py").exists()


def test_detail_image_translate_task_projection_lives_outside_route_module():
    module_source = Path("web/routes/medias/detail_images.py").read_text(encoding="utf-8")
    module = ast.parse(module_source)
    route_function = next(
        node
        for node in module.body
        if isinstance(node, ast.FunctionDef) and node.name == "api_detail_image_translate_tasks"
    )
    route_source = ast.get_source_segment(module_source, route_function) or ""

    assert "ctx.get(\"entry\")" not in route_source
    assert "ctx.get(\"target_lang\")" not in route_source
    assert "applied_detail_image_ids" not in route_source
    assert Path("web/services/media_detail_translation.py").exists()


def test_detail_image_translate_apply_workflow_lives_outside_route_module():
    module_source = Path("web/routes/medias/detail_images.py").read_text(encoding="utf-8")
    module = ast.parse(module_source)
    route_function = next(
        node
        for node in module.body
        if isinstance(node, ast.FunctionDef) and node.name == "api_detail_images_apply_translate_task"
    )
    route_source = ast.get_source_segment(module_source, route_function) or ""

    assert "ctx.get(\"product_id\")" not in route_source
    assert "ctx.get(\"target_lang\")" not in route_source
    assert "skipped_failed_indices" not in route_source
    assert Path("web/services/media_detail_translation.py").exists()


def test_detail_image_mutation_workflows_live_outside_route_module():
    module_source = Path("web/routes/medias/detail_images.py").read_text(encoding="utf-8")
    module = ast.parse(module_source)
    route_sources = []
    for function_name in (
        "api_detail_images_delete",
        "api_detail_images_clear_all",
        "api_detail_images_reorder",
    ):
        route_function = next(
            node
            for node in module.body
            if isinstance(node, ast.FunctionDef) and node.name == function_name
        )
        route_sources.append(ast.get_source_segment(module_source, route_function) or "")
        direct_dao_calls = [
            call.func.attr
            for call in ast.walk(route_function)
            if isinstance(call, ast.Call)
            and isinstance(call.func, ast.Attribute)
            and isinstance(call.func.value, ast.Name)
            and call.func.value.id == "medias"
            and call.func.attr
            in {
                "soft_delete_detail_image",
                "soft_delete_detail_images_by_lang",
                "reorder_detail_images",
            }
        ]
        assert direct_dao_calls == []
    route_source = "\n".join(route_sources)

    assert "english detail images cannot be cleared via this endpoint" not in route_source
    assert Path("web/services/media_detail_mutations.py").exists()


def test_detail_image_from_url_request_planning_lives_outside_route_module():
    module_source = Path("web/routes/medias/detail_images.py").read_text(encoding="utf-8")
    module = ast.parse(module_source)
    route_function = next(
        node
        for node in module.body
        if isinstance(node, ast.FunctionDef) and node.name == "api_detail_images_from_url"
    )
    route_source = ast.get_source_segment(module_source, route_function) or ""

    assert "json.loads" not in route_source
    assert "localized_links_json" not in route_source
    assert "product_code required before inferring a default link" not in route_source
    assert Path("web/services/media_detail_from_url.py").exists()


def test_task_delete_storage_cleanup_lives_outside_route_module():
    module_source = Path("web/routes/task.py").read_text(encoding="utf-8")
    module = ast.parse(module_source)
    route_function = next(
        node
        for node in module.body
        if isinstance(node, ast.FunctionDef) and node.name == "delete_task"
    )
    route_source = ast.get_source_segment(module_source, route_function) or ""
    direct_cleanup_calls = [
        call.func.attr
        for call in ast.walk(route_function)
        if isinstance(call, ast.Call)
        and isinstance(call.func, ast.Attribute)
        and isinstance(call.func.value, ast.Name)
        and call.func.value.id == "cleanup"
        and call.func.attr in {"collect_task_tos_keys", "delete_task_storage"}
    ]

    assert direct_cleanup_calls == []
    assert "cleanup_payload" not in route_source
    assert Path("web/services/task_deletion.py").exists()


def test_task_rename_validation_lives_outside_route_module():
    module_source = Path("web/routes/task.py").read_text(encoding="utf-8")
    module = ast.parse(module_source)
    route_function = next(
        node
        for node in module.body
        if isinstance(node, ast.FunctionDef) and node.name == "rename_task"
    )
    route_source = ast.get_source_segment(module_source, route_function) or ""
    direct_conflict_calls = [
        call.func.id
        for call in ast.walk(route_function)
        if isinstance(call, ast.Call)
        and isinstance(call.func, ast.Name)
        and call.func.id == "_resolve_name_conflict"
    ]

    assert direct_conflict_calls == []
    assert "display_name required" not in route_source
    assert "名称不超过50个字符" not in route_source
    assert Path("web/services/task_rename.py").exists()


def test_task_name_helpers_live_outside_route_module():
    source = Path("web/routes/task.py").read_text(encoding="utf-8")

    assert "def _default_display_name" not in source
    assert "def _resolve_name_conflict" not in source
    assert Path("web/services/task_names.py").exists()


def test_task_av_input_helpers_live_outside_route_module():
    source = Path("web/routes/task.py").read_text(encoding="utf-8")

    assert "def _av_task_target_lang" not in source
    assert "def _collect_av_source_language" not in source
    assert "def _collect_av_translate_inputs" not in source
    assert "def _validate_av_translate_inputs" not in source
    assert "def _av_step_maps" not in source
    assert Path("web/services/task_av_inputs.py").exists()


def test_task_source_video_helpers_live_outside_route_module():
    source = Path("web/routes/task.py").read_text(encoding="utf-8")

    assert "def _ensure_local_source_video" not in source
    assert "def _task_requires_source_sync" not in source
    assert Path("web/services/task_source_video.py").exists()


def test_task_preview_artifact_path_helpers_live_outside_route_module():
    source = Path("web/routes/task.py").read_text(encoding="utf-8")

    assert "def _artifact_candidates" not in source
    assert "def _resolve_artifact_path" not in source
    assert Path("web/services/artifact_download.py").exists()


def test_task_range_file_response_lives_outside_route_module():
    source = Path("web/routes/task.py").read_text(encoding="utf-8")

    assert "def _send_with_range" not in source
    assert Path("web/services/artifact_download.py").exists()


def test_task_av_rewrite_compose_cleanup_lives_outside_route_module():
    source = Path("web/routes/task.py").read_text(encoding="utf-8")

    assert "def _clear_av_compose_outputs" not in source
    assert Path("web/services/task_av_rewrite.py").exists()


def test_task_av_rewrite_voice_resolution_lives_outside_route_module():
    source = Path("web/routes/task.py").read_text(encoding="utf-8")

    assert "def _resolve_av_voice_ids" not in source
    assert Path("web/services/task_av_rewrite.py").exists()


def test_task_av_rewrite_tts_audio_rebuild_lives_outside_route_module():
    source = Path("web/routes/task.py").read_text(encoding="utf-8")

    assert "def _rebuild_tts_full_audio" not in source
    assert Path("web/services/task_av_rewrite.py").exists()


def test_task_av_rewrite_translate_compare_artifact_lives_outside_route_module():
    source = Path("web/routes/task.py").read_text(encoding="utf-8")

    assert "def _build_translate_compare_artifact" not in source
    assert Path("web/services/task_av_rewrite.py").exists()


def test_task_start_bool_parsing_lives_outside_route_module():
    source = Path("web/routes/task.py").read_text(encoding="utf-8")

    assert "def _parse_bool" not in source
    assert Path("web/services/task_start_inputs.py").exists()


def test_task_start_request_payload_parsing_lives_outside_route_module():
    source = Path("web/routes/task.py").read_text(encoding="utf-8")

    assert "def _request_payload" not in source
    assert "request.get_json(silent=True) or {}" not in source
    assert Path("web/services/task_start_inputs.py").exists()


def test_task_user_access_helper_lives_outside_route_module():
    source = Path("web/routes/task.py").read_text(encoding="utf-8")

    assert "def _get_current_user_task" not in source
    assert "def _is_admin_user" not in source
    assert 'task.get("_user_id") != current_user.id' not in source
    assert "current_user.id if current_user.is_authenticated else None" not in source
    assert "store.get(task_id) or" not in source
    assert "store.get(task_id)" not in source
    assert Path("web/services/task_access.py").exists()


def test_task_not_found_response_lives_outside_route_module():
    source = Path("web/routes/task.py").read_text(encoding="utf-8")

    assert 'jsonify({"error": "Task not found"}), 404' not in source
    assert Path("web/services/task_responses.py").exists()


def test_task_prompt_lookup_lives_outside_route_module():
    source = Path("web/routes/task.py").read_text(encoding="utf-8")

    assert "SELECT prompt_text FROM user_prompts" not in source
    assert Path("web/services/task_prompts.py").exists()


def test_task_thumbnail_lookup_lives_outside_route_module():
    source = Path("web/routes/task.py").read_text(encoding="utf-8")

    assert "SELECT thumbnail_path, task_dir FROM projects" not in source
    assert 'os.path.exists(row["thumbnail_path"])' not in source
    assert Path("web/services/task_thumbnail.py").exists()


def test_task_capcut_deploy_workflow_lives_outside_route_module():
    module_source = Path("web/routes/task.py").read_text(encoding="utf-8")
    module = ast.parse(module_source)
    route_function = next(
        node
        for node in module.body
        if isinstance(node, ast.FunctionDef) and node.name == "deploy_capcut"
    )
    route_source = ast.get_source_segment(module_source, route_function) or ""

    assert "safe_task_dir_path" not in route_source
    assert "deploy_capcut_project" not in route_source
    assert "jianying_project_dir" not in route_source
    assert Path("web/services/task_capcut.py").exists()


def test_task_analysis_run_workflow_lives_outside_route_module():
    module_source = Path("web/routes/task.py").read_text(encoding="utf-8")
    module = ast.parse(module_source)
    route_function = next(
        node
        for node in module.body
        if isinstance(node, ast.FunctionDef) and node.name == "run_ai_analysis"
    )
    route_source = ast.get_source_segment(module_source, route_function) or ""

    assert "SELECT id FROM projects WHERE id=%s AND user_id=%s AND deleted_at IS NULL" not in route_source
    assert "pipeline_runner.run_analysis" not in route_source
    assert 'get("analysis") == "running"' not in route_source
    assert Path("web/services/task_analysis.py").exists()


def test_task_translate_billing_provider_mapping_lives_outside_route_module():
    source = Path("web/routes/task.py").read_text(encoding="utf-8")

    assert 'model_provider.startswith("vertex_adc_")' not in source
    assert 'billing_provider = "doubao"' not in source
    assert Path("web/services/task_llm.py").exists()


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
        # Phase B-4：image OpenAI 客户端创建迁到 _helpers，gemini_image 不再直连
        "appcore/llm_providers/_helpers/openrouter_image.py",
        # Phase C-3：pipeline.translate._call_openai_compat 的 OpenAI() 客户端创建
        # 迁到这里，pipeline/translate.py 顶部不再 `from openai import OpenAI`
        "appcore/llm_providers/_helpers/openai_compat.py",
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
