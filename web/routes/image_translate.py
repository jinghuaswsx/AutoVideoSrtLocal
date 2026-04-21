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
from appcore import image_translate_settings as its

_BACKEND_LABELS = {
    "aistudio":   "Google AI Studio",
    "cloud":      "Google Cloud (Vertex AI)",
    "openrouter": "OpenRouter",
}


def _backend_badge() -> dict:
    """读 system_settings 里的全局通道；DB 异常时回落 aistudio，避免页面 500。"""
    try:
        key = its.get_channel()
    except Exception:
        key = "aistudio"
    return {"key": key, "label": _BACKEND_LABELS.get(key, key or "unknown")}
from web import store
from web.services import image_translate_runner

bp = Blueprint("image_translate", __name__)

_MAX_ITEMS = 20
_ALLOWED_EXT = {".jpg", ".jpeg", ".png", ".webp"}
_PRODUCT_NAME_MAX_LEN = 60
_PROJECT_NAME_ILLEGAL = set('\\/:*?"<>|\t\r\n')


def _sanitize_product_name(value: str) -> str:
    cleaned = "".join(ch for ch in (value or "") if ch not in _PROJECT_NAME_ILLEGAL)
    return cleaned.strip()


def _compose_project_name(product_name: str, preset: str, lang_name: str) -> str:
    from datetime import datetime as _dt
    today = _dt.now().strftime("%Y%m%d")
    preset_label = "封面" if preset == "cover" else ("商品详情" if preset == "detail" else "")
    parts = [product_name.strip(), preset_label, (lang_name or "").strip(), today]
    return "-".join(p for p in parts if p)

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


def start_image_translate_runner(task_id: str, uid: int) -> bool:
    return _start_runner(task_id, uid)


def _build_source_object_key(user_id: int, task_id: str, idx: int, ext: str) -> str:
    ext = ext.lower().lstrip(".") or "jpg"
    return f"uploads/image_translate/{user_id}/{task_id}/src_{idx}.{ext}"


def _resolve_source_bucket(task: dict, item: dict) -> str:
    """返回 'media' 或 'upload'（默认）。

    从 medias_edit_detail 入口创建的任务，源图来自 media bucket；老任务默认 upload。
    item 级优先级高于 task 级，保证同一任务里混合来源也能各自对。
    """
    bucket = (item.get("source_bucket") or "").strip().lower()
    if not bucket:
        bucket = ((task.get("medias_context") or {}).get("source_bucket") or "").strip().lower()
    return "media" if bucket == "media" else "upload"


def _signed_source_url(task: dict, item: dict) -> str:
    if _resolve_source_bucket(task, item) == "media":
        return tos_clients.generate_signed_media_download_url(item["src_tos_key"])
    return tos_clients.generate_signed_download_url(item["src_tos_key"])


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
        "product_name": task.get("product_name") or "",
        "project_name": task.get("project_name") or "",
        "progress": dict(task.get("progress") or {}),
        "items": list(task.get("items") or []),
        "medias_context": dict(task.get("medias_context") or {}),
        "steps": dict(task.get("steps") or {}),
        "error": task.get("error") or "",
        "is_running": image_translate_runner.is_running(task.get("id") or ""),
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
    if not its.is_image_translate_language_supported(lang):
        return jsonify({"error": "lang must be a supported image-translate language"}), 400
    return jsonify(its.get_prompts_for_lang(lang))


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
    product_name_raw = (body.get("product_name") or "").strip()
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
    product_name = _sanitize_product_name(product_name_raw)
    if not product_name:
        return jsonify({"error": "产品名不能为空"}), 400
    if len(product_name) > _PRODUCT_NAME_MAX_LEN:
        return jsonify({"error": f"产品名长度不能超过 {_PRODUCT_NAME_MAX_LEN} 字符"}), 400
    if not uploaded:
        return jsonify({"error": "uploaded 不能为空"}), 400

    reserved = {f["idx"]: f for f in rv["files"]}
    items = []
    seen_idxs: set[int] = set()
    for u in uploaded:
        if not isinstance(u, dict):
            return jsonify({"error": "uploaded item must be an object"}), 400
        idx_raw = u.get("idx")
        if isinstance(idx_raw, bool):
            return jsonify({"error": "uploaded item idx must be an integer"}), 400
        if isinstance(idx_raw, int):
            idx = idx_raw
        elif isinstance(idx_raw, str) and idx_raw.strip().isdigit():
            idx = int(idx_raw.strip())
        else:
            return jsonify({"error": "uploaded item idx must be an integer"}), 400
        if idx in seen_idxs:
            return jsonify({"error": f"duplicated uploaded idx={idx}"}), 400
        seen_idxs.add(idx)
        key = (u.get("object_key") or "").strip()
        filename = (u.get("filename") or reserved.get(idx, {}).get("filename") or "").strip()
        if idx not in reserved or reserved[idx]["object_key"] != key:
            return jsonify({"error": f"上传项不匹配 idx={idx}"}), 400
        if not tos_clients.object_exists(key):
            return jsonify({"error": f"对象不存在 idx={idx}"}), 400
        items.append({"idx": idx, "filename": filename, "src_tos_key": key})
    if seen_idxs != set(reserved):
        return jsonify({"error": "uploaded items must exactly match reserved items"}), 400

    lang_name = _target_language_name(lang_code)
    # Prompts are language-specific (no placeholders). Use as-is.
    final_prompt = prompt_tpl
    task_dir = ""
    project_name = _compose_project_name(product_name, preset, lang_name)
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
        product_name=product_name,
        project_name=project_name,
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
    return redirect(_signed_source_url(task, item))


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
    if image_translate_runner.is_running(task_id):
        return jsonify({"error": "任务正在跑，等跑完再重试"}), 409
    old_dst = (item.get("dst_tos_key") or "").strip()
    if old_dst:
        try:
            tos_clients.delete_object(old_dst)
        except Exception:
            pass
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


@bp.route("/api/image-translate/<task_id>/retry-failed", methods=["POST"])
@login_required
def api_retry_failed(task_id: str):
    """一键把该任务下所有 failed 项重置为 pending，重新入队跑一轮。"""
    task = _get_owned_task(task_id)
    items = task.get("items") or []
    reset_count = 0
    for item in items:
        if item.get("status") == "failed":
            item["status"] = "pending"
            item["attempts"] = 0
            item["error"] = ""
            item["dst_tos_key"] = ""
            reset_count += 1
    if reset_count == 0:
        return jsonify({"error": "当前没有失败项可重试"}), 409
    total = len(items)
    done = sum(1 for it in items if it["status"] == "done")
    failed = sum(1 for it in items if it["status"] == "failed")
    task["progress"] = {"total": total, "done": done, "failed": failed, "running": 0}
    task["status"] = "queued"
    store.update(
        task_id,
        items=items,
        progress=task["progress"],
        status="queued",
    )
    _start_runner(task_id, current_user.id)
    return jsonify({"task_id": task_id, "reset": reset_count, "status": "queued"}), 202


@bp.route("/api/image-translate/<task_id>/retry-unfinished", methods=["POST"])
@login_required
def api_retry_unfinished(task_id: str):
    """把所有非 done 的 item 重置为 pending 并重启 runner。
    与 retry-failed 的区别：范围不只是 failed，还包含 pending/running 僵尸。
    仅允许在 runner 不活跃时调用，避免与在跑的线程冲突。"""
    task = _get_owned_task(task_id)
    if image_translate_runner.is_running(task_id):
        return jsonify({"error": "任务正在跑，等跑完再重试"}), 409
    items = task.get("items") or []
    reset_count = 0
    for item in items:
        if item.get("status") == "done":
            continue
        old_dst = (item.get("dst_tos_key") or "").strip()
        if old_dst:
            try:
                tos_clients.delete_object(old_dst)
            except Exception:
                pass
        item["status"] = "pending"
        item["attempts"] = 0
        item["error"] = ""
        item["dst_tos_key"] = ""
        reset_count += 1
    if reset_count == 0:
        return jsonify({"error": "没有需要重试的图片"}), 409
    total = len(items)
    done = sum(1 for it in items if it["status"] == "done")
    task["progress"] = {"total": total, "done": done, "failed": 0, "running": 0}
    task["status"] = "queued"
    store.update(
        task_id,
        items=items,
        progress=task["progress"],
        status="queued",
    )
    _start_runner(task_id, current_user.id)
    return jsonify({"task_id": task_id, "reset": reset_count, "status": "queued"}), 202


@bp.route("/api/image-translate/<task_id>/retry-all", methods=["POST"])
@login_required
def api_retry_all(task_id: str):
    """把该任务所有 item（含 done）全部重置为 pending，删所有旧 dst，重启 runner。"""
    task = _get_owned_task(task_id)
    if image_translate_runner.is_running(task_id):
        return jsonify({"error": "任务正在跑，等跑完再重试"}), 409
    items = task.get("items") or []
    if not items:
        return jsonify({"error": "任务没有图片"}), 409
    for item in items:
        old_dst = (item.get("dst_tos_key") or "").strip()
        if old_dst:
            try:
                tos_clients.delete_object(old_dst)
            except Exception:
                pass
        item["status"] = "pending"
        item["attempts"] = 0
        item["error"] = ""
        item["dst_tos_key"] = ""
    total = len(items)
    task["progress"] = {"total": total, "done": 0, "failed": 0, "running": 0}
    task["status"] = "queued"
    store.update(
        task_id,
        items=items,
        progress=task["progress"],
        status="queued",
    )
    _start_runner(task_id, current_user.id)
    return jsonify({"task_id": task_id, "reset": total, "status": "queued"}), 202


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
            "project_name": state.get("project_name") or "",
            "product_name": state.get("product_name") or "",
            "model_id": state.get("model_id") or "",
            "total": len(items),
            "done": done,
        })
    return render_template(
        "image_translate_list.html",
        history=history,
        gemini_backend=_backend_badge(),
    )


@bp.route("/image-translate/<task_id>", methods=["GET"])
@login_required
def page_detail(task_id: str):
    task = _get_owned_task(task_id)
    return render_template(
        "image_translate_detail.html",
        task_id=task_id,
        state=_state_payload(task),
        gemini_backend=_backend_badge(),
    )


@bp.route("/api/image-translate/<task_id>", methods=["DELETE"])
@login_required
def api_delete_task(task_id: str):
    task = _get_owned_task(task_id)
    for it in task.get("items") or []:
        # 源图：来自 media bucket 的是素材库永久资源，不得清理；只清 upload bucket 里任务自己上传的 src。
        src_key = (it.get("src_tos_key") or "").strip()
        if src_key and _resolve_source_bucket(task, it) != "media":
            try:
                tos_clients.delete_object(src_key)
            except Exception:
                pass
        # 输出始终是 upload bucket 的 artifacts/ 路径，删除任务时可以一起清。
        dst_key = (it.get("dst_tos_key") or "").strip()
        if dst_key:
            try:
                tos_clients.delete_object(dst_key)
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
