from __future__ import annotations
import json
from flask import Blueprint, render_template, abort, redirect, url_for, request
from flask_login import login_required, current_user
from appcore.av_translate_inputs import (
    AV_TARGET_MARKET_OPTIONS,
    build_available_av_translate_inputs,
    list_available_av_target_language_options,
)
from appcore import project_state as project_store
from appcore.permissions import HOME_REDIRECT_ORDER
from appcore.task_recovery import recover_all_interrupted_tasks, recover_project_if_needed
from appcore.settings import get_retention_hours
from web.auth import permission_required

bp = Blueprint("projects", __name__)


def _is_av_sync_state(state: dict) -> bool:
    return (
        state.get("pipeline_version") == "av"
        or state.get("type") == "av_translate"
        or bool(state.get("av_translate_inputs"))
    )


def _av_sync_target_lang(state: dict) -> str:
    av_inputs = state.get("av_translate_inputs") if isinstance(state.get("av_translate_inputs"), dict) else {}
    return str(av_inputs.get("target_language") or state.get("target_lang") or "en").strip().lower() or "en"


@bp.route("/")
@login_required
def root():
    for code, path in HOME_REDIRECT_ORDER:
        if current_user.has_permission(code):
            return redirect(path)
    return redirect("/omni-translate")


@bp.route("/projects")
@login_required
@permission_required("projects")
def index():
    recover_all_interrupted_tasks()
    rows = project_store.list_translation_projects(current_user.id)
    from datetime import datetime
    return render_template("projects.html", projects=rows, now=datetime.now(),
                           retention_hours=get_retention_hours("translation"))


@bp.route("/video-translate-av-sync")
@login_required
def av_sync_page():
    target = url_for("projects.sentence_translate_page")
    if request.query_string:
        target = f"{target}?{request.query_string.decode('utf-8', errors='ignore')}"
    return redirect(target)


@bp.route("/sentence_translate")
@login_required
def sentence_translate_page():
    from datetime import datetime

    av_target_languages = list_available_av_target_language_options()
    filter_langs = tuple(item["code"] for item in av_target_languages)
    current_lang = request.args.get("lang", "").strip().lower()
    if current_lang and current_lang not in filter_langs:
        current_lang = ""

    rows = []
    try:
        rows = project_store.list_av_sync_projects(current_user.id, current_lang)
    except Exception:
        rows = []
    try:
        retention_hours = get_retention_hours("translation")
    except Exception:
        retention_hours = 24

    return render_template(
        "multi_translate_list.html",
        projects=rows,
        now=datetime.now(),
        current_lang=current_lang,
        filter_langs=filter_langs,
        supported_langs=filter_langs,
        retention_hours=retention_hours,
        module_title="视频翻译音画同步",
        module_list_path="/sentence_translate",
        module_detail_path="/sentence_translate",
        module_start_api="/api/tasks",
        module_delete_api="/api/tasks",
        module_new_title="新建视频翻译音画同步项目",
        module_empty_text="还没有音画同步项目，点击右上角新建",
        module_storage_key="avSyncViewMode",
        module_icon="🎬",
        module_kind="av_sync",
        av_target_languages=av_target_languages,
        av_target_markets=AV_TARGET_MARKET_OPTIONS,
        av_translate_defaults=build_available_av_translate_inputs(),
    )


def _load_project_row(task_id: str) -> tuple[dict, dict]:
    row = project_store.get_project_detail_row(task_id, current_user.id)
    if not row:
        abort(404)
    state = {}
    if row.get("state_json"):
        try:
            state = json.loads(row["state_json"])
        except Exception:
            pass
    return row, state


def _render_av_sync_detail(row: dict, state: dict):
    from appcore.api_keys import get_key

    try:
        translate_pref = get_key(current_user.id, "translate_pref") or "openrouter"
    except Exception:
        translate_pref = "openrouter"
    target_lang = _av_sync_target_lang(state)
    state = dict(state)
    state.setdefault("target_lang", target_lang)
    return render_template(
        "av_sync_detail.html",
        project=row,
        state=state,
        initial_task_json=json.dumps(state, ensure_ascii=False),
        translate_pref=translate_pref,
        target_lang=target_lang,
        av_target_languages=list_available_av_target_language_options(),
        av_target_markets=AV_TARGET_MARKET_OPTIONS,
        av_translate_defaults=build_available_av_translate_inputs(),
    )


@bp.route("/sentence_translate/<task_id>")
@login_required
def sentence_translate_detail(task_id: str):
    recover_project_if_needed(task_id, "sentence_translate")
    row, state = _load_project_row(task_id)
    if not _is_av_sync_state(state):
        abort(404)
    return _render_av_sync_detail(row, state)


@bp.route("/projects/<task_id>")
@login_required
def detail(task_id: str):
    recover_project_if_needed(task_id, "translation")
    row, state = _load_project_row(task_id)
    from appcore.api_keys import get_key
    try:
        translate_pref = get_key(current_user.id, "translate_pref") or "openrouter"
    except Exception:
        translate_pref = "openrouter"
    if _is_av_sync_state(state):
        return redirect(
            url_for("projects.sentence_translate_detail", task_id=task_id),
            code=302,
        )
    return render_template(
        "project_detail.html",
        project=row,
        state=state,
        initial_task_json=json.dumps(state, ensure_ascii=False),
        translate_pref=translate_pref,
        av_target_languages=list_available_av_target_language_options(),
        av_target_markets=AV_TARGET_MARKET_OPTIONS,
        av_translate_defaults=build_available_av_translate_inputs(),
    )


@bp.route("/projects/<task_id>/download/tos/<path:tos_key>")
@login_required
def download_tos(task_id: str, tos_key: str):
    del tos_key
    row = project_store.get_project_download_status_row(task_id, current_user.id)
    if not row:
        abort(404)
    if row.get("deleted_at"):
        abort(410)
    abort(410)
