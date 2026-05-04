"""Admin: TTS 变速短路 AI 评估跨任务查询页 + 重跑 + CSV 导出。"""
from __future__ import annotations

import csv
import io
import json
from typing import Any

from flask import Blueprint, render_template, request, jsonify, redirect, url_for, Response, flash
from flask_login import login_required, current_user

from web.auth import admin_required
from appcore.db import query as db_query, query_one as db_query_one
from appcore import tts_speedup_eval

bp = Blueprint("tts_speedup_eval", __name__, url_prefix="/admin")

_LIST_SQL = """
  SELECT id, task_id, round_index, language,
         video_duration, audio_pre_duration, audio_post_duration,
         speed_ratio, hit_final_range,
         score_naturalness, score_pacing, score_timbre,
         score_intelligibility, score_overall,
         summary_text, flags_json,
         model_provider, model_id, llm_input_tokens, llm_output_tokens,
         llm_cost_usd, status, error_text,
         audio_pre_path, audio_post_path,
         created_at, evaluated_at
    FROM tts_speedup_evaluations
   {where}
   ORDER BY created_at DESC
   LIMIT %s OFFSET %s
"""


def _build_where(args) -> tuple[str, list]:
    clauses = []
    params: list = []
    lang = (args.get("language") or "").strip()
    if lang:
        clauses.append("language = %s")
        params.append(lang)
    status = (args.get("status") or "").strip()
    if status in ("ok", "failed", "pending"):
        clauses.append("status = %s")
        params.append(status)
    hit = args.get("hit_final")
    if hit in ("0", "1"):
        clauses.append("hit_final_range = %s")
        params.append(int(hit))
    min_overall = args.get("min_overall")
    if min_overall and str(min_overall).isdigit():
        clauses.append("score_overall >= %s")
        params.append(int(min_overall))
    return ("WHERE " + " AND ".join(clauses)) if clauses else "", params


def _fetch_rows(args, *, limit: int = 200, offset: int = 0) -> list[dict]:
    where, params = _build_where(args)
    sql = _LIST_SQL.format(where=where)
    return db_query(sql, (*params, limit, offset))


def _fetch_summary(args) -> dict:
    where, params = _build_where(args)
    total_row = db_query_one(
        f"SELECT COUNT(*) AS n, AVG(score_overall) AS avg_overall, "
        f"  SUM(hit_final_range) AS hits "
        f"FROM tts_speedup_evaluations {where}",
        tuple(params),
    ) or {"n": 0, "avg_overall": None, "hits": 0}
    n = int(total_row.get("n") or 0)
    hits = int(total_row.get("hits") or 0)
    avg_overall = float(total_row["avg_overall"]) if total_row.get("avg_overall") else 0.0
    flag_rows = db_query(
        f"SELECT flags_json FROM tts_speedup_evaluations {where} "
        f"ORDER BY created_at DESC LIMIT 500",
        tuple(params),
    )
    counts: dict[str, int] = {}
    for r in flag_rows:
        raw = r.get("flags_json")
        if not raw:
            continue
        try:
            tags = json.loads(raw) if isinstance(raw, str) else (raw or [])
        except Exception:
            tags = []
        for t in tags:
            counts[t] = counts.get(t, 0) + 1
    top = sorted(counts.items(), key=lambda kv: kv[1], reverse=True)[:5]
    return {
        "total": n,
        "hit_final_pct": round(hits / n * 100, 1) if n else 0.0,
        "avg_overall": round(avg_overall, 2),
        "top_flags": [{"flag": k, "count": v} for k, v in top],
    }


@bp.route("/tts-speedup-evaluations/", methods=["GET"])
@login_required
@admin_required
def list_page():
    rows = _fetch_rows(request.args)
    summary = _fetch_summary(request.args)
    try:
        return render_template(
            "admin/tts_speedup_eval_list.html",
            rows=rows, summary=summary, args=request.args,
        )
    except Exception:
        # 模板未到位时（Task 10 之前）返回 JSON 兜底，便于 Task 8 自身测试
        return jsonify({
            "rows_count": len(rows),
            "summary": summary,
            "rows": rows,
        })


@bp.route("/tts-speedup-evaluations/<int:eval_id>/retry", methods=["POST"])
@login_required
@admin_required
def retry_endpoint(eval_id: int):
    ok = tts_speedup_eval.retry_evaluation(
        eval_id=eval_id, user_id=current_user.id,
    )
    if request.is_json or request.headers.get("Accept", "").startswith("application/json"):
        return jsonify({"ok": ok, "eval_id": eval_id})
    flash("评估已重跑" if ok else "评估重跑失败，请查看 error_text", "info")
    return redirect(url_for("tts_speedup_eval.list_page"))


@bp.route("/tts-speedup-evaluations.csv", methods=["GET"])
@login_required
@admin_required
def export_csv():
    rows = _fetch_rows(request.args, limit=10000)
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
