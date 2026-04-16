"""
声音仓库浏览服务：查询 elevenlabs_voices 表，支持筛选 / 分页 / 枚举。

职责：
- `list_voices(...)`：按语种 + 性别 + 多选 label（use_case/accent/age/descriptive）
  + 关键字搜索（name/descriptive）+ 分页，返回 {total, page, page_size, items}。
- `list_filter_options(...)`：遍历某语种下所有声音的 labels_json，聚合
  use_case / accent / age / descriptive 的去重排序枚举。

注意：
- 所有 SQL 参数均通过占位符传入，不做字符串拼接。
- `labels_json` 列在不同 MySQL 驱动下可能返回 str 或已解析的 dict，两种都要兼容。
"""
from __future__ import annotations

import json
from typing import Optional

from appcore.db import query, query_one


_SELECT_FIELDS = (
    "voice_id, name, gender, language, age, accent, category, "
    "descriptive, preview_url, labels_json"
)


def _parse_labels(raw) -> dict:
    """兼容 str / dict / None，解析失败返回 {}。"""
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {}
        except (json.JSONDecodeError, TypeError, ValueError):
            return {}
    return {}


def _row_to_dict(row: dict) -> dict:
    labels = _parse_labels(row.get("labels_json"))
    out = dict(row)
    out["labels"] = labels
    out.pop("labels_json", None)
    out["use_case"] = labels.get("use_case")
    out["description"] = labels.get("description") or row.get("descriptive") or ""
    return out


def list_voices(
    *,
    language: str,
    gender: Optional[str] = None,
    use_cases: Optional[list[str]] = None,
    accents: Optional[list[str]] = None,
    ages: Optional[list[str]] = None,
    descriptives: Optional[list[str]] = None,
    q: Optional[str] = None,
    page: int = 1,
    page_size: int = 48,
) -> dict:
    if not language:
        raise ValueError("language is required")
    page = max(1, int(page))
    page_size = max(1, min(200, int(page_size)))

    where = ["language = %s"]
    params: list = [language]

    if gender in ("male", "female"):
        where.append("gender = %s")
        params.append(gender)

    def _json_in(field: str, values: list[str]) -> None:
        marks = ",".join(["%s"] * len(values))
        where.append(
            f"JSON_UNQUOTE(JSON_EXTRACT(labels_json, '$.{field}')) IN ({marks})"
        )
        params.extend(values)

    if use_cases:
        _json_in("use_case", use_cases)
    if accents:
        _json_in("accent", accents)
    if ages:
        _json_in("age", ages)
    if descriptives:
        _json_in("descriptive", descriptives)

    if q:
        like = f"%{q}%"
        where.append("(name LIKE %s OR descriptive LIKE %s)")
        params.extend([like, like])

    where_sql = " AND ".join(where)

    total_row = query_one(
        f"SELECT COUNT(*) AS c FROM elevenlabs_voices WHERE {where_sql}",
        tuple(params),
    )
    total = int(total_row["c"]) if total_row else 0

    offset = (page - 1) * page_size
    rows = query(
        f"SELECT {_SELECT_FIELDS} FROM elevenlabs_voices "
        f"WHERE {where_sql} "
        f"ORDER BY (category='professional') DESC, synced_at DESC, voice_id ASC "
        f"LIMIT %s OFFSET %s",
        tuple(params) + (page_size, offset),
    )

    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": [_row_to_dict(r) for r in rows],
    }


def list_filter_options(*, language: str) -> dict:
    """返回某语种下所有声音的 label 枚举（去重 + 升序）。"""
    if not language:
        raise ValueError("language is required")

    rows = query(
        "SELECT labels_json FROM elevenlabs_voices WHERE language = %s",
        (language,),
    )

    use_cases: set[str] = set()
    accents: set[str] = set()
    ages: set[str] = set()
    descriptives: set[str] = set()

    for r in rows:
        labels = _parse_labels(r.get("labels_json"))
        v = labels.get("use_case")
        if v:
            use_cases.add(v)
        v = labels.get("accent")
        if v:
            accents.add(v)
        v = labels.get("age")
        if v:
            ages.add(v)
        v = labels.get("descriptive")
        if v:
            descriptives.add(v)

    return {
        "use_cases": sorted(use_cases),
        "accents": sorted(accents),
        "ages": sorted(ages),
        "descriptives": sorted(descriptives),
    }
