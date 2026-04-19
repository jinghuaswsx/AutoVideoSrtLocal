"""
声音仓库浏览 service 单元测试。

参考 `tests/test_appcore_medias_multi_lang.py` 的 mock 风格：
直接 patch `appcore.voice_library_browse.query` / `query_one` 模拟 DB。
"""
from __future__ import annotations

import json
import pytest
from unittest.mock import patch


# -----------------------------
# 辅助：统一 mock 出 query/query_one
# -----------------------------


class _DBCapture:
    """记录每次 query/query_one 的 sql / args，供断言使用。"""

    def __init__(self, *, rows=None, total=0):
        self.rows = rows if rows is not None else []
        self.total = total
        self.query_calls: list[tuple[str, tuple]] = []
        self.query_one_calls: list[tuple[str, tuple]] = []

    def query(self, sql, args=()):
        self.query_calls.append((sql, tuple(args) if args else ()))
        return self.rows

    def query_one(self, sql, args=()):
        self.query_one_calls.append((sql, tuple(args) if args else ()))
        if "COUNT(" in sql.upper():
            return {"c": self.total}
        return None


# -----------------------------
# list_voices
# -----------------------------


def test_language_required():
    from appcore import voice_library_browse
    with pytest.raises(ValueError):
        voice_library_browse.list_voices(language="")
    with pytest.raises(ValueError):
        voice_library_browse.list_voices(language=None)


def test_filter_by_language_and_gender():
    from appcore import voice_library_browse
    cap = _DBCapture(rows=[], total=0)
    with patch("appcore.voice_library_browse.query", side_effect=cap.query), \
         patch("appcore.voice_library_browse.query_one", side_effect=cap.query_one):
        result = voice_library_browse.list_voices(language="en", gender="female")

    assert result["total"] == 0
    assert result["items"] == []
    # COUNT 与 SELECT 都应该有 language = %s AND gender = %s
    count_sql = cap.query_one_calls[0][0]
    count_args = cap.query_one_calls[0][1]
    assert "language = %s" in count_sql
    assert "gender = %s" in count_sql
    # language 参数在前，gender 紧随其后
    assert count_args[:2] == ("en", "female")

    select_sql = cap.query_calls[0][0]
    select_args = cap.query_calls[0][1]
    assert "language = %s" in select_sql
    assert "gender = %s" in select_sql
    assert "FROM elevenlabs_voices" in select_sql
    assert "LIMIT %s OFFSET %s" in select_sql
    assert select_args[:2] == ("en", "female")


def test_multi_select_use_case():
    from appcore import voice_library_browse
    cap = _DBCapture(rows=[], total=0)
    with patch("appcore.voice_library_browse.query", side_effect=cap.query), \
         patch("appcore.voice_library_browse.query_one", side_effect=cap.query_one):
        voice_library_browse.list_voices(
            language="en",
            use_cases=["narration", "characters"],
        )

    count_sql = cap.query_one_calls[0][0]
    count_args = cap.query_one_calls[0][1]
    # use_case 已迁到独立列，不再走 JSON_EXTRACT
    assert "use_case IN (%s,%s)" in count_sql
    assert "JSON_EXTRACT(labels_json, '$.use_case')" not in count_sql
    # 参数顺序：language, narration, characters
    assert count_args == ("en", "narration", "characters")


def test_multi_select_accent_age_descriptive():
    from appcore import voice_library_browse
    cap = _DBCapture(rows=[], total=0)
    with patch("appcore.voice_library_browse.query", side_effect=cap.query), \
         patch("appcore.voice_library_browse.query_one", side_effect=cap.query_one):
        voice_library_browse.list_voices(
            language="en",
            accents=["american"],
            ages=["middle-aged"],
            descriptives=["warm"],
        )

    count_sql = cap.query_one_calls[0][0]
    count_args = cap.query_one_calls[0][1]
    assert "JSON_UNQUOTE(JSON_EXTRACT(labels_json, '$.accent')) IN (%s)" in count_sql
    assert "JSON_UNQUOTE(JSON_EXTRACT(labels_json, '$.age')) IN (%s)" in count_sql
    assert (
        "JSON_UNQUOTE(JSON_EXTRACT(labels_json, '$.descriptive')) IN (%s)"
        in count_sql
    )
    assert count_args == ("en", "american", "middle-aged", "warm")


def test_search_q_matches_name():
    from appcore import voice_library_browse
    cap = _DBCapture(rows=[], total=0)
    with patch("appcore.voice_library_browse.query", side_effect=cap.query), \
         patch("appcore.voice_library_browse.query_one", side_effect=cap.query_one):
        voice_library_browse.list_voices(language="en", q="rachel")

    count_sql = cap.query_one_calls[0][0]
    count_args = cap.query_one_calls[0][1]
    assert "name LIKE %s" in count_sql
    assert "descriptive LIKE %s" in count_sql
    assert count_args == ("en", "%rachel%", "%rachel%")


def test_pagination():
    from appcore import voice_library_browse
    # 模拟总数 100，返回 3 行
    fake_rows = [
        {
            "voice_id": f"v{i}",
            "name": f"N{i}",
            "gender": "female",
            "language": "en",
            "age": None,
            "accent": None,
            "category": "professional",
            "descriptive": None,
            "preview_url": None,
            "labels_json": None,
        }
        for i in range(3)
    ]
    cap = _DBCapture(rows=fake_rows, total=100)
    with patch("appcore.voice_library_browse.query", side_effect=cap.query), \
         patch("appcore.voice_library_browse.query_one", side_effect=cap.query_one):
        result = voice_library_browse.list_voices(
            language="en", page=2, page_size=3
        )

    assert result["total"] == 100
    assert result["page"] == 2
    assert result["page_size"] == 3
    assert len(result["items"]) == 3

    select_sql = cap.query_calls[0][0]
    select_args = cap.query_calls[0][1]
    # 最后两个参数应为 LIMIT / OFFSET
    assert select_args[-2:] == (3, 3)  # page_size=3, offset=(2-1)*3=3
    assert "LIMIT %s OFFSET %s" in select_sql
    assert "ORDER BY" in select_sql
    assert "category='professional'" in select_sql


def test_page_and_size_clamp():
    from appcore import voice_library_browse
    cap = _DBCapture(rows=[], total=0)
    with patch("appcore.voice_library_browse.query", side_effect=cap.query), \
         patch("appcore.voice_library_browse.query_one", side_effect=cap.query_one):
        # page=0 会被 clamp 到 1；page_size=500 会被 clamp 到 200；page_size=0 clamp 到 1
        res = voice_library_browse.list_voices(
            language="en", page=0, page_size=500
        )
        assert res["page"] == 1
        assert res["page_size"] == 200


def test_row_to_dict_parses_labels_json_string():
    from appcore import voice_library_browse
    labels = {"use_case": "narration", "description": "calm and warm"}
    fake_rows = [{
        "voice_id": "v1",
        "name": "N1",
        "gender": "female",
        "language": "en",
        "age": "middle-aged",
        "accent": "american",
        "category": "professional",
        "descriptive": "warm",
        "preview_url": "http://x",
        "labels_json": json.dumps(labels),  # 字符串形式（pymysql 默认）
    }]
    cap = _DBCapture(rows=fake_rows, total=1)
    with patch("appcore.voice_library_browse.query", side_effect=cap.query), \
         patch("appcore.voice_library_browse.query_one", side_effect=cap.query_one):
        res = voice_library_browse.list_voices(language="en")
    item = res["items"][0]
    assert item["labels"] == labels
    assert item["use_case"] == "narration"
    assert item["description"] == "calm and warm"
    assert "labels_json" not in item


def test_row_to_dict_accepts_dict_labels_json():
    """当 DB driver 直接返回 dict（某些 MySQL JSON 列场景）时也要兼容。"""
    from appcore import voice_library_browse
    labels = {"use_case": "characters", "description": "bright"}
    fake_rows = [{
        "voice_id": "v2",
        "name": "N2",
        "gender": "male",
        "language": "en",
        "age": None,
        "accent": None,
        "category": "generated",
        "descriptive": None,
        "preview_url": None,
        "labels_json": labels,  # 直接是 dict
    }]
    cap = _DBCapture(rows=fake_rows, total=1)
    with patch("appcore.voice_library_browse.query", side_effect=cap.query), \
         patch("appcore.voice_library_browse.query_one", side_effect=cap.query_one):
        res = voice_library_browse.list_voices(language="en")
    item = res["items"][0]
    assert item["labels"] == labels
    assert item["use_case"] == "characters"
    assert item["description"] == "bright"


def test_row_to_dict_falls_back_to_descriptive_when_no_description():
    from appcore import voice_library_browse
    fake_rows = [{
        "voice_id": "v3",
        "name": "N3",
        "gender": "female",
        "language": "en",
        "age": None,
        "accent": None,
        "category": "professional",
        "descriptive": "fallback-desc",
        "preview_url": None,
        "labels_json": "{}",
    }]
    cap = _DBCapture(rows=fake_rows, total=1)
    with patch("appcore.voice_library_browse.query", side_effect=cap.query), \
         patch("appcore.voice_library_browse.query_one", side_effect=cap.query_one):
        res = voice_library_browse.list_voices(language="en")
    assert res["items"][0]["description"] == "fallback-desc"


# -----------------------------
# list_filter_options
# -----------------------------


def test_list_filter_options_language_required():
    from appcore import voice_library_browse
    with pytest.raises(ValueError):
        voice_library_browse.list_filter_options(language="")


def test_list_filter_options_returns_sorted_unique():
    from appcore import voice_library_browse

    # labels_json 行（use_case 已迁到独立列，这里只覆盖 accent/age/descriptive）
    labels_rows = [
        {"labels_json": json.dumps({
            "accent": "american", "age": "middle-aged", "descriptive": "warm",
        })},
        {"labels_json": json.dumps({
            "accent": "british", "age": "young", "descriptive": "bright",
        })},
        # 重复项：应该被去重
        {"labels_json": json.dumps({
            "accent": "american", "age": "middle-aged", "descriptive": "warm",
        })},
        # dict 形式（兼容 DB driver 自动解析）
        {"labels_json": {
            "accent": "american", "age": "old", "descriptive": "deep",
        }},
        # 非法 / 空值
        {"labels_json": None},
        {"labels_json": "not-json"},
    ]
    # use_case 列直接返回去重后的值
    use_case_rows = [
        {"use_case": "narration"},
        {"use_case": "characters"},
        {"use_case": "advertisement"},
    ]

    def fake_query(sql, args=()):
        if "DISTINCT use_case" in sql:
            return use_case_rows
        return labels_rows

    with patch("appcore.voice_library_browse.query", side_effect=fake_query):
        opts = voice_library_browse.list_filter_options(language="en")

    assert opts["use_cases"] == sorted({"narration", "characters", "advertisement"})
    assert opts["accents"] == sorted({"american", "british"})
    assert opts["ages"] == sorted({"middle-aged", "young", "old"})
    assert opts["descriptives"] == sorted({"warm", "bright", "deep"})


def test_list_voices_filters_use_case_via_column(monkeypatch):
    """use_cases 过滤应使用独立列 use_case = %s 而非 JSON_EXTRACT。"""
    from appcore import voice_library_browse as vlb
    captured = {}

    def fake_query_one(sql, params):
        captured["count_sql"] = sql
        captured["count_params"] = params
        return {"c": 0}

    def fake_query(sql, params):
        captured["list_sql"] = sql
        captured["list_params"] = params
        return []

    monkeypatch.setattr(vlb, "query_one", fake_query_one)
    monkeypatch.setattr(vlb, "query", fake_query)

    vlb.list_voices(language="en", use_cases=["news", "narration"])

    assert "use_case IN" in captured["count_sql"]
    assert "JSON_EXTRACT(labels_json, '$.use_case')" not in captured["count_sql"]
    assert "news" in captured["count_params"]
    assert "narration" in captured["count_params"]


def test_list_filter_options_use_cases_via_distinct_column(monkeypatch):
    from appcore import voice_library_browse as vlb
    captured = {}

    def fake_query(sql, params):
        captured.setdefault("sqls", []).append(sql)
        if "DISTINCT use_case" in sql:
            return [{"use_case": "news"}, {"use_case": "narration"}]
        return []

    monkeypatch.setattr(vlb, "query", fake_query)

    result = vlb.list_filter_options(language="en")
    assert any("DISTINCT use_case" in s for s in captured["sqls"])
    assert result["use_cases"] == ["narration", "news"]


def test_row_to_dict_uses_column_use_case_over_labels(monkeypatch):
    """_row_to_dict 读到 row 里的 use_case 独立列时优先于 labels_json。"""
    from appcore import voice_library_browse as vlb
    row = {
        "voice_id": "v1", "name": "x", "gender": "male", "language": "en",
        "age": None, "accent": None, "category": None, "descriptive": "",
        "preview_url": "", "use_case": "news",
        "labels_json": '{"use_case": "ignored"}',
    }
    out = vlb._row_to_dict(row)
    assert out["use_case"] == "news"
