"""Persistence for single-product fine AI evaluation runs."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from appcore.db import execute, query, query_one


JSON_RUN_FIELDS = {
    "countries": "countries_json",
    "product_snapshot": "product_snapshot_json",
    "product_facts": "product_facts_json",
    "summary": "summary_json",
    "frontend": "frontend_json",
    "metadata": "metadata_json",
    "progress": "progress_json",
}

JSON_COUNTRY_FIELDS = {
    "scores": "scores_json",
    "decision": "decision_json",
    "full_result": "full_result_json",
    "sources": "sources_json",
    "raw_response": "raw_response_json",
    "metadata": "metadata_json",
}

DATETIME_RUN_FIELDS = {"started_at", "completed_at", "failed_at", "created_at", "updated_at"}


class FineAiEvaluationRepository:
    def create_run(self, run: dict[str, Any]) -> dict[str, Any]:
        execute(
            "INSERT INTO ai_evaluation_runs "
            "(evaluation_run_id, product_id, status, countries_json, "
            " product_snapshot_json, product_facts_json, summary_json, frontend_json, "
            " metadata_json, progress_json, created_at, updated_at) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NOW(),NOW())",
            (
                run["evaluation_run_id"],
                int(run["product_id"]),
                run.get("status") or "queued",
                _dump(run.get("countries") or []),
                _dump(run.get("product_snapshot") or {}),
                _dump(run.get("product_facts") or {}),
                _dump(run.get("summary") or {}),
                _dump(run.get("frontend") or {}),
                _dump(run.get("metadata") or {}),
                _dump(run.get("progress") or {}),
            ),
        )
        return self.get_run(run["evaluation_run_id"]) or dict(run)

    def get_run(self, evaluation_run_id: str) -> dict[str, Any] | None:
        row = query_one(
            "SELECT * FROM ai_evaluation_runs WHERE evaluation_run_id=%s",
            (str(evaluation_run_id),),
        )
        return _load_run(row) if row else None

    def get_latest_run(self, product_id: int | str) -> dict[str, Any] | None:
        row = query_one(
            "SELECT * FROM ai_evaluation_runs WHERE product_id=%s "
            "ORDER BY created_at DESC, id DESC LIMIT 1",
            (int(product_id),),
        )
        return _load_run(row) if row else None

    def list_inflight_runs(self) -> list[dict[str, Any]]:
        rows = query(
            "SELECT * FROM ai_evaluation_runs WHERE status IN ('queued', 'running')",
            (),
        )
        return [_load_run(row) for row in rows or []]

    def get_latest_external_link_run(
        self,
        product_link: str,
        *,
        card_video_object_key: str = "",
        card_video_path: str = "",
        card_video_url: str = "",
        card_video_name: str = "",
    ) -> dict[str, Any] | None:
        link = str(product_link or "").strip()
        if not link:
            return None
        where = [
            "product_id=0",
            "JSON_UNQUOTE(JSON_EXTRACT(metadata_json, '$.source_type'))='external_product_link'",
            "("
            "JSON_UNQUOTE(JSON_EXTRACT(metadata_json, '$.external_product_link'))=%s OR "
            "JSON_UNQUOTE(JSON_EXTRACT(metadata_json, '$.link_check.original_link'))=%s OR "
            "JSON_UNQUOTE(JSON_EXTRACT(metadata_json, '$.link_check.selected_link'))=%s"
            ")",
        ]
        args: list[Any] = [link, link, link]
        video_filters = [
            ("card_video_object_key", card_video_object_key, "$.external_card_video.object_key"),
            ("card_video_path", card_video_path, "$.external_card_video.path"),
            ("card_video_url", card_video_url, "$.external_card_video.url"),
            ("card_video_name", card_video_name, "$.external_card_video.name"),
        ]
        for _, value, json_path in video_filters:
            text = str(value or "").strip()
            if not text:
                continue
            where.append(f"JSON_UNQUOTE(JSON_EXTRACT(metadata_json, '{json_path}'))=%s")
            args.append(text)
        row = query_one(
            "SELECT * FROM ai_evaluation_runs WHERE "
            + " AND ".join(where)
            + " ORDER BY created_at DESC, id DESC LIMIT 1",
            tuple(args),
        )
        if not row and str(card_video_path or "").strip():
            video_path = str(card_video_path or "").strip()
            fallback_row = query_one(
                "SELECT * FROM ai_evaluation_runs WHERE product_id=0 "
                "AND JSON_UNQUOTE(JSON_EXTRACT(metadata_json, '$.source_type'))='external_product_link' "
                "AND ("
                "JSON_UNQUOTE(JSON_EXTRACT(metadata_json, '$.external_card_video.path'))=%s OR "
                "JSON_UNQUOTE(JSON_EXTRACT(metadata_json, '$.card_video_path'))=%s OR "
                "JSON_UNQUOTE(JSON_EXTRACT(metadata_json, '$.video_path'))=%s"
                ") "
                "ORDER BY created_at DESC, id DESC LIMIT 1",
                (video_path, video_path, video_path),
            )
            if fallback_row:
                try:
                    meta_json = fallback_row.get("metadata_json")
                    metadata = json.loads(meta_json) if isinstance(meta_json, str) else (meta_json or {})
                    if not isinstance(metadata, dict):
                        metadata = {}
                    link_check = metadata.get("link_check") or {}
                    if not isinstance(link_check, dict):
                        link_check = {}
                    run_links = {
                        str(metadata.get("external_product_link") or "").strip(),
                        str(link_check.get("original_link") or "").strip(),
                        str(link_check.get("selected_link") or "").strip(),
                    }
                    run_links = {l for l in run_links if l}
                    if link and run_links and link not in run_links:
                        fallback_row = None
                except Exception:
                    pass
                row = fallback_row
        return _load_run(row) if row else None

    def update_run(self, evaluation_run_id: str, **fields) -> dict[str, Any]:
        if not fields:
            return self.get_run(evaluation_run_id) or {}
        set_parts = ["updated_at=NOW()"]
        args: list[Any] = []
        for key, value in fields.items():
            column = JSON_RUN_FIELDS.get(key, key)
            if column.endswith("_json"):
                value = _dump(value)
            elif key in DATETIME_RUN_FIELDS:
                value = _mysql_datetime(value)
            set_parts.append(f"{column}=%s")
            args.append(value)
        args.append(str(evaluation_run_id))
        execute(
            f"UPDATE ai_evaluation_runs SET {', '.join(set_parts)} WHERE evaluation_run_id=%s",
            tuple(args),
        )
        return self.get_run(evaluation_run_id) or {}

    def upsert_country(self, evaluation_run_id: str, country_code: str, data: dict[str, Any]) -> None:
        full_result = data.get("full_result") or data
        execute(
            "INSERT INTO ai_country_evaluations "
            "(evaluation_run_id, product_id, country_code, country_name, status, "
            " scores_json, decision_json, full_result_json, sources_json, raw_response_json, "
            " metadata_json, error_message, created_at, updated_at, completed_at, failed_at) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NOW(),NOW(),%s,%s) "
            "ON DUPLICATE KEY UPDATE "
            " country_name=VALUES(country_name), status=VALUES(status), scores_json=VALUES(scores_json), "
            " decision_json=VALUES(decision_json), full_result_json=VALUES(full_result_json), "
            " sources_json=VALUES(sources_json), raw_response_json=VALUES(raw_response_json), "
            " metadata_json=VALUES(metadata_json), error_message=VALUES(error_message), "
            " updated_at=NOW(), completed_at=VALUES(completed_at), failed_at=VALUES(failed_at)",
            (
                str(evaluation_run_id),
                int(data.get("product_id") or 0),
                str(country_code).upper(),
                full_result.get("country_name") or "",
                full_result.get("status") or data.get("status") or "pending",
                _dump(full_result.get("scores") or {}),
                _dump(full_result.get("decision") or {}),
                _dump(full_result),
                _dump(full_result.get("sources") or []),
                _dump(data.get("raw_response") or {}),
                _dump(data.get("metadata") or {}),
                data.get("error_message") or (full_result.get("error") or {}).get("message"),
                _utc_now_naive() if full_result.get("status") == "completed" else None,
                _utc_now_naive() if full_result.get("status") == "failed" else None,
            ),
        )

    def list_countries(self, evaluation_run_id: str) -> dict[str, dict[str, Any]]:
        rows = query(
            "SELECT country_code, full_result_json FROM ai_country_evaluations "
            "WHERE evaluation_run_id=%s ORDER BY id ASC",
            (str(evaluation_run_id),),
        )
        out: dict[str, dict[str, Any]] = {}
        for row in rows or []:
            code = str(row.get("country_code") or "").upper()
            out[code] = _load(row.get("full_result_json"), {})
        return out


def _dump(value: Any) -> str:
    return json.dumps(value if value is not None else {}, ensure_ascii=False, default=str)


def _mysql_datetime(value: Any) -> Any:
    if value is None or isinstance(value, datetime):
        parsed = value
    else:
        text = str(value or "").strip()
        if not text:
            return None
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return value
    if parsed is None:
        return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(UTC).replace(tzinfo=None)
    return parsed.replace(microsecond=0)


def _load(value: Any, default: Any) -> Any:
    if value is None:
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(str(value))
    except Exception:
        return default


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _utc_now_naive() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None, microsecond=0)


def _load_run(row: dict[str, Any]) -> dict[str, Any]:
    out = dict(row)
    raw_product_id = out.get("product_id")
    out["product_id"] = "" if raw_product_id is None else str(raw_product_id)
    for public_key, column in JSON_RUN_FIELDS.items():
        out[public_key] = _load(out.get(column), [] if public_key == "countries" else {})
    for key in ("created_at", "updated_at", "completed_at", "failed_at", "started_at"):
        if key in out:
            out[key] = _iso(out.get(key))
    return out
