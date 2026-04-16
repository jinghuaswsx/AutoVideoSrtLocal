from __future__ import annotations

import io
import os
import tempfile
import threading
import uuid
import zipfile
from datetime import datetime

from flask import Blueprint, Response, abort, jsonify, redirect, render_template, request
from flask_login import current_user, login_required

from appcore import medias, task_state, tos_clients
from appcore.db import execute as db_execute
from appcore.db import query_one as db_query_one
from appcore.gemini_image import IMAGE_MODELS, is_valid_image_model
from appcore.image_translate_settings import SUPPORTED_LANGS, get_prompts_for_lang
from web import store
from web.services import image_translate_runner

bp = Blueprint("image_translate", __name__)

_MAX_ITEMS = 20
_ALLOWED_EXT = {".jpg", ".jpeg", ".png", ".webp"}

_upload_guard = threading.Lock()
_upload_reservations: dict[str, dict] = {}


def _get_owned_task(task_id: str) -> dict:
    task = store.get(task_id)
    if (
        not task
        or task.get("_user_id") != current_user.id
        or task.get("type") != "image_translate"
        or (task.get("status") or "").strip() == "deleted"
        or task.get("deleted_at")
    ):
        abort(404)
    return task


def _target_language_name(code: str) -> str:
    row = db_query_one(
        "SELECT name_zh FROM media_languages WHERE code=%s AND enabled=1",
        (code,),
    )
    if not row:
        return code
    return row["name_zh"] or code


def _start_runner(task_id: str, uid: int) -> bool:
    return image_translate_runner.start(task_id, user_id=uid)


def _build_source_object_key(user_id: int, task_id: str, idx: int, ext: str) -> str:
    ext = ext.lower().lstrip(".") or "jpg"
    return f"uploads/image_translate/{user_id}/{task_id}/src_{idx}.{ext}"


def _state_payload(task: dict) -> dict:
    return {
        "id": task.get("id"),
        "type": "image_translate",
        "status": task.get("status") or "queued",
        "preset": task.get("preset") or "",
        "target_language": task.get("target_language") or "",
        "target_language_name": task.get("target_language_name") or "",
        "model_id": task.get("model_id") or "",
        "prompt": task.get("prompt") or "",
        "progress": dict(task.get("progress") or {}),
        "items": list(task.get("items") or []),
        "steps": dict(task.get("steps") or {}),
        "error": task.get("error") or "",
    }


@bp.route("/api/image-translate/models", methods=["GET"])
@login_required
def api_models():
    from appcore.api_keys import resolve_extra
    extra = resolve_extra(current_user.id, "image_translate") or {}
    return jsonify({
        "items": [{"id": mid, "name": label} for mid, label in IMAGE_MODELS],
        "default_model_id": (extra.get("default_model_id") or "").strip(),
    })


@bp.route("/api/image-translate/system-prompts", methods=["GET"])
@login_required
def api_system_prompts():
    lang = (request.args.get("lang") or "").strip().lower()
    if lang not in SUPPORTED_LANGS:
        return jsonify({"error": f"lang 必须是 {SUPPORTED_LANGS} 之一"}), 400
    return jsonify(get_prompts_for_lang(lang))


@bp.route("/api/image-translate/upload/bootstrap", methods=["POST"])
@login_required
def api_upload_bootstrap():
    if not tos_clients.is_tos_configured():
        return jsonify({"error": "TOS 未配置"}), 503
    body = request.get_json(silent=True) or {}
    files = body.get("files") or []
    if not files:
        return jsonify({"error": "files 不能为空"}), 400
    if len(files) > _MAX_ITEMS:
        return jsonify({"error": f"单次最多 {_MAX_ITEMS} 张"}), 400

    task_id = str(uuid.uuid4())
    uploads = []
    reserved = []
    for idx, f in enumerate(files):
        filename = (f.get("filename") or "").strip()
        if not filename:
            return jsonify({"error": f"第 {idx} 张缺少 filename"}), 400
        dot_idx = filename.rfind(".")
        ext = filename[dot_idx:].lower() if dot_idx >= 0 else ""
        if ext not in _ALLOWED_EXT:
            return jsonify({"error": f"不支持的图片格式: {filename}"}), 400
        key = _build_source_object_key(current_user.id, task_id, idx, ext)
        uploads.append({
            "idx": idx,
            "object_key": key,
            "upload_url": tos_clients.generate_signed_upload_url(key),
        })
        reserved.append({"idx": idx, "object_key": key, "filename": filename})
    with _upload_guard:
        _upload_reservations[task_id] = {
            "user_id": current_user.id,
            "files": reserved,
        }
    return jsonify({"task_id": task_id, "uploads": uploads})


@bp.route("/api/image-translate/upload/complete", methods=["POST"])
@login_required
def api_upload_complete():
    body = request.get_json(silent=True) or {}
    task_id = (body.get("task_id") or "").strip()
    preset = (body.get("preset") or "").strip().lower()
    lang_code = (body.get("target_language") or "").strip().lower()
    model_id = (body.get("model_id") or "").strip()
    prompt_tpl = (body.get("prompt") or "").strip()
    uploaded = body.get("uploaded") or []

    with _upload_guard:
        rv = _upload_reservations.get(task_id)
    if not rv or rv["user_id"] != current_user.id:
        return jsonify({"error": "task_id 非法或过期"}), 403
    if preset not in {"cover", "detail"}:
        return jsonify({"error": "preset 必须是 cover 或 detail"}), 400
    if not medias.is_valid_language(lang_code) or lang_code == "en":
        return jsonify({"error": "目标语言不支持"}), 400
    if not is_valid_image_model(model_id):
        return jsonify({"error": "模型不支持"}), 400
    if not prompt_tpl:
        return jsonify({"error": "prompt 不能为空"}), 400
    if not uploaded:
        return jsonify({"error": "uploaded 不能为空"}), 400

    reserved = {f["idx"]: f for f in rv["files"]}
    items = []
    for u in uploaded:
        idx = int(u.get("idx"))
        key = (u.get("object_key") or "").strip()
        filename = (u.get("filename") or reserved.get(idx, {}).get("filename") or "").strip()
        if idx not in reserved or reserved[idx]["object_key"] != key:
            return jsonify({"error": f"上传项不匹配 idx={idx}"}), 400
        if not tos_clients.object_exists(key):
            return jsonify({"error": f"对象不存在 idx={idx}"}), 400
        items.append({"idx": idx, "filename": filename, "src_tos_key": key})

    lang_name = _target_language_name(lang_code)
    # Prompts are language-specific (no placeholders). Use as-is.
    final_prompt = prompt_tpl
    task_dir = ""
    task_state.create_image_translate(
        task_id,
        task_dir,
        user_id=current_user.id,
        preset=preset,
        target_language=lang_code,
        target_language_name=lang_name,
        model_id=model_id,
        prompt=final_prompt,
        items=items,
    )
    # 记用户偏好（容错，失败不影响提交）
    try:
        from appcore.api_keys import set_key
        set_key(current_user.id, "image_translate", "", {"default_model_id": model_id})
    except Exception:
        pass

    with _upload_guard:
        _upload_reservations.pop(task_id, None)

    _start_runner(task_id, current_user.id)
    return jsonify({"task_id": task_id}), 201


@bp.route("/api/image-translate/<task_id>", methods=["GET"])
@login_required
def api_state(task_id: str):
    task = _get_owned_task(task_id)
    return jsonify(_state_payload(task))


def _get_item(task: dict, idx: int) -> dict | None:
    for it in task.get("items") or []:
        if int(it.get("idx")) == int(idx):
            return it
    return None


@bp.route("/api/image-translate/<task_id>/artifact/source/<int:idx>", methods=["GET"])
@login_required
def api_source_artifact(task_id: str, idx: int):
    task = _get_owned_task(task_id)
    item = _get_item(task, idx)
    if not item or not item.get("src_tos_key"):
        abort(404)
    return redirect(tos_clients.generate_signed_download_url(item["src_tos_key"]))


@bp.route("/api/image-translate/<task_id>/artifact/result/<int:idx>", methods=["GET"])
@login_required
def api_result_artifact(task_id: str, idx: int):
    task = _get_owned_task(task_id)
    item = _get_item(task, idx)
    if not item or item.get("status") != "done" or not item.get("dst_tos_key"):
        abort(404)
    return redirect(tos_clients.generate_signed_download_url(item["dst_tos_key"]))


@bp.route("/api/image-translate/<task_id>/download/result/<int:idx>", methods=["GET"])
@login_required
def api_download_result(task_id: str, idx: int):
    task = _get_owned_task(task_id)
    item = _get_item(task, idx)
    if not item or item.get("status") != "done" or not item.get("dst_tos_key"):
        abort(404)
    return redirect(tos_clients.generate_signed_download_url(item["dst_tos_key"]))


@bp.route("/api/image-translate/<task_id>/retry/<int:idx>", methods=["POST"])
@login_required
def api_retry_item(task_id: str, idx: int):
    task = _get_owned_task(task_id)
    item = _get_item(task, idx)
    if not item:
        abort(404)
    if item.get("status") != "failed":
        return jsonify({"error": "只有 failed 的图可以重试"}), 409
    item["status"] = "pending"
    item["attempts"] = 0
    item["error"] = ""
    item["dst_tos_key"] = ""
    total = len(task["items"])
    done = sum(1 for it in task["items"] if it["status"] == "done")
    failed = sum(1 for it in task["items"] if it["status"] == "failed")
    task["progress"] = {"total": total, "done": done, "failed": failed, "running": 0}
    task["status"] = "queued"
    store.update(
        task_id,
        items=task["items"],
        progress=task["progress"],
        status="queued",
    )
    _start_runner(task_id, current_user.id)
    return jsonify({"task_id": task_id, "idx": idx, "status": "queued"}), 202


@bp.route("/api/image-translate/<task_id>/download/zip", methods=["GET"])
@login_required
def api_download_zip(task_id: str):
    task = _get_owned_task(task_id)
    done_items = [it for it in (task.get("items") or [])
                  if it.get("status") == "done" and it.get("dst_tos_key")]
    if not done_items:
        abort(404)

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for it in done_items:
            key = it["dst_tos_key"]
            suffix = os.path.splitext(key)[1] or ".png"
            fd, tmp_path = tempfile.mkstemp(suffix=suffix, prefix="it_zip_")
            os.close(fd)
            try:
                tos_clients.download_file(key, tmp_path)
                with open(tmp_path, "rb") as f:
                    raw = f.read()
            finally:
                if os.path.exists(tmp_path):
                    try:
                        os.unlink(tmp_path)
                    except OSError:
                        pass
            base = os.path.splitext(
                os.path.basename(it.get("filename") or f"out_{it['idx']}")
            )[0] or "image"
            zf.writestr(f"{int(it['idx']):02d}_{base}{suffix}", raw)
    buf.seek(0)

    filename = f"{task_id}-{task.get('target_language') or 'result'}.zip"
    return Response(
        buf.getvalue(),
        mimetype="application/zip",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@bp.route("/image-translate", methods=["GET"])
@login_required
def page_list():
    import json as _json
    from appcore.db import query as db_query
    rows = db_query(
        """
        SELECT id, created_at, status, state_json
        FROM projects
        WHERE user_id=%s AND type='image_translate' AND deleted_at IS NULL
        ORDER BY created_at DESC
        LIMIT 100
        """,
        (current_user.id,),
    )
    history = []
    for row in rows or []:
        state = {}
        try:
            state = _json.loads(row.get("state_json") or "{}")
        except Exception:
            state = {}
        items = state.get("items") or []
        done = sum(1 for it in items if it.get("status") == "done")
        preset = state.get("preset") or ""
        preset_label = "封面图翻译" if preset == "cover" else ("产品详情图翻译" if preset == "detail" else "")
        history.append({
            "id": row["id"],
            "created_at": row.get("created_at"),
            "status": row.get("status") or state.get("status") or "",
            "preset": preset,
            "preset_label": preset_label,
            "target_language_name": state.get("target_language_name") or "",
            "model_id": state.get("model_id") or "",
            "total": len(items),
            "done": done,
        })
    return render_template("image_translate_list.html", history=history)


@bp.route("/image-translate/<task_id>", methods=["GET"])
@login_required
def page_detail(task_id: str):
    task = _get_owned_task(task_id)
    return render_template(
        "image_translate_detail.html",
        task_id=task_id,
        state=_state_payload(task),
    )


@bp.route("/api/image-translate/<task_id>", methods=["DELETE"])
@login_required
def api_delete_task(task_id: str):
    task = _get_owned_task(task_id)
    for it in task.get("items") or []:
        for key_name in ("src_tos_key", "dst_tos_key"):
            v = (it.get(key_name) or "").strip()
            if v:
                try:
                    tos_clients.delete_object(v)
                except Exception:
                    pass
    try:
        db_execute(
            "UPDATE projects SET deleted_at = NOW() WHERE id=%s AND user_id=%s",
            (task_id, current_user.id),
        )
    except Exception:
        pass
    store.update(task_id, status="deleted",
                 deleted_at=datetime.now().isoformat(timespec="seconds"))
    return ("", 204)
