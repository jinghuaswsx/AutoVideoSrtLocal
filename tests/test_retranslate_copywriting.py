"""tools/retranslate_copywriting 单测。"""
from __future__ import annotations

from tools import retranslate_copywriting as mod


class _FakeDB:
    """统一的 query_one + execute 桩。"""

    def __init__(self, rows: dict[tuple, dict]):
        # rows key 形如 ("by_id", id) 或 ("by_source", product_id, idx)
        self.rows = rows
        self.executes: list[tuple[str, tuple]] = []

    def query_one(self, sql: str, args: tuple):
        sql_upper = sql.upper()
        if "WHERE ID =" in sql_upper:
            return self.rows.get(("by_id", args[0]))
        if "AND LANG = 'EN'" in sql_upper:
            return self.rows.get(("by_source", args[0], args[1]))
        raise AssertionError(f"unexpected query_one: {sql}")

    def execute(self, sql: str, args: tuple):
        self.executes.append((sql, args))
        return 1


def _stub_translate(text: str, src: str, tgt: str) -> tuple[str, int]:
    if not text or not text.strip():
        return "", 0
    # 简单可逆桩：在原文前加 [tgt]，让 diff 一目了然
    return f"[{tgt}] {text}", len(text)


def test_dry_run_does_not_write_db():
    db = _FakeDB({
        ("by_id", 101): {
            "id": 101, "product_id": 7, "lang": "ja", "idx": 1,
            "title": "Old JP", "body": "Old body", "description": "Old desc",
            "ad_carrier": None, "ad_copy": None, "ad_keywords": None,
        },
        ("by_source", 7, 1): {
            "id": 50, "title": "Welcome", "body": "Some body",
            "description": "Tagline",
            "ad_carrier": None, "ad_copy": None, "ad_keywords": None,
        },
    })

    report = mod.retranslate_ids(
        [101],
        query_one=db.query_one,
        execute=db.execute,
        translate_fn=_stub_translate,
        apply=False,
    )
    assert db.executes == []
    assert report["applied"] is False
    assert report["applied_ids"] == []
    assert report["total"] == 1
    item = report["items"][0]
    assert item["status"] == "translated"
    assert item["fields"]["title"] == {"old": "Old JP", "new": "[ja] Welcome"}
    assert item["fields"]["body"] == {"old": "Old body", "new": "[ja] Some body"}
    assert item["fields"]["description"] == {"old": "Old desc", "new": "[ja] Tagline"}


def test_apply_writes_update_with_new_values():
    db = _FakeDB({
        ("by_id", 101): {
            "id": 101, "product_id": 7, "lang": "it", "idx": 1,
            "title": "Old", "body": "Old body", "description": "Old desc",
            "ad_carrier": None, "ad_copy": None, "ad_keywords": None,
        },
        ("by_source", 7, 1): {
            "id": 50, "title": "Welcome", "body": "Some body",
            "description": "Tagline",
            "ad_carrier": None, "ad_copy": None, "ad_keywords": None,
        },
    })

    report = mod.retranslate_ids(
        [101],
        query_one=db.query_one,
        execute=db.execute,
        translate_fn=_stub_translate,
        apply=True,
    )
    assert report["applied"] is True
    assert report["applied_ids"] == [101]
    # 一次 UPDATE 调用，按字段顺序传入新值
    assert len(db.executes) == 1
    sql, args = db.executes[0]
    assert "UPDATE media_copywritings SET" in sql
    assert args == (
        "[it] Welcome",          # title
        "[it] Some body",        # body
        "[it] Tagline",          # description
        None,                    # ad_carrier
        None,                    # ad_copy
        None,                    # ad_keywords
        101,                     # WHERE id = ?
    )


def test_apply_skips_unchanged_rows():
    db = _FakeDB({
        ("by_id", 101): {
            "id": 101, "product_id": 7, "lang": "de", "idx": 1,
            "title": "[de] Welcome", "body": "[de] Some body", "description": "[de] Tagline",
            "ad_carrier": None, "ad_copy": None, "ad_keywords": None,
        },
        ("by_source", 7, 1): {
            "id": 50, "title": "Welcome", "body": "Some body",
            "description": "Tagline",
            "ad_carrier": None, "ad_copy": None, "ad_keywords": None,
        },
    })
    report = mod.retranslate_ids(
        [101],
        query_one=db.query_one,
        execute=db.execute,
        translate_fn=_stub_translate,
        apply=True,
    )
    assert db.executes == []
    assert report["applied_ids"] == []
    assert report["skipped_unchanged_ids"] == [101]


def test_skips_empty_source_fields_without_calling_llm():
    """英文源字段为空 / None / 空白时不调 LLM。"""
    calls: list[tuple] = []

    def counting(text: str, src: str, tgt: str) -> tuple[str, int]:
        calls.append((text, src, tgt))
        return f"[{tgt}] {text}", 1

    db = _FakeDB({
        ("by_id", 101): {
            "id": 101, "product_id": 7, "lang": "ja", "idx": 1,
            "title": "OLD", "body": None, "description": "",
            "ad_carrier": "carrier_old", "ad_copy": None, "ad_keywords": None,
        },
        ("by_source", 7, 1): {
            "id": 50, "title": "Hi", "body": None, "description": "  ",
            "ad_carrier": None, "ad_copy": None, "ad_keywords": None,
        },
    })

    report = mod.retranslate_ids(
        [101],
        query_one=db.query_one,
        execute=db.execute,
        translate_fn=counting,
        apply=True,
    )
    # 只有 title 字段被翻译；body/description/ad_* 在源里是空，不调 LLM。
    assert calls == [("Hi", "en", "ja")]
    sql, args = db.executes[0]
    assert args == (
        "[ja] Hi",     # title 译文
        None,          # body 源 None → target 原值（None）
        "",            # description 源是空白 → target 原值（"")
        "carrier_old", # ad_carrier 源 None → target 原值
        None,          # ad_copy
        None,          # ad_keywords
        101,
    )


def test_missing_target_id_logs_status():
    db = _FakeDB({})  # 空库
    report = mod.retranslate_ids(
        [9999],
        query_one=db.query_one,
        execute=db.execute,
        translate_fn=_stub_translate,
        apply=True,
    )
    assert report["missing_ids"] == [9999]
    assert report["applied_ids"] == []
    assert db.executes == []
    assert report["items"][0]["status"] == "missing"


def test_missing_source_marks_status_without_apply():
    db = _FakeDB({
        ("by_id", 101): {
            "id": 101, "product_id": 7, "lang": "ja", "idx": 1,
            "title": "Old", "body": None, "description": None,
            "ad_carrier": None, "ad_copy": None, "ad_keywords": None,
        },
        # 英文源缺失
    })
    report = mod.retranslate_ids(
        [101],
        query_one=db.query_one,
        execute=db.execute,
        translate_fn=_stub_translate,
        apply=True,
    )
    assert report["applied_ids"] == []
    assert db.executes == []
    assert report["items"][0]["status"] == "missing_source"


def test_parse_args_requires_ids():
    import pytest

    with pytest.raises(SystemExit):
        mod.parse_args(["--apply"])


def test_parse_args_basic():
    args = mod.parse_args(["--ids", "1,2,3", "--apply"])
    assert args.ids == "1,2,3"
    assert args.apply is True


def test_translate_failure_marks_errored_and_continues():
    """单条 id 翻译报错（如 disabled lang）不应中断后续 id。"""
    db = _FakeDB({
        ("by_id", 101): {
            "id": 101, "product_id": 7, "lang": "fi", "idx": 1,
            "title": "Old", "body": None, "description": None,
            "ad_carrier": None, "ad_copy": None, "ad_keywords": None,
        },
        ("by_source", 7, 1): {
            "id": 50, "title": "Hi", "body": None, "description": None,
            "ad_carrier": None, "ad_copy": None, "ad_keywords": None,
        },
        ("by_id", 102): {
            "id": 102, "product_id": 8, "lang": "ja", "idx": 1,
            "title": "Old", "body": None, "description": None,
            "ad_carrier": None, "ad_copy": None, "ad_keywords": None,
        },
        ("by_source", 8, 1): {
            "id": 51, "title": "Bye", "body": None, "description": None,
            "ad_carrier": None, "ad_copy": None, "ad_keywords": None,
        },
    })

    def selective_translate(text: str, src: str, tgt: str) -> tuple[str, int]:
        if tgt == "fi":
            raise ValueError("unsupported language: fi")
        return f"[{tgt}] {text}", 5

    report = mod.retranslate_ids(
        [101, 102],
        query_one=db.query_one,
        execute=db.execute,
        translate_fn=selective_translate,
        apply=True,
    )
    assert report["errored_ids"] == [101]
    assert report["applied_ids"] == [102]
    assert report["items"][0]["status"] == "error"
    assert "unsupported language" in report["items"][0]["error"]
    # 第二条仍正常 UPDATE
    assert len(db.executes) == 1
    assert db.executes[0][1][6] == 102
