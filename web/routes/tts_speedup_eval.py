"""Admin: TTS 变速短路 AI 评估跨任务查询页 + 重跑 + CSV 导出。"""
from __future__ import annotations

import csv
import io

from flask import Blueprint, render_template, request, redirect, url_for, Response, flash
from flask_login import login_required, current_user

from web.auth import admin_required
from web.services.tts_speedup_eval import (
    build_tts_speedup_list_fallback_response,
    build_tts_speedup_retry_response,
    tts_speedup_eval_flask_response,
)
from appcore import tts_speedup_eval

bp = Blueprint("tts_speedup_eval", __name__, url_prefix="/admin")


@bp.route("/tts-speedup-evaluations/", methods=["GET"])
@login_required
@admin_required
def list_page():
    rows = tts_speedup_eval.list_evaluations(request.args)
    summary = tts_speedup_eval.summarize_evaluations(request.args)
    try:
        return render_template(
            "admin/tts_speedup_eval_list.html",
            rows=rows, summary=summary, args=request.args,
        )
    except Exception:
        # 模板未到位时（Task 10 之前）返回 JSON 兜底，便于 Task 8 自身测试
        return tts_speedup_eval_flask_response(
            build_tts_speedup_list_fallback_response(rows=rows, summary=summary)
        )


@bp.route("/tts-speedup-evaluations/<int:eval_id>/retry", methods=["POST"])
@login_required
@admin_required
def retry_endpoint(eval_id: int):
    ok = tts_speedup_eval.retry_evaluation(
        eval_id=eval_id, user_id=current_user.id,
    )
    if request.is_json or request.headers.get("Accept", "").startswith("application/json"):
        return tts_speedup_eval_flask_response(
            build_tts_speedup_retry_response(ok=ok, eval_id=eval_id)
        )
    flash("评估已重跑" if ok else "评估重跑失败，请查看 error_text", "info")
    return redirect(url_for("tts_speedup_eval.list_page"))


@bp.route("/tts-speedup-evaluations.csv", methods=["GET"])
@login_required
@admin_required
def export_csv():
    rows = tts_speedup_eval.list_evaluations(request.args, limit=10000)
    buf = io.StringIO()
    fieldnames = [
        "id", "created_at", "task_id", "round_index", "language",
        "video_duration", "audio_pre_duration", "audio_post_duration",
        "speed_ratio", "hit_final_range",
        "score_overall", "score_naturalness", "score_pacing",
        "score_timbre", "score_intelligibility",
        "summary_text", "flags_json",
        "model_provider", "model_id",
        "llm_input_tokens", "llm_output_tokens", "llm_cost_usd",
        "status", "error_text",
    ]
    writer = csv.DictWriter(buf, fieldnames=fieldnames, extrasaction="ignore")
    writer.writeheader()
    for r in rows:
        writer.writerow({k: r.get(k) for k in fieldnames})
    csv_text = buf.getvalue()
    return Response(
        csv_text.encode("utf-8-sig"),
        mimetype="text/csv; charset=utf-8",
        headers={
            "Content-Disposition":
                'attachment; filename="tts_speedup_evaluations.csv"',
        },
    )
