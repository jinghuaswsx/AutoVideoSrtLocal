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


def test_pipeline_runner_class_lives_outside_runtime_facade():
    runtime_facade = Path("appcore/runtime/__init__.py").read_text(encoding="utf-8")

    assert "class PipelineRunner" not in runtime_facade
    assert "from ._pipeline_runner import PipelineRunner" in runtime_facade
    assert Path("appcore/runtime/_pipeline_runner.py").exists()


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


def test_detail_image_archive_responses_live_outside_route_module():
    module_source = Path("web/routes/medias/detail_images.py").read_text(encoding="utf-8")
    module = ast.parse(module_source)
    route_sources = []
    for function_name in (
        "api_detail_images_download_zip",
        "api_detail_images_download_localized_zip",
    ):
        route_function = next(
            node
            for node in module.body
            if isinstance(node, ast.FunctionDef) and node.name == function_name
        )
        route_sources.append(ast.get_source_segment(module_source, route_function) or "")
        direct_archive_calls = [
            f"{call.func.value.id}.{call.func.attr}"
            for call in ast.walk(route_function)
            if isinstance(call, ast.Call)
            and isinstance(call.func, ast.Attribute)
            and isinstance(call.func.value, ast.Name)
            and call.func.value.id == "medias"
            and call.func.attr in {"list_detail_images", "list_languages"}
        ]
        assert direct_archive_calls == []

    route_source = "\n".join(route_sources)
    assert "build_detail_images_archive" not in route_source
    assert "DetailImagesZipGroup" not in route_source
    assert "_detail_images_is_gif" not in route_source
    assert 'temp_prefix="localized_detail_images_zip_"' not in route_source
    assert "object_keys" not in route_source
    assert "_build_detail_images_zip_response" in route_source
    assert "_build_localized_detail_images_zip_response" in route_source
    assert Path("web/services/media_detail_archives.py").exists()


def test_detail_image_upload_validation_lives_outside_route_module():
    route_source = Path("web/routes/medias/detail_images.py").read_text(encoding="utf-8")

    assert "files[{idx}]" not in route_source
    assert "images[{idx}]" not in route_source
    assert "object missing:" not in route_source
    assert Path("web/services/media_detail_uploads.py").exists()


def test_detail_image_list_response_lives_outside_route_module():
    module_source = Path("web/routes/medias/detail_images.py").read_text(encoding="utf-8")
    module = ast.parse(module_source)
    route_function = next(
        node
        for node in module.body
        if isinstance(node, ast.FunctionDef) and node.name == "api_detail_images_list"
    )
    route_source = ast.get_source_segment(module_source, route_function) or ""

    direct_calls = [
        call.func.attr
        for call in ast.walk(route_function)
        if isinstance(call, ast.Call)
        and isinstance(call.func, ast.Attribute)
        and isinstance(call.func.value, ast.Name)
        and call.func.value.id == "medias"
        and call.func.attr in {"is_valid_language", "list_detail_images"}
    ]

    assert direct_calls == []
    assert "涓嶆敮鎸佺殑璇" not in route_source
    assert "_serialize_detail_image(" not in route_source
    assert "_build_detail_images_list_response" in route_source
    assert Path("web/services/media_detail_listing.py").exists()


def test_detail_image_proxy_access_lives_outside_route_module():
    module_source = Path("web/routes/medias/detail_images.py").read_text(encoding="utf-8")
    module = ast.parse(module_source)
    route_function = next(
        node
        for node in module.body
        if isinstance(node, ast.FunctionDef) and node.name == "detail_image_proxy"
    )
    route_source = ast.get_source_segment(module_source, route_function) or ""

    direct_calls = [
        f"{call.func.value.id}.{call.func.attr}"
        for call in ast.walk(route_function)
        if isinstance(call, ast.Call)
        and isinstance(call.func, ast.Attribute)
        and isinstance(call.func.value, ast.Name)
        and call.func.value.id == "medias"
        and call.func.attr in {"get_detail_image", "get_product"}
    ]

    assert direct_calls == []
    assert "_can_access_product" not in route_source
    assert "deleted_at" not in route_source
    assert "_build_detail_image_proxy_response" in route_source
    assert Path("web/services/media_detail_listing.py").exists()


def test_detail_image_translate_payload_construction_lives_outside_route_module():
    route_source = Path("web/routes/medias/detail_images.py").read_text(encoding="utf-8")

    assert "source_detail_image_id" not in route_source
    assert "source_detail_image_ids" not in route_source
    assert "auto_apply_detail_images" not in route_source
    assert Path("web/services/media_detail_translation.py").exists()


def test_detail_image_translate_from_en_response_lives_outside_route_module():
    module_source = Path("web/routes/medias/detail_images.py").read_text(encoding="utf-8")
    module = ast.parse(module_source)
    route_function = next(
        node
        for node in module.body
        if isinstance(node, ast.FunctionDef) and node.name == "api_detail_images_translate_from_en"
    )
    route_source = ast.get_source_segment(module_source, route_function) or ""

    direct_calls = [
        f"{call.func.value.id}.{call.func.attr}"
        for call in ast.walk(route_function)
        if isinstance(call, ast.Call)
        and isinstance(call.func, ast.Attribute)
        and isinstance(call.func.value, ast.Name)
        and (
            (call.func.value.id == "medias" and call.func.attr in {"list_detail_images", "get_language_name"})
            or (call.func.value.id == "task_state" and call.func.attr == "create_image_translate")
        )
    ]

    assert direct_calls == []
    assert "IMAGE_TRANSLATE_DEFAULT_CONCURRENCY_MODE" not in route_source
    assert "build_detail_translate_task_payload" not in route_source
    assert "_detail_images_is_gif" not in route_source
    assert "get_prompts_for_lang" not in route_source
    assert "_default_image_translate_model_id" not in route_source
    assert "_start_image_translate_runner" not in route_source
    assert "_ensure_product_listed" not in route_source
    assert "product_not_listed" not in route_source
    assert "is_product_listed" not in route_source
    assert "_build_detail_translate_from_en_response" in route_source
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

    direct_calls = [
        f"{call.func.value.id}.{call.func.attr}"
        for call in ast.walk(route_function)
        if isinstance(call, ast.Call)
        and isinstance(call.func, ast.Attribute)
        and isinstance(call.func.value, ast.Name)
        and (
            (call.func.value.id == "medias" and call.func.attr == "is_valid_language")
            or (call.func.value.id == "routes" and call.func.attr == "db_query")
        )
    ]

    assert direct_calls == []
    assert "db_query(" not in route_source
    assert "project_detail_translate_task_rows" not in route_source
    assert "ctx.get(\"entry\")" not in route_source
    assert "ctx.get(\"target_lang\")" not in route_source
    assert "applied_detail_image_ids" not in route_source
    assert "_build_detail_translate_tasks_response" in route_source
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

    direct_calls = [
        f"{call.func.value.id}.{call.func.attr}"
        for call in ast.walk(route_function)
        if isinstance(call, ast.Call)
        and isinstance(call.func, ast.Attribute)
        and isinstance(call.func.value, ast.Name)
        and (
            (call.func.value.id == "medias" and call.func.attr == "is_valid_language")
            or (call.func.value.id == "store" and call.func.attr == "get")
            or (call.func.value.id == "image_translate_runner" and call.func.attr == "is_running")
            or (
                call.func.value.id == "image_translate_runtime"
                and call.func.attr == "apply_translated_detail_images_from_task"
            )
        )
    ]

    assert direct_calls == []
    assert "ctx.get(\"product_id\")" not in route_source
    assert "ctx.get(\"target_lang\")" not in route_source
    assert "skipped_failed_indices" not in route_source
    assert "english detail images do not need manual apply" not in route_source
    assert "_build_detail_translate_apply_response" in route_source
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

    assert "_parse_lang(" not in route_source
    assert "delete_detail_image(" not in route_source
    assert "clear_detail_images(" not in route_source
    assert "reorder_detail_images_command(" not in route_source
    assert "english detail images cannot be cleared via this endpoint" not in route_source
    assert "_build_detail_images_delete_response" in route_source
    assert "_build_detail_images_clear_response" in route_source
    assert "_build_detail_images_reorder_response" in route_source
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


def test_detail_image_from_url_response_lives_outside_route_module():
    module_source = Path("web/routes/medias/detail_images.py").read_text(encoding="utf-8")
    module = ast.parse(module_source)
    route_function = next(
        node
        for node in module.body
        if isinstance(node, ast.FunctionDef) and node.name == "api_detail_images_from_url"
    )
    route_source = ast.get_source_segment(module_source, route_function) or ""

    direct_detail_calls = [
        call.func.attr
        for call in ast.walk(route_function)
        if isinstance(call, ast.Call)
        and isinstance(call.func, ast.Attribute)
        and isinstance(call.func.value, ast.Name)
        and call.func.value.id == "medias"
        and call.func.attr
        in {
            "soft_delete_detail_images_by_lang",
            "add_detail_image",
            "get_detail_image",
        }
    ]

    assert direct_detail_calls == []
    assert "LinkCheckFetcher" not in route_source
    assert "fetch_page" not in route_source
    assert "LocaleLockError" not in route_source
    assert "_download_image_to_local_media(" not in route_source
    assert "_detail_image_existing_counts" not in route_source
    assert "_serialize_detail_image(" not in route_source
    assert "no carousel/detail images detected on the page" not in route_source
    assert "_build_detail_images_from_url_response" in route_source
    assert Path("web/services/media_detail_from_url.py").exists()


def test_detail_image_from_url_status_response_lives_outside_route_module():
    module_source = Path("web/routes/medias/detail_images.py").read_text(encoding="utf-8")
    module = ast.parse(module_source)
    route_function = next(
        node
        for node in module.body
        if isinstance(node, ast.FunctionDef) and node.name == "api_detail_images_from_url_status"
    )
    route_source = ast.get_source_segment(module_source, route_function) or ""

    assert "medias_detail_fetch_tasks" not in route_source
    assert ".get(task_id" not in route_source
    assert "task not found" not in route_source
    assert "product_id" not in route_source
    assert "_build_detail_images_from_url_status_response" in route_source
    assert Path("web/services/media_detail_from_url.py").exists()


def test_detail_image_upload_responses_live_outside_route_module():
    module_source = Path("web/routes/medias/detail_images.py").read_text(encoding="utf-8")
    module = ast.parse(module_source)
    route_sources = []
    for function_name in ("api_detail_images_bootstrap", "api_detail_images_complete"):
        route_function = next(
            node
            for node in module.body
            if isinstance(node, ast.FunctionDef) and node.name == function_name
        )
        route_sources.append(ast.get_source_segment(module_source, route_function) or "")
        direct_detail_calls = [
            f"{call.func.value.id}.{call.func.attr}"
            for call in ast.walk(route_function)
            if isinstance(call, ast.Call)
            and isinstance(call.func, ast.Attribute)
            and isinstance(call.func.value, ast.Name)
            and call.func.value.id in {"object_keys", "medias"}
            and call.func.attr
            in {
                "build_media_object_key",
                "add_detail_image",
                "get_detail_image",
            }
        ]
        assert direct_detail_calls == []
    route_source = "\n".join(route_sources)

    assert "files required" not in route_source
    assert "images required" not in route_source
    assert "object missing" not in route_source
    assert "storage_backend" not in route_source
    assert "_build_detail_images_bootstrap_response" in route_source
    assert "_build_detail_images_complete_response" in route_source
    assert Path("web/services/media_detail_uploads.py").exists()


def test_media_products_list_response_lives_outside_route_module():
    module_source = Path("web/routes/medias/products.py").read_text(encoding="utf-8")
    module = ast.parse(module_source)
    route_function = next(
        node
        for node in module.body
        if isinstance(node, ast.FunctionDef) and node.name == "api_list_products"
    )
    route_source = ast.get_source_segment(module_source, route_function) or ""

    assert "medias.list_products" not in route_source
    assert "count_items_by_product" not in route_source
    assert "list_product_skus_batch" not in route_source
    assert "list_xmyc_unit_prices" not in route_source
    assert "_serialize_product(" not in route_source
    assert "_build_products_list_response" in route_source
    assert Path("web/services/media_products_listing.py").exists()


def test_media_product_detail_response_lives_outside_route_module():
    module_source = Path("web/routes/medias/products.py").read_text(encoding="utf-8")
    module = ast.parse(module_source)
    route_function = next(
        node
        for node in module.body
        if isinstance(node, ast.FunctionDef) and node.name == "api_get_product"
    )
    route_source = ast.get_source_segment(module_source, route_function) or ""

    assert "get_product_covers" not in route_source
    assert "list_items" not in route_source
    assert "list_raw_sources" not in route_source
    assert "list_product_skus" not in route_source
    assert "list_xmyc_unit_prices" not in route_source
    assert "list_copywritings" not in route_source
    assert "_serialize_product(" not in route_source
    assert "_serialize_item(" not in route_source
    assert "_build_product_detail_response" in route_source
    assert Path("web/services/media_product_detail.py").exists()


def test_media_product_owner_update_response_lives_outside_route_module():
    module_source = Path("web/routes/medias/products.py").read_text(encoding="utf-8")
    module = ast.parse(module_source)
    route_function = next(
        node
        for node in module.body
        if isinstance(node, ast.FunctionDef) and node.name == "api_update_product_owner"
    )
    route_source = ast.get_source_segment(module_source, route_function) or ""

    assert "medias.get_product" not in route_source
    assert "medias.update_product_owner" not in route_source
    assert "medias.get_user_display_name" not in route_source
    assert "user_id required" not in route_source
    assert "仅管理员可操作" not in route_source
    assert "_build_product_owner_update_response" in route_source
    assert Path("web/services/media_product_owner.py").exists()


def test_media_product_create_response_lives_outside_route_module():
    module_source = Path("web/routes/medias/products.py").read_text(encoding="utf-8")
    module = ast.parse(module_source)
    route_function = next(
        node
        for node in module.body
        if isinstance(node, ast.FunctionDef) and node.name == "api_create_product"
    )
    route_source = ast.get_source_segment(module_source, route_function) or ""

    assert "medias.get_product_by_code" not in route_source
    assert "medias.create_product" not in route_source
    assert "_validate_product_code" not in route_source
    assert "name required" not in route_source
    assert "product_code already exists" not in route_source
    assert "_build_product_create_response" in route_source
    assert Path("web/services/media_product_mutations.py").exists()


def test_media_product_translate_listing_gate_lives_outside_route_module():
    module_source = Path("web/routes/medias/translate.py").read_text(encoding="utf-8")
    module = ast.parse(module_source)
    route_function = next(
        node
        for node in module.body
        if isinstance(node, ast.FunctionDef) and node.name == "api_product_translate"
    )
    route_source = ast.get_source_segment(module_source, route_function) or ""

    assert "_ensure_product_listed" not in route_source
    assert "product_not_listed" not in route_source
    assert "is_product_listed" not in route_source
    assert "start_product_translation" in route_source
    assert Path("web/services/media_product_translate.py").exists()


def test_media_product_update_response_lives_outside_route_module():
    module_source = Path("web/routes/medias/products.py").read_text(encoding="utf-8")
    module = ast.parse(module_source)
    route_function = next(
        node
        for node in module.body
        if isinstance(node, ast.FunctionDef) and node.name == "api_update_product"
    )
    route_source = ast.get_source_segment(module_source, route_function) or ""

    assert "medias.get_product_by_code" not in route_source
    assert "medias.update_product" not in route_source
    assert "medias.replace_copywritings" not in route_source
    assert "_ROAS_PRODUCT_FIELDS" not in route_source
    assert "localized_links_json" not in route_source
    assert "ad_supported_langs" not in route_source
    assert "uk_media_products_mk_id" not in route_source
    assert "_build_product_update_response" in route_source
    assert Path("web/services/media_product_mutations.py").exists()


def test_media_product_delete_response_lives_outside_route_module():
    module_source = Path("web/routes/medias/products.py").read_text(encoding="utf-8")
    module = ast.parse(module_source)
    route_function = next(
        node
        for node in module.body
        if isinstance(node, ast.FunctionDef) and node.name == "api_delete_product"
    )
    route_source = ast.get_source_segment(module_source, route_function) or ""

    assert "medias.soft_delete_product" not in route_source
    assert "_build_product_delete_response" in route_source
    assert Path("web/services/media_product_mutations.py").exists()


def test_raw_source_list_update_delete_responses_live_outside_route_module():
    module_source = Path("web/routes/medias/raw_sources.py").read_text(encoding="utf-8")
    module = ast.parse(module_source)
    route_sources = []
    for function_name in (
        "api_list_raw_sources",
        "api_update_raw_source",
        "api_delete_raw_source",
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
                "list_raw_sources",
                "update_raw_source",
                "soft_delete_raw_source",
            }
        ]
        assert direct_dao_calls == []
    route_source = "\n".join(route_sources)

    assert "sort_order must be int" not in route_source
    assert "no valid fields" not in route_source
    assert "_raw_source_filename_error_response(display_name)" not in route_source
    assert "_serialize_raw_source(" not in route_source
    assert "_build_raw_sources_list_response" in route_source
    assert "_build_raw_source_update_response" in route_source
    assert "_build_raw_source_delete_response" in route_source
    assert Path("web/services/media_raw_sources.py").exists()


def test_raw_source_create_response_lives_outside_route_module():
    module_source = Path("web/routes/medias/raw_sources.py").read_text(encoding="utf-8")
    module = ast.parse(module_source)
    route_function = next(
        node
        for node in module.body
        if isinstance(node, ast.FunctionDef) and node.name == "api_create_raw_source"
    )
    route_source = ast.get_source_segment(module_source, route_function) or ""

    direct_calls = [
        f"{call.func.value.id}.{call.func.attr}"
        for call in ast.walk(route_function)
        if isinstance(call, ast.Call)
        and isinstance(call.func, ast.Attribute)
        and isinstance(call.func.value, ast.Name)
        and call.func.value.id
        in {
            "local_media_storage",
            "object_keys",
            "medias",
        }
        and call.func.attr
        in {
            "write_bytes",
            "build_media_raw_source_key",
            "create_raw_source",
            "get_raw_source",
        }
    ]

    assert direct_calls == []
    assert "video and cover both required" not in route_source
    assert "video too large (>2GB)" not in route_source
    assert "upload video failed:" not in route_source
    assert "upload cover failed:" not in route_source
    assert "db insert failed:" not in route_source
    assert "english_video_required" not in route_source
    assert "raw_source_filename_mismatch" not in route_source
    assert "_serialize_raw_source(" not in route_source
    assert "_build_raw_source_create_response" in route_source
    assert Path("web/services/media_raw_sources.py").exists()


def test_media_evaluation_responses_live_outside_route_module():
    module_source = Path("web/routes/medias/evaluation.py").read_text(encoding="utf-8")
    module = ast.parse(module_source)
    route_sources = []
    for function_name in (
        "api_product_evaluate",
        "api_product_evaluate_request_preview",
        "api_product_evaluate_request_payload",
    ):
        route_function = next(
            node
            for node in module.body
            if isinstance(node, ast.FunctionDef) and node.name == function_name
        )
        route_sources.append(ast.get_source_segment(module_source, route_function) or "")
        direct_evaluation_calls = [
            call.func.attr
            for call in ast.walk(route_function)
            if isinstance(call, ast.Call)
            and isinstance(call.func, ast.Attribute)
            and isinstance(call.func.value, ast.Name)
            and call.func.value.id == "material_evaluation"
            and call.func.attr
            in {
                "evaluate_product_if_ready",
                "build_request_debug_payload",
            }
        ]
        assert direct_evaluation_calls == []
    route_source = "\n".join(route_sources)

    assert "_material_evaluation_message(result)" not in route_source
    assert "full_payload_url" not in route_source
    assert "_build_product_evaluation_response" in route_source
    assert "_build_product_evaluation_preview_response" in route_source
    assert "_build_product_evaluation_payload_response" in route_source
    assert Path("web/services/media_evaluation.py").exists()


def test_media_item_update_delete_responses_live_outside_route_module():
    module_source = Path("web/routes/medias/items.py").read_text(encoding="utf-8")
    module = ast.parse(module_source)
    route_sources = []
    for function_name in ("api_update_item", "api_delete_item"):
        route_function = next(
            node
            for node in module.body
            if isinstance(node, ast.FunctionDef) and node.name == function_name
        )
        route_sources.append(ast.get_source_segment(module_source, route_function) or "")
        direct_item_write_calls = [
            call.func.attr
            for call in ast.walk(route_function)
            if isinstance(call, ast.Call)
            and isinstance(call.func, ast.Attribute)
            and isinstance(call.func.value, ast.Name)
            and call.func.value.id == "medias"
            and call.func.attr
            in {
                "update_item_display_name",
                "soft_delete_item",
            }
        ]
        assert direct_item_write_calls == []
    route_source = "\n".join(route_sources)

    assert "display_name required" not in route_source
    assert "display_name too long" not in route_source
    assert "_serialize_item(" not in route_source
    assert "_build_item_update_response" in route_source
    assert "_build_item_delete_response" in route_source
    assert Path("web/services/media_items.py").exists()


def test_media_item_upload_responses_live_outside_route_module():
    module_source = Path("web/routes/medias/items.py").read_text(encoding="utf-8")
    module = ast.parse(module_source)
    route_sources = []
    for function_name in ("api_item_bootstrap", "api_item_complete"):
        route_function = next(
            node
            for node in module.body
            if isinstance(node, ast.FunctionDef) and node.name == function_name
        )
        route_sources.append(ast.get_source_segment(module_source, route_function) or "")
        direct_item_calls = [
            call.func.attr
            for call in ast.walk(route_function)
            if isinstance(call, ast.Call)
            and isinstance(call.func, ast.Attribute)
            and isinstance(call.func.value, ast.Name)
            and call.func.value.id == "medias"
            and call.func.attr == "create_item"
        ]
        direct_object_key_calls = [
            call.func.attr
            for call in ast.walk(route_function)
            if isinstance(call, ast.Call)
            and isinstance(call.func, ast.Attribute)
            and isinstance(call.func.value, ast.Name)
            and call.func.value.id == "object_keys"
        ]
        assert direct_item_calls == []
        assert direct_object_key_calls == []
    route_source = "\n".join(route_sources)

    assert "object_key and filename required" not in route_source
    assert "object not found" not in route_source
    assert "filename required" not in route_source
    assert "extract_thumbnail" not in route_source
    assert "get_media_duration" not in route_source
    assert "db_execute" not in route_source
    assert "_ensure_product_listed" not in route_source
    assert "product_not_listed" not in route_source
    assert "is_product_listed" not in route_source
    assert "_build_item_bootstrap_response" in route_source
    assert "_build_item_complete_response" in route_source
    assert Path("web/services/media_items.py").exists()


def test_media_item_video_ai_review_responses_live_outside_route_module():
    module_source = Path("web/routes/medias/items.py").read_text(encoding="utf-8")
    module = ast.parse(module_source)
    route_sources = []
    for function_name in ("api_run_video_ai_review", "api_get_video_ai_review"):
        route_function = next(
            node
            for node in module.body
            if isinstance(node, ast.FunctionDef) and node.name == function_name
        )
        route_sources.append(ast.get_source_segment(module_source, route_function) or "")
    route_source = "\n".join(route_sources)

    assert "from appcore import video_ai_review" not in route_source
    assert "video_ai_review." not in route_source
    assert "trigger_review" not in route_source
    assert "latest_review" not in route_source
    assert "ReviewInProgressError" not in route_source
    assert "AI 视频分析正在运行中" not in route_source
    assert '"status": "started"' not in route_source
    assert "start_media_item_video_ai_review" in route_source
    assert "get_media_item_video_ai_review" in route_source
    assert Path("web/services/media_item_video_ai_review.py").exists()


def test_media_object_access_validation_lives_outside_route_module():
    module_source = Path("web/routes/medias/media_upload.py").read_text(encoding="utf-8")
    module = ast.parse(module_source)
    route_sources = []
    for function_name in ("media_object_proxy", "public_media_object"):
        route_function = next(
            node
            for node in module.body
            if isinstance(node, ast.FunctionDef) and node.name == function_name
        )
        route_sources.append(ast.get_source_segment(module_source, route_function) or "")
        direct_storage_calls = [
            call.func.attr
            for call in ast.walk(route_function)
            if isinstance(call, ast.Call)
            and isinstance(call.func, ast.Attribute)
            and isinstance(call.func.value, ast.Name)
            and call.func.value.id == "local_media_storage"
        ]
        assert direct_storage_calls == []
    route_source = "\n".join(route_sources)

    assert "\"..\" in key.split(\"/\")" not in route_source
    assert "parts = key.split(\"/\")" not in route_source
    assert "_validate_private_media_object_access" in route_source
    assert "_validate_public_media_object_access" in route_source
    assert Path("web/services/media_object_access.py").exists()


def test_local_media_upload_response_lives_outside_route_module():
    module_source = Path("web/routes/medias/media_upload.py").read_text(encoding="utf-8")
    module = ast.parse(module_source)
    route_function = next(
        node
        for node in module.body
        if isinstance(node, ast.FunctionDef) and node.name == "api_local_media_upload"
    )
    route_source = ast.get_source_segment(module_source, route_function) or ""

    assert "_local_upload_reservations.get" not in route_source
    assert "reservation.get" not in route_source
    assert "local_media_storage.write_stream" not in route_source
    assert "complete_local_media_upload" in route_source
    assert Path("web/services/media_local_upload.py").exists()


def test_media_cover_bootstrap_responses_live_outside_route_module():
    module_source = Path("web/routes/medias/covers.py").read_text(encoding="utf-8")
    module = ast.parse(module_source)
    route_sources = []
    for function_name in ("api_item_cover_bootstrap", "api_cover_bootstrap"):
        route_function = next(
            node
            for node in module.body
            if isinstance(node, ast.FunctionDef) and node.name == function_name
        )
        route_sources.append(ast.get_source_segment(module_source, route_function) or "")
        direct_object_key_calls = [
            call.func.attr
            for call in ast.walk(route_function)
            if isinstance(call, ast.Call)
            and isinstance(call.func, ast.Attribute)
            and isinstance(call.func.value, ast.Name)
            and call.func.value.id == "object_keys"
        ]
        assert direct_object_key_calls == []
    route_source = "\n".join(route_sources)

    assert "filename required" not in route_source
    assert "storage_backend" not in route_source
    assert "_build_item_cover_bootstrap_response" in route_source
    assert "_build_product_cover_bootstrap_response" in route_source
    assert Path("web/services/media_covers.py").exists()


def test_media_item_cover_set_responses_live_outside_route_module():
    module_source = Path("web/routes/medias/covers.py").read_text(encoding="utf-8")
    module = ast.parse(module_source)
    route_sources = []
    for function_name in ("api_item_cover_update", "api_item_cover_set"):
        route_function = next(
            node
            for node in module.body
            if isinstance(node, ast.FunctionDef) and node.name == function_name
        )
        route_sources.append(ast.get_source_segment(module_source, route_function) or "")
        direct_item_cover_calls = [
            call.func.attr
            for call in ast.walk(route_function)
            if isinstance(call, ast.Call)
            and isinstance(call.func, ast.Attribute)
            and isinstance(call.func.value, ast.Name)
            and call.func.value.id == "medias"
            and call.func.attr == "update_item_cover"
        ]
        assert direct_item_cover_calls == []
    route_source = "\n".join(route_sources)

    assert "object_key required" not in route_source
    assert "object not found" not in route_source
    assert "_download_media_object" not in route_source
    assert "_build_item_cover_update_response" in route_source
    assert "_build_item_cover_set_response" in route_source
    assert Path("web/services/media_covers.py").exists()


def test_media_product_cover_complete_delete_responses_live_outside_route_module():
    module_source = Path("web/routes/medias/covers.py").read_text(encoding="utf-8")
    module = ast.parse(module_source)
    route_sources = []
    for function_name in ("api_cover_complete", "api_cover_delete"):
        route_function = next(
            node
            for node in module.body
            if isinstance(node, ast.FunctionDef) and node.name == function_name
        )
        route_sources.append(ast.get_source_segment(module_source, route_function) or "")
        direct_product_cover_calls = [
            call.func.attr
            for call in ast.walk(route_function)
            if isinstance(call, ast.Call)
            and isinstance(call.func, ast.Attribute)
            and isinstance(call.func.value, ast.Name)
            and call.func.value.id == "medias"
            and call.func.attr
            in {
                "get_product_covers",
                "set_product_cover",
                "delete_product_cover",
            }
        ]
        assert direct_product_cover_calls == []
    route_source = "\n".join(route_sources)

    assert "object_key required" not in route_source
    assert "object not found" not in route_source
    assert "涓嶆敮鎸佺殑璇" not in route_source
    assert "鑻辨枃涓诲浘涓嶈兘鍒犻櫎" not in route_source
    assert "_build_product_cover_complete_response" in route_source
    assert "_build_product_cover_delete_response" in route_source
    assert Path("web/services/media_covers.py").exists()


def test_media_cover_from_url_responses_live_outside_route_module():
    module_source = Path("web/routes/medias/covers.py").read_text(encoding="utf-8")
    module = ast.parse(module_source)
    route_sources = []
    for function_name in (
        "api_cover_from_url",
        "api_item_cover_from_url",
        "api_item_cover_set_from_url",
    ):
        route_function = next(
            node
            for node in module.body
            if isinstance(node, ast.FunctionDef) and node.name == function_name
        )
        route_sources.append(ast.get_source_segment(module_source, route_function) or "")
        direct_cover_calls = [
            call.func.attr
            for call in ast.walk(route_function)
            if isinstance(call, ast.Call)
            and isinstance(call.func, ast.Attribute)
            and isinstance(call.func.value, ast.Name)
            and call.func.value.id == "medias"
            and call.func.attr
            in {
                "get_product_covers",
                "set_product_cover",
                "update_item_cover",
            }
        ]
        assert direct_cover_calls == []
    route_source = "\n".join(route_sources)

    assert "_download_image_to_local_media" not in route_source
    assert "_delete_media_object" not in route_source
    assert "write_bytes" not in route_source
    assert "_build_product_cover_from_url_response" in route_source
    assert "_build_item_cover_from_url_response" in route_source
    assert "_build_item_cover_set_from_url_response" in route_source
    assert Path("web/services/media_covers.py").exists()


def test_media_push_error_responses_live_outside_route_module():
    module_source = Path("web/routes/medias/pushes.py").read_text(encoding="utf-8")
    module = ast.parse(module_source)
    route_sources = []
    for function_name in (
        "_product_links_push_error_response",
        "_product_localized_texts_push_error_response",
        "_product_unsuitable_push_error_response",
    ):
        route_function = next(
            node
            for node in module.body
            if isinstance(node, ast.FunctionDef) and node.name == function_name
        )
        route_sources.append(ast.get_source_segment(module_source, route_function) or "")
    route_source = "\n".join(route_sources)

    assert "ProductNotListedError" not in route_source
    assert "ProductLinksPushConfigError" not in route_source
    assert "ProductLocalizedTextsPushConfigError" not in route_source
    assert "ProductLinksPayloadError" not in route_source
    assert "ProductLocalizedTextsPayloadError" not in route_source
    assert "product_not_listed" not in route_source
    assert "product_unsuitable_push_failed" not in route_source
    assert "build_product_links_push_error_response" in route_source
    assert "build_product_localized_texts_push_error_response" in route_source
    assert "build_product_unsuitable_push_error_response" in route_source
    assert Path("web/services/media_pushes.py").exists()


def test_media_link_check_responses_live_outside_route_module():
    module_source = Path("web/routes/medias/link_check.py").read_text(encoding="utf-8")
    module = ast.parse(module_source)
    route_sources = []
    for function_name in (
        "api_product_link_check_create",
        "api_product_link_check_get",
        "api_product_link_check_detail",
    ):
        route_function = next(
            node
            for node in module.body
            if isinstance(node, ast.FunctionDef) and node.name == function_name
        )
        route_sources.append(ast.get_source_segment(module_source, route_function) or "")
    route_source = "\n".join(route_sources)

    assert "medias.is_valid_language" not in route_source
    assert "medias.get_language" not in route_source
    assert "medias.get_product_covers" not in route_source
    assert "medias.list_detail_images" not in route_source
    assert "medias.parse_link_check_tasks_json" not in route_source
    assert "medias.set_product_link_check_task" not in route_source
    assert "store.create_link_check" not in route_source
    assert "link_check_runner.start" not in route_source
    assert "uuid.uuid4" not in route_source
    assert "datetime.now" not in route_source
    assert "_collect_link_check_reference_images" not in route_source
    assert "unsupported language" not in route_source
    assert "task not found" not in route_source
    assert "build_product_link_check_create_response" in route_source
    assert "build_product_link_check_summary_response" in route_source
    assert "build_product_link_check_detail_response" in route_source
    assert Path("web/services/media_link_check.py").exists()


def test_media_shopify_image_responses_live_outside_route_module():
    module_source = Path("web/routes/medias/shopify_image.py").read_text(encoding="utf-8")
    module = ast.parse(module_source)
    route_sources = []
    for function_name in (
        "_shopify_image_lang_or_404",
        "api_product_shopify_image_confirm",
        "api_product_shopify_image_unavailable",
        "api_product_shopify_image_clear",
        "api_product_shopify_image_requeue",
    ):
        route_function = next(
            node
            for node in module.body
            if isinstance(node, ast.FunctionDef) and node.name == function_name
        )
        route_sources.append(ast.get_source_segment(module_source, route_function) or "")
    route_source = "\n".join(route_sources)

    assert "medias.is_valid_language" not in route_source
    assert "shopify_image_tasks." not in route_source
    assert "TASK_BLOCKED" not in route_source
    assert "mark_link_unavailable" not in route_source
    assert "create_or_reuse_task" not in route_source
    assert "normalize_shopify_image_lang" in route_source
    assert "build_shopify_image_confirm_response" in route_source
    assert "build_shopify_image_unavailable_response" in route_source
    assert "build_shopify_image_clear_response" in route_source
    assert "build_shopify_image_requeue_response" in route_source
    assert Path("web/services/media_shopify_image.py").exists()


def test_mk_copywriting_response_lives_outside_route_module():
    module_source = Path("web/routes/medias/products.py").read_text(encoding="utf-8")
    module = ast.parse(module_source)
    route_function = next(
        node
        for node in module.body
        if isinstance(node, ast.FunctionDef) and node.name == "api_mk_copywriting"
    )
    route_source = ast.get_source_segment(module_source, route_function) or ""

    assert "requests.get" not in route_source
    assert "_extract_mk_copywriting" not in route_source
    assert "_build_mk_request_headers" not in route_source
    assert "_is_mk_login_expired" not in route_source
    assert "mk_credentials_missing" not in route_source
    assert "mk_request_failed" not in route_source
    assert "_build_mk_copywriting_response" in route_source
    assert Path("web/services/media_mk_copywriting.py").exists()


def test_supply_pairing_search_response_lives_outside_route_module():
    module_source = Path("web/routes/medias/products.py").read_text(encoding="utf-8")
    module = ast.parse(module_source)
    route_function = next(
        node
        for node in module.body
        if isinstance(node, ast.FunctionDef) and node.name == "api_supply_pairing_search"
    )
    route_source = ast.get_source_segment(module_source, route_function) or ""

    assert "supply_pairing.search_supply_pairing" not in route_source
    assert "supply_pairing.extract_1688_url" not in route_source
    assert "missing_query" not in route_source
    assert "dxm_failed" not in route_source
    assert "extracted_1688_url" not in route_source
    assert "_build_supply_pairing_search_response" in route_source
    assert Path("web/services/media_supply_pairing.py").exists()


def test_xmyc_sku_response_building_lives_outside_route_module():
    module_source = Path("web/routes/medias/products.py").read_text(encoding="utf-8")
    module = ast.parse(module_source)
    route_sources = []
    for function_name in (
        "api_list_xmyc_skus",
        "api_get_product_xmyc_skus",
        "api_set_product_xmyc_skus",
        "api_update_xmyc_sku",
    ):
        route_function = next(
            node
            for node in module.body
            if isinstance(node, ast.FunctionDef) and node.name == function_name
        )
        route_sources.append(ast.get_source_segment(module_source, route_function) or "")
    route_source = "\n".join(route_sources)

    assert "xmyc_storage.list_skus" not in route_source
    assert "xmyc_storage.get_skus_for_product" not in route_source
    assert "xmyc_storage.set_product_skus" not in route_source
    assert "xmyc_storage.update_sku" not in route_source
    assert "sku_aggregates.enrich_skus_with_roas" not in route_source
    assert "product_roas.get_configured_rmb_per_usd" not in route_source
    assert "invalid_pagination" not in route_source
    assert "skus_must_be_list" not in route_source
    assert "invalid_fields" not in route_source
    assert "_build_xmyc_skus_list_response" in route_source
    assert "_build_product_xmyc_skus_response" in route_source
    assert "_build_product_xmyc_skus_set_response" in route_source
    assert "_build_xmyc_sku_update_response" in route_source
    assert Path("web/services/media_xmyc_skus.py").exists()


def test_parcel_cost_suggest_response_lives_outside_route_module():
    module_source = Path("web/routes/medias/products.py").read_text(encoding="utf-8")
    module = ast.parse(module_source)
    route_function = next(
        node
        for node in module.body
        if isinstance(node, ast.FunctionDef) and node.name == "api_parcel_cost_suggest"
    )
    route_source = ast.get_source_segment(module_source, route_function) or ""

    assert "parcel_cost_suggest.DEFAULT_LOOKBACK_DAYS" not in route_source
    assert "parcel_cost_suggest.suggest_parcel_cost" not in route_source
    assert "parcel_cost_suggest.ParcelCostSuggestError" not in route_source
    assert "invalid_days" not in route_source
    assert "no_orders" not in route_source
    assert "dxm_failed" not in route_source
    assert "_build_parcel_cost_suggest_response" in route_source
    assert Path("web/services/media_parcel_cost.py").exists()


def test_refresh_shopify_sku_response_lives_outside_route_module():
    module_source = Path("web/routes/medias/products.py").read_text(encoding="utf-8")
    module = ast.parse(module_source)
    route_function = next(
        node
        for node in module.body
        if isinstance(node, ast.FunctionDef) and node.name == "api_refresh_product_shopify_sku"
    )
    route_source = ast.get_source_segment(module_source, route_function) or ""

    assert "missing_shopifyid" not in route_source
    assert "fetch_shopify_and_dxm_via_cdp" not in route_source
    assert "build_pair_rows" not in route_source
    assert "shopify_product_not_found" not in route_source
    assert "medias.update_product" not in route_source
    assert "medias.replace_product_skus" not in route_source
    assert "medias.list_product_skus" not in route_source
    assert "medias.list_xmyc_unit_prices" not in route_source
    assert "_serialize_product_skus" not in route_source
    assert "_build_refresh_product_shopify_sku_response" in route_source
    assert Path("web/services/media_shopify_sku_refresh.py").exists()


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
    assert "SELECT id, task_dir, state_json FROM projects" not in route_source
    assert "UPDATE projects SET deleted_at=%s WHERE id=%s" not in route_source
    assert "cleanup_deleted_task_storage" not in route_source
    assert "store.update" not in route_source
    assert "delete_task_workflow" in route_source
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


def test_task_rename_workflow_lives_outside_route_module():
    module_source = Path("web/routes/task.py").read_text(encoding="utf-8")
    module = ast.parse(module_source)
    route_function = next(
        node
        for node in module.body
        if isinstance(node, ast.FunctionDef) and node.name == "rename_task"
    )
    route_source = ast.get_source_segment(module_source, route_function) or ""

    assert "SELECT id, user_id FROM projects WHERE id=%s AND user_id=%s AND deleted_at IS NULL" not in route_source
    assert "UPDATE projects SET display_name=%s WHERE id=%s" not in route_source
    assert "resolve_task_display_name_conflict" not in route_source
    assert "store.update" not in route_source
    assert "rename_task_display_name" in route_source
    assert Path("web/services/task_rename.py").exists()


def test_task_upload_initialization_lives_outside_route_module():
    module_source = Path("web/routes/task.py").read_text(encoding="utf-8")
    module = ast.parse(module_source)
    route_function = next(
        node
        for node in module.body
        if isinstance(node, ast.FunctionDef) and node.name == "upload"
    )
    route_source = ast.get_source_segment(module_source, route_function) or ""

    assert "store.create" not in route_source
    assert "store.update" not in route_source
    assert "resolve_task_display_name_conflict" not in route_source
    assert "UPDATE projects SET display_name=%s WHERE id=%s" not in route_source
    assert "av_step_maps" not in route_source
    assert "build_source_object_info" not in route_source
    assert "initialize_uploaded_av_task" in route_source
    assert Path("web/services/task_upload.py").exists()


def test_task_confirm_voice_workflow_lives_outside_route_module():
    module_source = Path("web/routes/task.py").read_text(encoding="utf-8")
    module = ast.parse(module_source)
    route_function = next(
        node
        for node in module.body
        if isinstance(node, ast.FunctionDef) and node.name == "confirm_voice"
    )
    route_source = ast.get_source_segment(module_source, route_function) or ""

    assert "normalize_confirm_voice_payload" not in route_source
    assert "resolve_default_voice" not in route_source
    assert "store.update" not in route_source
    assert "store.set_step" not in route_source
    assert "store.set_current_review_step" not in route_source
    assert "ensure_local_source_video" not in route_source
    assert "pipeline_runner.resume" not in route_source
    assert "confirm_task_voice" in route_source
    assert Path("web/services/task_voice.py").exists()


def test_task_voice_rematch_workflow_lives_outside_route_module():
    module_source = Path("web/routes/task.py").read_text(encoding="utf-8")
    module = ast.parse(module_source)
    route_function = next(
        node
        for node in module.body
        if isinstance(node, ast.FunctionDef) and node.name == "rematch_voice"
    )
    route_source = ast.get_source_segment(module_source, route_function) or ""

    assert "base64" not in route_source
    assert "deserialize_embedding" not in route_source
    assert "resolve_default_voice" not in route_source
    assert "match_candidates" not in route_source
    assert "fetch_voices_by_ids" not in route_source
    assert "store.update" not in route_source
    assert "rematch_task_voice" in route_source
    assert Path("web/services/task_voice_rematch.py").exists()


def test_task_start_workflow_lives_outside_route_module():
    module_source = Path("web/routes/task.py").read_text(encoding="utf-8")
    module = ast.parse(module_source)
    route_function = next(
        node
        for node in module.body
        if isinstance(node, ast.FunctionDef) and node.name == "start"
    )
    route_source = ast.get_source_segment(module_source, route_function) or ""

    assert "store.update" not in route_source
    assert "merge_av_step_maps" not in route_source
    assert "task_requires_source_sync" not in route_source
    assert "ensure_local_source_video" not in route_source
    assert "pipeline_runner.start" not in route_source
    assert "start_task_pipeline" in route_source
    assert Path("web/services/task_start.py").exists()


def test_task_restart_workflow_lives_outside_route_module():
    module_source = Path("web/routes/task.py").read_text(encoding="utf-8")
    module = ast.parse(module_source)
    route_function = next(
        node
        for node in module.body
        if isinstance(node, ast.FunctionDef) and node.name == "restart"
    )
    route_source = ast.get_source_segment(module_source, route_function) or ""

    assert "store.update" not in route_source
    assert "restart_task(" not in route_source
    assert "refresh_task" not in route_source
    assert "restart_task_workflow" in route_source
    assert Path("web/services/task_restart.py").exists()


def test_task_start_translate_workflow_lives_outside_route_module():
    module_source = Path("web/routes/task.py").read_text(encoding="utf-8")
    module = ast.parse(module_source)
    route_function = next(
        node
        for node in module.body
        if isinstance(node, ast.FunctionDef) and node.name == "start_translate"
    )
    route_source = ast.get_source_segment(module_source, route_function) or ""

    assert "_translate_pre_select" not in route_source
    assert "_VALID_TRANSLATE_PREFS" not in route_source
    assert "resolve_task_prompt_text" not in route_source
    assert "store.update" not in route_source
    assert "store.set_current_review_step" not in route_source
    assert "pipeline_runner.resume" not in route_source
    assert "start_task_translate" in route_source
    assert Path("web/services/task_translate.py").exists()


def test_task_retranslate_workflow_lives_outside_route_module():
    module_source = Path("web/routes/task.py").read_text(encoding="utf-8")
    module = ast.parse(module_source)
    route_function = next(
        node
        for node in module.body
        if isinstance(node, ast.FunctionDef) and node.name == "retranslate"
    )
    route_source = ast.get_source_segment(module_source, route_function) or ""

    assert "generate_localized_translation" not in route_source
    assert "get_model_display_name" not in route_source
    assert "build_source_full_text_zh" not in route_source
    assert "ai_billing.log_request" not in route_source
    assert "_llm_request_payload" not in route_source
    assert "_llm_response_payload" not in route_source
    assert "resolve_translate_billing_provider" not in route_source
    assert "translation_history" not in route_source
    assert "store.update" not in route_source
    assert "retranslate_task" in route_source
    assert Path("web/services/task_retranslate.py").exists()


def test_task_select_translation_workflow_lives_outside_route_module():
    module_source = Path("web/routes/task.py").read_text(encoding="utf-8")
    module = ast.parse(module_source)
    route_function = next(
        node
        for node in module.body
        if isinstance(node, ast.FunctionDef) and node.name == "select_translation"
    )
    route_source = ast.get_source_segment(module_source, route_function) or ""

    assert "translation_history" not in route_source
    assert "store.update_variant" not in route_source
    assert "store.update" not in route_source
    assert "select_task_translation" in route_source
    assert Path("web/services/task_translation_selection.py").exists()


def test_task_alignment_workflow_lives_outside_route_module():
    module_source = Path("web/routes/task.py").read_text(encoding="utf-8")
    module = ast.parse(module_source)
    route_function = next(
        node
        for node in module.body
        if isinstance(node, ast.FunctionDef) and node.name == "update_alignment"
    )
    route_source = ast.get_source_segment(module_source, route_function) or ""

    assert "build_script_segments" not in route_source
    assert "build_alignment_artifact" not in route_source
    assert "store.confirm_alignment" not in route_source
    assert "store.set_artifact" not in route_source
    assert "store.set_current_review_step" not in route_source
    assert "store.set_step" not in route_source
    assert "store.update" not in route_source
    assert "pipeline_runner.resume" not in route_source
    assert "confirm_task_alignment" in route_source
    assert Path("web/services/task_alignment.py").exists()


def test_task_segments_workflow_lives_outside_route_module():
    module_source = Path("web/routes/task.py").read_text(encoding="utf-8")
    module = ast.parse(module_source)
    route_function = next(
        node
        for node in module.body
        if isinstance(node, ast.FunctionDef) and node.name == "update_segments"
    )
    route_source = ast.get_source_segment(module_source, route_function) or ""

    assert "store.confirm_segments" not in route_source
    assert "refresh_task" not in route_source
    assert "_build_av_localized_translation" not in route_source
    assert "store.update_variant" not in route_source
    assert "store.update" not in route_source
    assert "store.set_artifact" not in route_source
    assert "store.set_current_review_step" not in route_source
    assert "store.set_step" not in route_source
    assert "store.set_step_message" not in route_source
    assert "pipeline_runner.resume" not in route_source
    assert "confirm_task_segments" in route_source
    assert Path("web/services/task_segments.py").exists()


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


def test_task_av_rewrite_sentence_workflow_lives_outside_route_module():
    module_source = Path("web/routes/task.py").read_text(encoding="utf-8")
    module = ast.parse(module_source)
    route_function = next(
        node
        for node in module.body
        if isinstance(node, ast.FunctionDef) and node.name == "av_rewrite_sentence"
    )
    route_source = ast.get_source_segment(module_source, route_function) or ""

    assert "tts.generate_segment_audio" not in route_source
    assert "tts.get_audio_duration" not in route_source
    assert "classify_overshoot" not in route_source
    assert "compute_speed_for_target" not in route_source
    assert "duration_ratio" not in route_source
    assert "rebuild_tts_full_audio" not in route_source
    assert "build_subtitle_units_from_sentences" not in route_source
    assert "build_srt_from_chunks" not in route_source
    assert "save_srt" not in route_source
    assert "clear_av_compose_outputs" not in route_source
    assert "store.update" not in route_source
    assert "rewrite_task_av_sentence" in route_source
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


def test_task_video_ai_review_workflow_lives_outside_route_module():
    module_source = Path("web/routes/task.py").read_text(encoding="utf-8")
    module = ast.parse(module_source)
    run_route = next(
        node
        for node in module.body
        if isinstance(node, ast.FunctionDef) and node.name == "run_video_ai_review"
    )
    get_route = next(
        node
        for node in module.body
        if isinstance(node, ast.FunctionDef) and node.name == "get_video_ai_review"
    )
    route_source = "\n".join(
        ast.get_source_segment(module_source, node) or ""
        for node in (run_route, get_route)
    )

    assert "def _can_view_av_task" not in module_source
    assert "video_ai_review.trigger_review" not in route_source
    assert "video_ai_review.latest_review" not in route_source
    assert "ReviewInProgressError" not in route_source
    assert "task_state.get" not in route_source
    assert Path("web/services/task_video_ai_review.py").exists()


def test_openapi_materials_serializers_live_outside_route_module():
    source = Path("web/routes/openapi_materials.py").read_text(encoding="utf-8")

    assert "def _iso_or_none" not in source
    assert "def _number_or_none" not in source
    assert "def _serialize_product" not in source
    assert "def _serialize_cover_map" not in source
    assert "def _group_copywritings" not in source
    assert "def _serialize_shopify_image_task" not in source
    assert "def _serialize_items" not in source
    assert "def _normalize_target_url" not in source
    assert Path("web/services/openapi_materials_serializers.py").exists()


def test_openapi_materials_listing_helpers_live_outside_route_module():
    source = Path("web/routes/openapi_materials.py").read_text(encoding="utf-8")

    assert "def _parse_archived_filter" not in source
    assert "def _batch_cover_langs" not in source
    assert "def _batch_copywriting_langs" not in source
    assert "def _batch_item_lang_counts" not in source
    assert Path("web/services/openapi_materials_listing.py").exists()


def test_openapi_materials_list_response_lives_outside_route_module():
    module_source = Path("web/routes/openapi_materials.py").read_text(encoding="utf-8")
    module = ast.parse(module_source)
    route_function = next(
        node
        for node in module.body
        if isinstance(node, ast.FunctionDef) and node.name == "list_materials"
    )
    route_source = ast.get_source_segment(module_source, route_function) or ""

    assert "SELECT COUNT(*) AS c FROM media_products" not in route_source
    assert "FROM media_products WHERE" not in route_source
    assert "_batch_cover_langs" not in route_source
    assert "_batch_copywriting_langs" not in route_source
    assert "_batch_item_lang_counts" not in route_source
    assert "items.append" not in route_source
    assert "_build_materials_list_response" in route_source
    assert Path("web/services/openapi_materials_listing.py").exists()


def test_openapi_material_detail_response_lives_outside_route_module():
    module_source = Path("web/routes/openapi_materials.py").read_text(encoding="utf-8")
    module = ast.parse(module_source)
    route_function = next(
        node
        for node in module.body
        if isinstance(node, ast.FunctionDef) and node.name == "get_material"
    )
    route_source = ast.get_source_segment(module_source, route_function) or ""

    assert "medias.get_product_covers" not in route_source
    assert "medias.list_copywritings" not in route_source
    assert "medias.list_items" not in route_source
    assert "_serialize_product" not in route_source
    assert "_serialize_cover_map" not in route_source
    assert "_group_copywritings" not in route_source
    assert "_serialize_items" not in route_source
    assert '"storage_backend": "local"' not in route_source
    assert Path("web/services/openapi_materials_serializers.py").exists()


def test_openapi_push_item_serialization_lives_outside_route_module():
    source = Path("web/routes/openapi_materials.py").read_text(encoding="utf-8")

    assert "def _serialize_push_item" not in source
    assert "FROM media_push_logs WHERE id=%s" not in source
    assert Path("web/services/openapi_push_items.py").exists()


def test_openapi_push_items_list_projection_lives_outside_route_module():
    module_source = Path("web/routes/openapi_materials.py").read_text(encoding="utf-8")
    module = ast.parse(module_source)
    route_function = next(
        node
        for node in module.body
        if isinstance(node, ast.FunctionDef) and node.name == "list_push_items"
    )
    route_source = ast.get_source_segment(module_source, route_function) or ""

    assert "product_shape =" not in route_source
    assert "all_items: list[dict]" not in route_source
    assert 'it["status"] in status_filter' not in route_source
    assert "all_items[start:end]" not in route_source
    assert Path("web/services/openapi_push_items.py").exists()


def test_openapi_push_item_payload_response_lives_outside_route_module():
    module_source = Path("web/routes/openapi_materials.py").read_text(encoding="utf-8")
    module = ast.parse(module_source)
    route_function = next(
        node
        for node in module.body
        if isinstance(node, ast.FunctionDef) and node.name == "get_push_item_payload_by_keys"
    )
    route_source = ast.get_source_segment(module_source, route_function) or ""

    assert "pushes.build_item_payload" not in route_source
    assert "pushes.resolve_localized_text_payload" not in route_source
    assert "pushes.build_localized_texts_request" not in route_source
    assert '"localized_texts_request": localized_texts_request' not in route_source
    assert Path("web/services/openapi_push_items.py").exists()


def test_openapi_push_item_writeback_lives_outside_route_module():
    module_source = Path("web/routes/openapi_materials.py").read_text(encoding="utf-8")
    module = ast.parse(module_source)
    route_sources = []
    for function_name in ("mark_pushed", "mark_failed"):
        route_function = next(
            node
            for node in module.body
            if isinstance(node, ast.FunctionDef) and node.name == function_name
        )
        route_sources.append(ast.get_source_segment(module_source, route_function) or "")
    route_source = "\n".join(route_sources)

    assert "pushes.record_push_success" not in route_source
    assert "pushes.record_push_failure" not in route_source
    assert "request_payload" not in route_source
    assert "response_body" not in route_source
    assert "error_message" not in route_source
    assert Path("web/services/openapi_push_items.py").exists()


def test_openapi_material_push_payload_lives_outside_route_module():
    module_source = Path("web/routes/openapi_materials.py").read_text(encoding="utf-8")
    module = ast.parse(module_source)
    route_function = next(
        node
        for node in module.body
        if isinstance(node, ast.FunctionDef) and node.name == "build_push_payload"
    )
    route_source = ast.get_source_segment(module_source, route_function) or ""

    assert "medias.is_product_listed" not in route_source
    assert "medias.list_items" not in route_source
    assert "pushes.resolve_push_texts" not in route_source
    assert "product_links =" not in route_source
    assert "videos = []" not in route_source
    assert '"platforms": ["tiktok"]' not in route_source
    assert Path("web/services/openapi_push_items.py").exists()


def test_openapi_link_check_bootstrap_lives_outside_route_module():
    module_source = Path("web/routes/openapi_materials.py").read_text(encoding="utf-8")
    module = ast.parse(module_source)
    route_function = next(
        node
        for node in module.body
        if isinstance(node, ast.FunctionDef) and node.name == "bootstrap_link_check"
    )
    route_source = ast.get_source_segment(module_source, route_function) or ""

    assert "medias.list_languages" not in route_source
    assert "medias.find_product_for_link_check_url" not in route_source
    assert "medias.list_reference_images_for_lang" not in route_source
    assert "medias.get_language_name" not in route_source
    assert "reference_images.append" not in route_source
    assert "_normalize_target_url" not in route_source
    assert "_media_download_url" not in route_source
    assert "language not detected" not in route_source
    assert "references not ready" not in route_source
    assert "_build_link_check_bootstrap_response" in route_source
    assert Path("web/services/openapi_link_check.py").exists()


def test_openapi_shopify_localizer_bootstrap_lives_outside_route_module():
    module_source = Path("web/routes/openapi_materials.py").read_text(encoding="utf-8")
    module = ast.parse(module_source)
    route_function = next(
        node
        for node in module.body
        if isinstance(node, ast.FunctionDef) and node.name == "shopify_localizer_bootstrap"
    )
    route_source = ast.get_source_segment(module_source, route_function) or ""

    assert "reference_images = [" not in route_source
    assert "localized_images = [" not in route_source
    assert "def _serialize" not in route_source
    assert "invalid_target_lang" not in route_source
    assert "shopify_product_id_missing" not in route_source
    assert "english references not ready" not in route_source
    assert "localized images not ready" not in route_source
    assert "_build_shopify_localizer_bootstrap_response" in route_source
    assert Path("web/services/openapi_shopify_localizer.py").exists()


def test_openapi_shopify_localizer_task_routes_live_outside_route_module():
    module_source = Path("web/routes/openapi_materials.py").read_text(encoding="utf-8")
    module = ast.parse(module_source)
    route_sources = []
    for function_name in (
        "shopify_localizer_task_claim",
        "shopify_localizer_task_heartbeat",
        "shopify_localizer_task_complete",
        "shopify_localizer_task_fail",
    ):
        route_function = next(
            node
            for node in module.body
            if isinstance(node, ast.FunctionDef) and node.name == function_name
        )
        route_sources.append(ast.get_source_segment(module_source, route_function) or "")
    route_source = "\n".join(route_sources)

    assert "shopify_image_tasks." not in route_source
    assert "lock_seconds" not in route_source
    assert "worker_id" not in route_source
    assert "error_code" not in route_source
    assert "_serialize_shopify_image_task" not in route_source
    assert "_build_shopify_localizer_task_claim_response" in route_source
    assert "_build_shopify_localizer_task_heartbeat_response" in route_source
    assert "_build_shopify_localizer_task_complete_response" in route_source
    assert "_build_shopify_localizer_task_fail_response" in route_source
    assert Path("web/services/openapi_shopify_localizer.py").exists()


def test_task_resume_workflow_lives_outside_route_module():
    module_source = Path("web/routes/task.py").read_text(encoding="utf-8")
    module = ast.parse(module_source)
    route_function = next(
        node
        for node in module.body
        if isinstance(node, ast.FunctionDef) and node.name == "resume_from_step"
    )
    route_source = ast.get_source_segment(module_source, route_function) or ""

    assert "recover_task_if_needed" not in route_source
    assert "SELECT id FROM projects WHERE id=%s AND user_id=%s AND deleted_at IS NULL" not in route_source
    assert "store.set_step" not in route_source
    assert "ensure_local_source_video" not in route_source
    assert "pipeline_runner.resume" not in route_source
    assert Path("web/services/task_resume.py").exists()


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
    """SDK 直连白名单：openai / google.genai 的 import 只允许出现在
    adapter 或 _helpers 薄封装里。

    Phase D 完成后状态：
      - 业务代码（pipeline / web / tools / appcore 业务模块）100% 走
        appcore.llm_client.invoke_chat / invoke_generate
      - appcore/gemini.py 已删除（D-3）
      - pipeline/translate.py 不再 from openai import OpenAI（D-4）
      - pipeline/video_csk / video_review / video_score / shot_decompose /
        translate_v2 / tts_v2 通过 invoke_generate 调用（B-3）
      - 流式 generate_stream 已随 gemini.py 一并删除（D-2 合并入 D-3）
    """
    allowed_paths = {
        # adapter（实现 LLMAdapter.chat / generate，唯一允许直连 SDK 的层）
        "appcore/llm_providers/openrouter_adapter.py",
        "appcore/llm_providers/gemini_aistudio_adapter.py",
        "appcore/llm_providers/gemini_vertex_adapter.py",
        # adapter 间共享 helper（不暴露给业务代码）
        "appcore/llm_providers/_helpers/openai_compat.py",
        "appcore/llm_providers/_helpers/openrouter_image.py",
        "appcore/llm_providers/_helpers/gemini_calls.py",
        "appcore/llm_providers/_helpers/vertex_json.py",
    }
    offenders: list[str] = []

    def _is_sdk_module(name: str) -> bool:
        return name == "openai" or name == "google" or name == "google.genai" or name.startswith("google.genai.")

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
                    imports_genai = (
                        module == "google" and any(alias.name == "genai" for alias in node.names)
                        or module == "google.genai"
                        or module.startswith("google.genai.")
                    )
                    imports_legacy_gemini = (
                        module == "appcore"
                        and any(alias.name == "gemini" for alias in node.names)
                    )
                    if (imports_openai or imports_genai or imports_legacy_gemini) and path_key not in allowed_paths:
                        offenders.append(f"{path}:{node.lineno}")
                elif isinstance(node, ast.Import):
                    for alias in node.names:
                        if (
                            alias.name in {"openai", "appcore.gemini"}
                            or _is_sdk_module(alias.name)
                        ) and path_key not in allowed_paths:
                            offenders.append(f"{path}:{node.lineno}")

    assert offenders == [], f"unauthorised SDK direct-import: {offenders}"
