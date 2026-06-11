"""通用视频翻译路由工厂：用于合并完全克隆的德法等语种翻译路由。"""
from __future__ import annotations

import json
import logging
import os
import uuid
import importlib
from datetime import datetime

from flask import Blueprint, render_template, request, send_file, abort
from flask_login import login_required, current_user

from web.services.translate_route_responses import (
    build_translate_route_payload_response,
    translate_route_flask_response,
)

log = logging.getLogger(__name__)

LANG_NAMES = {
    "de": "德语",
    "fr": "法语",
}


def _json_response(payload: dict, status_code: int = 200):
    return translate_route_flask_response(
        build_translate_route_payload_response(payload, status_code)
    )


def _default_display_name(original_filename: str) -> str:
    name = os.path.splitext(original_filename)[0] if original_filename else ""
    return name[:10] or "未命名"


def _resolve_name_conflict(module, user_id: int, desired_name: str) -> str:
    base = desired_name
    candidate = base
    n = 2
    while True:
        row = module.translation_route_store.find_project_by_display_name(
            user_id,
            candidate,
            query_one_func=module.db_query_one,
        )
        if not row:
            return candidate
        candidate = f"{base} ({n})"
        n += 1


def create_lang_translate_bp(
    lang_code: str,
    blueprint_name: str,
    url_prefix: str,
    template_prefix: str,
    pipeline_runner_module: str,
    module,
) -> Blueprint:
    bp = Blueprint(blueprint_name, __name__)
    lang_name = LANG_NAMES.get(lang_code, lang_code)

    # dynamically import the runner module
    runner = importlib.import_module(f"web.services.{pipeline_runner_module}")

    # ── 页面路由 ──────────────────────────────────────────

    @bp.route(url_prefix)
    @login_required
    def index():
        module.recover_all_interrupted_tasks()
        rows = module.translation_route_store.list_user_projects(
            current_user.id,
            blueprint_name,
            query_func=module.db_query,
        )
        from appcore.settings import get_retention_hours
        return render_template(
            f"{template_prefix}_list.html",
            projects=rows,
            now=datetime.now(),
            retention_hours=get_retention_hours(blueprint_name)
        )

    @bp.route(f"{url_prefix}/<task_id>")
    @login_required
    def detail(task_id: str):
        module.recover_project_if_needed(task_id, blueprint_name)
        row = module.translation_route_store.get_user_project(
            task_id,
            current_user.id,
            blueprint_name,
            query_one_func=module.db_query_one,
        )
        if not row:
            abort(404)
        state = {}
        if row.get("state_json"):
            try:
                state = json.loads(row["state_json"])
            except Exception:
                pass
        from appcore.api_keys import get_key
        translate_pref = get_key(current_user.id, "translate_pref") or "openrouter"
        return render_template(
            f"{template_prefix}_detail.html",
            project=row,
            state=state,
            translate_pref=translate_pref,
        )

    # ── API 路由 ──────────────────────────────────────────

    @bp.route(f"/api{url_prefix}/start", methods=["POST"])
    @login_required
    def upload_and_start():
        if "video" not in request.files:
            return _json_response({"error": "No video file"}, 400)
        file = request.files["video"]
        if not file.filename:
            return _json_response({"error": "Empty filename"}, 400)

        from web.upload_util import build_source_object_info, client_filename_basename, save_uploaded_video, validate_video_extension

        original_filename = client_filename_basename(file.filename)
        if not validate_video_extension(original_filename):
            return _json_response({"error": "不支持的视频格式"}, 400)

        task_id = str(uuid.uuid4())
        task_dir = os.path.join(module.OUTPUT_DIR, task_id)
        os.makedirs(task_dir, exist_ok=True)

        video_path, file_size, content_type = save_uploaded_video(file, module.UPLOAD_DIR, task_id, original_filename)
        user_id = current_user.id

        module.store.create(
            task_id,
            video_path,
            task_dir,
            original_filename=original_filename,
            user_id=user_id,
        )

        display_name = _resolve_name_conflict(module, user_id, _default_display_name(original_filename))
        module.store.update(
            task_id,
            display_name=display_name,
            type=blueprint_name,
            source_language="en",
            user_specified_source_language=True,
            source_tos_key="",
            source_object_info=build_source_object_info(
                original_filename=original_filename,
                content_type=content_type,
                file_size=file_size,
                storage_backend="local",
                uploaded_at=datetime.now().isoformat(timespec="seconds"),
            ),
            delivery_mode="local_primary",
        )
        return _json_response({"task_id": task_id}, 201)

    @bp.route(f"/api{url_prefix}/bootstrap", methods=["POST"])
    @login_required
    def bootstrap_upload():
        return _json_response({"error": f"新建{lang_name}翻译任务已切换为本地上传，请改用 multipart /api{url_prefix}/start"}, 410)

    @bp.route(f"/api{url_prefix}/complete", methods=["POST"])
    @login_required
    def complete_upload():
        return _json_response({"error": f"新建{lang_name}翻译任务已切换为本地上传，TOS complete 创建任务入口已停用"}, 410)

    @bp.route(f"/api{url_prefix}/<task_id>", methods=["GET"])
    @login_required
    def get_task(task_id):
        module.recover_task_if_needed(task_id)
        task = module.store.get(task_id)
        if not task or task.get("_user_id") != current_user.id:
            return _json_response({"error": "Task not found"}, 404)
        return _json_response(task)

    @bp.route(f"/api{url_prefix}/<task_id>/restart", methods=["POST"])
    @login_required
    def restart(task_id):
        module.recover_task_if_needed(task_id)
        task = module.store.get(task_id)
        if not task or task.get("_user_id") != current_user.id:
            return _json_response({"error": "Task not found"}, 404)

        body = request.get_json(silent=True) or {}
        from web.services.task_restart import restart_task
        updated = restart_task(
            task_id,
            voice_id=None if body.get("voice_id") in (None, "", "auto") else body.get("voice_id"),
            voice_gender=body.get("voice_gender", "male"),
            subtitle_font=body.get("subtitle_font", "Impact"),
            subtitle_size=body.get("subtitle_size", 14),
            subtitle_position_y=float(body.get("subtitle_position_y", 0.68)),
            subtitle_position=body.get("subtitle_position", "bottom"),
            interactive_review=body.get("interactive_review", "false") in ("true", True, "1"),
            user_id=current_user.id,
            runner=runner,
        )
        return _json_response({"status": "restarted", "task": updated})

    @bp.route(f"/api{url_prefix}/<task_id>/start", methods=["POST"])
    @login_required
    def start_task(task_id):
        module.recover_task_if_needed(task_id)
        task = module.store.get(task_id)
        if not task or task.get("_user_id") != current_user.id:
            return _json_response({"error": "Task not found"}, 404)

        body = request.get_json(silent=True) or {}
        module.store.update(
            task_id,
            voice_gender=body.get("voice_gender", "male"),
            voice_id=None if body.get("voice_id") in (None, "", "auto") else body.get("voice_id"),
            subtitle_position=body.get("subtitle_position", "bottom"),
            subtitle_font=body.get("subtitle_font", "Impact"),
            subtitle_size=body.get("subtitle_size", 14),
            subtitle_position_y=float(body.get("subtitle_position_y", 0.68)),
            interactive_review=body.get("interactive_review", "false") in ("true", True, "1"),
        )

        runner.start(task_id, user_id=current_user.id)
        updated_task = module.store.get(task_id) or task
        return _json_response({"status": "started", "task": updated_task})

    @bp.route(f"/api{url_prefix}/<task_id>/source-language", methods=["PUT"])
    @login_required
    def update_source_language(task_id):
        task = module.store.get(task_id)
        if not task or task.get("_user_id") != current_user.id:
            return _json_response({"error": "Task not found"}, 404)
        body = request.get_json(silent=True) or {}
        lang = body.get("source_language")
        if lang not in ("zh", "en"):
            return _json_response({"error": "source_language must be 'zh' or 'en'"}, 400)
        module.store.update(task_id, source_language=lang, user_specified_source_language=True)
        return _json_response({"status": "ok"})

    @bp.route(f"/api{url_prefix}/<task_id>/alignment", methods=["PUT"])
    @login_required
    def update_alignment(task_id):
        task = module.store.get(task_id)
        if not task or task.get("_user_id") != current_user.id:
            return _json_response({"error": "Task not found"}, 404)

        body = request.get_json(silent=True) or {}
        break_after = body.get("break_after")
        if not isinstance(break_after, list):
            return _json_response({"error": "break_after required"}, 400)

        source_language = body.get("source_language")
        if source_language in ("zh", "en"):
            module.store.update(task_id, source_language=source_language, user_specified_source_language=True)

        from web.preview_artifacts import build_alignment_artifact
        script_segments = module.build_script_segments(task.get("utterances", []), break_after)
        module.store.confirm_alignment(task_id, break_after, script_segments)
        module.store.set_artifact(
            task_id, "alignment",
            build_alignment_artifact(task.get("scene_cuts", []), script_segments, break_after),
        )
        module.store.set_current_review_step(task_id, "")
        module.store.set_step(task_id, "alignment", "done")
        module.store.set_step_message(task_id, "alignment", "分段确认完成")

        if task.get("interactive_review"):
            module.store.set_current_review_step(task_id, "translate")
            module.store.set_step(task_id, "translate", "waiting")
            module.store.set_step_message(task_id, "translate", "请选择翻译模型和提示词")
            module.store.update(task_id, _translate_pre_select=True)
        else:
            runner.resume(task_id, "translate", user_id=current_user.id)
        return _json_response({"status": "ok", "script_segments": script_segments})

    @bp.route(f"/api{url_prefix}/<task_id>/segments", methods=["PUT"])
    @login_required
    def update_segments(task_id):
        task = module.store.get(task_id)
        if not task or task.get("_user_id") != current_user.id:
            return _json_response({"error": "Task not found"}, 404)

        body = request.get_json(silent=True) or {}
        segments = body.get("segments")
        if segments:
            variant = "normal"
            variants = dict(task.get("variants", {}))
            variant_state = dict(variants.get(variant, {}))
            localized_translation = dict(variant_state.get("localized_translation", {}))
            localized_translation["sentences"] = [
                {"index": seg.get("index", i), "text": seg.get("translated", ""),
                 "source_segment_indices": seg.get("source_segment_indices", [i])}
                for i, seg in enumerate(segments)
            ]
            localized_translation["full_text"] = " ".join(
                s["text"] for s in localized_translation["sentences"]
            )
            variant_state["localized_translation"] = localized_translation
            variants[variant] = variant_state
            from datetime import datetime, timezone
            module.store.update(task_id, variants=variants, localized_translation=localized_translation,
                         _segments_confirmed=True,
                         evals_invalidated_at=datetime.now(timezone.utc).isoformat())

        module.store.set_current_review_step(task_id, "")
        runner.resume(task_id, "tts", user_id=current_user.id)
        return _json_response({"status": "ok"})

    @bp.route(f"/api{url_prefix}/<task_id>/export", methods=["POST"])
    @login_required
    def export_task(task_id):
        task = module.store.get(task_id)
        if not task or task.get("_user_id") != current_user.id:
            return _json_response({"error": "Task not found"}, 404)
        runner.resume(task_id, "compose", user_id=current_user.id)
        return _json_response({"status": "started"})

    RESUMABLE_STEPS = ["extract", "asr", "alignment", "translate", "tts", "subtitle", "compose", "export"]

    @bp.route(f"/api{url_prefix}/<task_id>/resume", methods=["POST"])
    @login_required
    def resume_task(task_id):
        module.recover_task_if_needed(task_id)
        task = module.store.get(task_id)
        if not task or task.get("_user_id") != current_user.id:
            return _json_response({"error": "Task not found"}, 404)
        body = request.get_json(silent=True) or {}
        start_step = body.get("start_step", "")
        if start_step not in RESUMABLE_STEPS:
            return _json_response({"error": f"start_step must be one of {RESUMABLE_STEPS}"}, 400)

        started = False
        for s in RESUMABLE_STEPS:
            if s == start_step:
                started = True
            if started:
                module.store.set_step(task_id, s, "pending")
                module.store.set_step_message(task_id, s, "等待中...")

        module.store.update(task_id, status="running", current_review_step="")
        runner.resume(task_id, start_step, user_id=current_user.id)
        return _json_response({"status": "started", "start_step": start_step})

    @bp.route(f"/api{url_prefix}/<task_id>/download/<file_type>")
    @login_required
    def download(task_id, file_type):
        task = module.store.get(task_id)
        if not task or task.get("_user_id") != current_user.id:
            return _json_response({"error": "Task not found"}, 404)

        variant = request.args.get("variant", "normal")
        return module.serve_artifact_download(task, task_id, file_type, variant=variant)

    @bp.route(f"/api{url_prefix}/<task_id>", methods=["DELETE"])
    @login_required
    def delete(task_id):
        row = module.translation_route_store.get_active_project_storage(
            task_id,
            current_user.id,
            blueprint_name,
            query_one_func=module.db_query_one,
        )
        if not row:
            return _json_response({"error": "Task not found"}, 404)

        task = module.store.get(task_id) or {}
        from appcore import cleanup
        cleanup_payload = dict(task)
        cleanup_payload["task_dir"] = row.get("task_dir") or cleanup_payload.get("task_dir", "")
        cleanup_payload["state_json"] = row.get("state_json") or ""
        cleanup_payload["tos_keys"] = cleanup.collect_task_tos_keys(cleanup_payload)
        try:
            cleanup.delete_task_storage(cleanup_payload)
        except Exception:
            pass

        module.translation_route_store.soft_delete_project(
            task_id,
            current_user.id,
            blueprint_name,
            execute_func=module.db_execute,
        )
        module.store.update(task_id, status="deleted")
        return _json_response({"status": "ok"})

    @bp.route(f"/api{url_prefix}/<task_id>/artifact/<name>")
    @login_required
    def get_artifact(task_id, name):
        task = module.store.get(task_id)
        if not task or task.get("_user_id") != current_user.id:
            return _json_response({"error": "Task not found"}, 404)

        variant = request.args.get("variant") or None

        from web.services.artifact_download import (
            preview_artifact_tos_redirect,
            safe_task_file_response,
        )
        tos_resp = preview_artifact_tos_redirect(task, name, variant=variant)
        if tos_resp is not None:
            return tos_resp

        preview_files = task.get("preview_files") or {}
        if variant:
            preview_files = (task.get("variants") or {}).get(variant, {}).get("preview_files", {})

        path = preview_files.get(name)
        if path:
            return safe_task_file_response(task, path)
        return _json_response({"error": "Artifact not found"}, 404)

    @bp.route(f"/api{url_prefix}/<task_id>/artifact-path")
    @login_required
    def get_artifact_path(task_id: str):
        task = module.store.get(task_id)
        if not task or task.get("_user_id") != current_user.id:
            return _json_response({"error": "Task not found"}, 404)

        from web.services.artifact_download import safe_task_relative_file_response

        return safe_task_relative_file_response(task, request.args.get("path"))

    _ALLOWED_ROUND_KINDS = {
        "localized_translation":        ("localized_translation.round_{r}.json",       "application/json"),
        "localized_rewrite_messages":   ("localized_rewrite_messages.round_{r}.json",  "application/json"),
        "initial_translate_messages":   ("localized_translate_messages.json",          "application/json"),
        "tts_script":                   ("tts_script.round_{r}.json",                  "application/json"),
        "tts_full_audio":               ("tts_full.round_{r}.mp3",                     "audio/mpeg"),
    }

    @bp.route(f"/api{url_prefix}/<task_id>/round-file/<int:round_index>/<kind>")
    @login_required
    def get_round_file(task_id: str, round_index: int, kind: str):
        if round_index not in (1, 2, 3, 4, 5):
            abort(404)
        if kind not in _ALLOWED_ROUND_KINDS:
            abort(404)

        task = module.store.get(task_id)
        if not task or task.get("_user_id") != current_user.id:
            return _json_response({"error": "Task not found"}, 404)

        filename_pattern, mime = _ALLOWED_ROUND_KINDS[kind]
        filename = filename_pattern.format(r=round_index)
        path = os.path.join(task.get("task_dir", ""), filename)
        from web.services.artifact_download import safe_task_file_response
        return safe_task_file_response(
            task,
            path,
            not_found_message="File not ready",
            mimetype=mime,
            as_attachment=False,
            download_name=filename,
            conditional=False,
        )

    @bp.route(f"/api{url_prefix}/<task_id>/analysis/run", methods=["POST"])
    @login_required
    def run_ai_analysis(task_id):
        row = module.translation_route_store.get_active_project_id(
            task_id,
            current_user.id,
            blueprint_name,
            query_one_func=module.db_query_one,
        )
        if not row:
            return _json_response({"error": "Task not found"}, 404)

        task = module.store.get(task_id)
        if not task:
            return _json_response({"error": "Task not found"}, 404)

        if (task.get("steps") or {}).get("analysis") == "running":
            return _json_response({"error": "AI 分析正在运行中"}, 409)

        if not runner.run_analysis(task_id, user_id=current_user.id):
            return _json_response({"error": "AI 分析正在运行中"}, 409)
        return _json_response({"status": "started"})

    return bp
