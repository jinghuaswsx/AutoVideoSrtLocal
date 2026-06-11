import json
import logging
import re
from flask import Blueprint, render_template, redirect, url_for, request, flash, jsonify
from flask_login import login_required
from web.auth import admin_required
from appcore.db import query, query_one
from appcore import server_health

log = logging.getLogger(__name__)

bp = Blueprint("server_health", __name__, url_prefix="/server-health")

def _format_record(row) -> dict | None:
    if not row:
        return None
    r = dict(row)
    try:
        r["cpu_usage"] = json.loads(r["cpu_usage"]) if r.get("cpu_usage") else {}
    except Exception:
        r["cpu_usage"] = {}
    try:
        r["memory_usage"] = json.loads(r["memory_usage"]) if r.get("memory_usage") else {}
    except Exception:
        r["memory_usage"] = {}
    try:
        r["gpu_usage"] = json.loads(r["gpu_usage"]) if r.get("gpu_usage") else None
    except Exception:
        r["gpu_usage"] = None
    try:
        r["disk_usage"] = json.loads(r["disk_usage"]) if r.get("disk_usage") else {}
    except Exception:
        r["disk_usage"] = {}
    try:
        r["issues"] = json.loads(r["issues"]) if r.get("issues") else []
    except Exception:
        r["issues"] = []
        
    sugg = r.get("suggestions") or ""
    code_blocks = re.findall(r'```(?:bash|shell|sh)?\n(.*?)\n```', sugg, re.DOTALL)
    r["codex_commands"] = "\n\n".join(code_blocks).strip() if code_blocks else ""
    # 去除代码块后的说明文本（如果未匹配到代码块，则整个 suggestions 都视为说明）
    if code_blocks:
        clean_sugg = re.sub(r'```(?:bash|shell|sh)?\n.*?\n```', '', sugg, flags=re.DOTALL).strip()
        r["suggestions_text"] = clean_sugg
    else:
        r["suggestions_text"] = sugg
        
    return r

@bp.route("")
@login_required
@admin_required
def index():
    """默认加载最新的一条巡查记录详情，如果无记录则当即触发一次采集。"""
    row = query_one("SELECT * FROM server_health_records ORDER BY created_at DESC LIMIT 1")
    if not row:
        try:
            # 当即触发一次，写入 DB
            record_id = server_health.evaluate_and_save_record()
            row = query_one("SELECT * FROM server_health_records WHERE id = %s", (record_id,))
        except Exception as e:
            log.error("[server_health] Auto check on empty DB failed: %s", e, exc_info=True)
            flash(f"系统初次启动采集失败: {e}", "danger")
            return render_template("server_health.html", record=None)
            
    record = _format_record(row)
    return render_template("server_health.html", record=record)

@bp.route("/record/<int:record_id>")
@login_required
@admin_required
def detail(record_id: int):
    """查看指定 ID 的巡查详情。"""
    row = query_one("SELECT * FROM server_health_records WHERE id = %s", (record_id,))
    if not row:
        flash("未找到该巡查记录", "warning")
        return redirect(url_for("server_health.index"))
        
    record = _format_record(row)
    return render_template("server_health.html", record=record)

@bp.route("/list")
@login_required
@admin_required
def list_records():
    """查看历史巡查记录列表。"""
    page = request.args.get("page", 1, type=int)
    limit = 20
    offset = (page - 1) * limit
    
    total_row = query_one("SELECT COUNT(*) as cnt FROM server_health_records")
    total = total_row["cnt"] if total_row else 0
    
    rows = query(
        "SELECT id, created_at, status, system_load FROM server_health_records ORDER BY created_at DESC LIMIT %s OFFSET %s",
        (limit, offset)
    )
    
    records = []
    for row in rows:
        records.append({
            "id": row["id"],
            "created_at": row["created_at"],
            "status": row["status"],
            "system_load": row["system_load"]
        })
        
    total_pages = (total + limit - 1) // limit
    return render_template(
        "server_health_list.html",
        records=records,
        page=page,
        total_pages=total_pages,
        total=total
    )

@bp.route("/check", methods=["POST"])
@login_required
@admin_required
def manual_check():
    """手动触发一次新的资源巡查。"""
    try:
        record_id = server_health.evaluate_and_save_record()
        flash("巡查成功完成！", "success")
        return redirect(url_for("server_health.detail", record_id=record_id))
    except Exception as e:
        log.error("[server_health] Manual check failed: %s", e, exc_info=True)
        flash(f"手动巡查失败: {e}", "danger")
        return redirect(url_for("server_health.index"))
