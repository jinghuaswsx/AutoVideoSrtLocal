"""tools/audit_copywriting_translation 单测：规则、LLM 解析、报告组装。"""
from __future__ import annotations

import json

import pytest

from tools import audit_copywriting_translation as mod


# ----------------------------------------------------------------------
# parse_block / 工具函数
# ----------------------------------------------------------------------

def test_parse_block_three_line_chinese_labels():
    raw = "标题: Hi\n文案: Body text\n描述: Tagline"
    parsed = mod.parse_block(raw)
    assert parsed == {"title": "Hi", "body": "Body text", "description": "Tagline"}


def test_parse_block_returns_none_for_plain_text():
    assert mod.parse_block("Just a single English line") is None
    assert mod.parse_block("") is None
    assert mod.parse_block(None) is None


def test_parse_block_handles_localized_labels():
    raw = "Titel: Hallo\nText: Body\nBeschreibung: Tagline"
    parsed = mod.parse_block(raw)
    assert parsed == {"title": "Hallo", "body": "Body", "description": "Tagline"}


# ----------------------------------------------------------------------
# regex_audit
# ----------------------------------------------------------------------

def test_regex_audit_detects_nested_japanese_label():
    """复现 ja 实际 bug：标题: 標題: ...。"""
    row = {
        "t_title": None,
        "t_body": "标题: 標題: What's on Your Keychain?\n文案: 私のは...\n描述: ...",
        "t_description": None,
        "s_body": "标题: What's on Your Keychain?\n文案: Mine opens bottles.\n描述: Discover the 3-in-1",
    }
    hits = mod.regex_audit(row)
    assert "A:body" in hits


def test_regex_audit_detects_nested_italian_label():
    row = {
        "t_body": "标题: Titolo: Pronti\n文案: Testo: Vai\n描述: Descrizione: Avanti",
        "s_body": "标题: Ready\n文案: Go\n描述: Forward",
    }
    hits = mod.regex_audit(row)
    assert "A:body" in hits


def test_regex_audit_detects_translated_slot_equals_source():
    """三段块槽位与英文源完全相同（如 it 标题留英文）。"""
    row = {
        "t_body": "标题: What's on Your Keychain?\n文案: Il mio apre bottiglie.\n描述: Scopri il 3 in 1",
        "s_body": "标题: What's on Your Keychain?\n文案: Mine opens bottles.\n描述: Discover the 3-in-1",
    }
    hits = mod.regex_audit(row)
    assert "B:body.title" in hits
    assert "B:body.body" not in hits
    assert "B:body.description" not in hits


def test_regex_audit_detects_single_field_column_unchanged():
    """单字段列（如 title 列）与英文源完全相同。"""
    row = {
        "t_title": "Welcome",
        "s_title": "Welcome",
        "t_body": None,
        "s_body": None,
    }
    hits = mod.regex_audit(row)
    assert "C:title" in hits


def test_regex_audit_clean_translation_returns_no_hits():
    row = {
        "t_title": "Bienvenue",
        "s_title": "Welcome",
        "t_body": "标题: Qu'y a-t-il sur ton porte-clés ?\n文案: Le mien ouvre.\n描述: Découvrez",
        "s_body": "标题: What's on Your Keychain?\n文案: Mine opens.\n描述: Discover",
        "t_description": None,
        "s_description": None,
    }
    assert mod.regex_audit(row) == []


def test_regex_audit_ignores_placeholder_dashes():
    """三段块槽位用 "-" 占位时不应触发 Rule B（来自 wrap-block 的占位符）。"""
    row = {
        "t_body": "标题: Welkom\n文案: -\n描述: -",
        "s_body": "标题: Welcome\n文案: -\n描述: -",
    }
    hits = mod.regex_audit(row)
    assert all(not h.startswith("B:body.body") for h in hits)
    assert all(not h.startswith("B:body.description") for h in hits)


# ----------------------------------------------------------------------
# parse_audit_verdict
# ----------------------------------------------------------------------

def test_parse_audit_verdict_clean_json():
    raw = '{"verdict": "不符合", "reason": "标题保留英文"}'
    assert mod.parse_audit_verdict(raw) == {"verdict": "不符合", "reason": "标题保留英文"}


def test_parse_audit_verdict_strips_markdown_fence():
    raw = '```json\n{"verdict": "符合", "reason": "全部翻译"}\n```'
    assert mod.parse_audit_verdict(raw) == {"verdict": "符合", "reason": "全部翻译"}


def test_parse_audit_verdict_falls_back_to_regex_on_malformed():
    raw = 'Some prefix garbage "verdict": "不符合", "reason": "标题英文"'
    out = mod.parse_audit_verdict(raw)
    assert out["verdict"] == "不符合"
    assert "标题英文" in out["reason"]


def test_parse_audit_verdict_unknown_when_no_signal():
    out = mod.parse_audit_verdict("haha 我不知道")
    assert out["verdict"] == "未知"


# ----------------------------------------------------------------------
# build_audit_prompt
# ----------------------------------------------------------------------

def test_build_audit_prompt_includes_lang_name_and_payload():
    prompt = mod.build_audit_prompt(
        "ja",
        source_en="标题: What's on Your Keychain?",
        translated="标题: 標題: What's on Your Keychain?",
    )
    assert "日语" in prompt
    assert "What's on Your Keychain?" in prompt
    assert "標題:" in prompt
    # 必须明确禁止三种语言的本地化标签
    assert "タイトル" in prompt
    assert "Titolo" in prompt


# ----------------------------------------------------------------------
# audit_rows（端到端，mock LLM）
# ----------------------------------------------------------------------

@pytest.fixture
def sample_rows():
    return [
        # ja 嵌套标签 + 英文残留 → 规则命中 → LLM 不符合
        {
            "id": 1, "product_id": 100, "lang": "ja", "idx": 1,
            "product_code": "p1", "product_name": "钥匙扣",
            "t_title": None,
            "t_body": "标题: 標題: What's on Your Keychain?\n文案: 私のは...\n描述: 3-in-1",
            "t_description": None,
            "t_ad_carrier": None, "t_ad_copy": None, "t_ad_keywords": None,
            "s_title": None,
            "s_body": "标题: What's on Your Keychain?\n文案: Mine opens.\n描述: Discover",
            "s_description": None,
            "s_ad_carrier": None, "s_ad_copy": None, "s_ad_keywords": None,
        },
        # it 标题留英文 → 规则命中 → LLM 不符合
        {
            "id": 2, "product_id": 100, "lang": "it", "idx": 1,
            "product_code": "p1", "product_name": "钥匙扣",
            "t_title": None,
            "t_body": "标题: What's on Your Keychain?\n文案: Il mio apre.\n描述: Scopri il 3 in 1",
            "t_description": None,
            "t_ad_carrier": None, "t_ad_copy": None, "t_ad_keywords": None,
            "s_title": None,
            "s_body": "标题: What's on Your Keychain?\n文案: Mine opens.\n描述: Discover the 3-in-1",
            "s_description": None,
            "s_ad_carrier": None, "s_ad_copy": None, "s_ad_keywords": None,
        },
        # de 干净翻译 → 规则不命中
        {
            "id": 3, "product_id": 200, "lang": "ja", "idx": 1,
            "product_code": "p2", "product_name": "另一款",
            "t_title": None,
            "t_body": "标题: あなたのキーホルダーには？\n文案: 僕のは開ける.\n描述: 3-in-1を発見",
            "t_description": None,
            "t_ad_carrier": None, "t_ad_copy": None, "t_ad_keywords": None,
            "s_title": None,
            "s_body": "标题: What's on Your Keychain?\n文案: Mine opens.\n描述: Discover",
            "s_description": None,
            "s_ad_carrier": None, "s_ad_copy": None, "s_ad_keywords": None,
        },
    ]


def test_audit_rows_full_pipeline(sample_rows):
    """规则命中 + LLM 复核 → 最终重译清单只含 LLM 不符合的行。"""

    def fake_invoke(use_case_code, messages, **kwargs):
        assert use_case_code == "copywriting_translate.audit"
        prompt = messages[0]["content"]
        # ja 行 1: 包含 标題: → 不符合
        if "標題:" in prompt:
            return {"text": '{"verdict": "不符合", "reason": "标题嵌套日语标签"}'}
        # it 行: 标题留英文 → 不符合
        if "What's on Your Keychain?" in prompt and "Il mio" in prompt:
            return {"text": '{"verdict": "不符合", "reason": "标题保留英文"}'}
        return {"text": '{"verdict": "符合", "reason": "翻译完整"}'}

    report = mod.audit_rows(sample_rows, invoke_chat=fake_invoke, concurrency=2)

    assert report["stats"]["ja"]["total"] == 2
    assert report["stats"]["it"]["total"] == 1
    # 只有前两条规则命中（id=1, id=2）。第三条 ja 干净翻译，规则不命中。
    assert report["summary"]["regex_hits_total"] == 2
    # 两条都被 LLM 判"不符合"。
    assert report["summary"]["llm_confirmed_broken_total"] == 2
    assert sorted(report["retranslate_ids"]) == [1, 2]
    assert report["retranslate_count"] == 2

    items_by_id = {it["id"]: it for it in report["items"]}
    assert items_by_id[1]["verdict"] == "不符合"
    assert items_by_id[2]["verdict"] == "不符合"
    assert "A:body" in items_by_id[1]["rules"]
    assert "B:body.title" in items_by_id[2]["rules"]


def test_audit_rows_skip_llm_when_no_regex_hit(sample_rows):
    """规则不命中的行不应触发 LLM 调用。"""
    calls = []

    def counting_invoke(use_case_code, messages, **kwargs):
        calls.append(messages[0]["content"][:30])
        return {"text": '{"verdict": "符合", "reason": "ok"}'}

    # 只取干净行
    clean_only = [r for r in sample_rows if r["id"] == 3]
    report = mod.audit_rows(clean_only, invoke_chat=counting_invoke, concurrency=1)

    assert calls == []
    assert report["summary"]["regex_hits_total"] == 0
    assert report["summary"]["llm_confirmed_broken_total"] == 0
    assert report["retranslate_ids"] == []


def test_audit_rows_llm_disagreement_keeps_row_out_of_retranslate(sample_rows):
    """规则命中但 LLM 判"符合"时 → 不进重译清单（避免误伤）。"""

    def disagree_invoke(*args, **kwargs):
        return {"text": '{"verdict": "符合", "reason": "误报"}'}

    report = mod.audit_rows(sample_rows, invoke_chat=disagree_invoke, concurrency=2)
    assert report["summary"]["regex_hits_total"] == 2
    assert report["summary"]["llm_confirmed_broken_total"] == 0
    assert report["summary"]["llm_disagreed_total"] == 2
    assert report["retranslate_ids"] == []


def test_audit_rows_llm_failure_marks_unknown(sample_rows):
    def failing_invoke(*args, **kwargs):
        raise RuntimeError("openrouter 502")

    report = mod.audit_rows(sample_rows, invoke_chat=failing_invoke, concurrency=2)
    assert report["summary"]["llm_unknown_total"] == 2
    assert report["retranslate_ids"] == []


def test_audit_rows_no_llm_returns_only_regex(sample_rows):
    report = mod.audit_rows(sample_rows, invoke_chat=None, concurrency=2)
    assert report["summary"]["regex_hits_total"] == 2
    assert report["summary"]["llm_confirmed_broken_total"] == 0
    # 没跑 LLM → verdict 都是"未知" → 不进重译
    assert report["retranslate_ids"] == []


# ----------------------------------------------------------------------
# fetch_rows（mock query_all）
# ----------------------------------------------------------------------

def test_fetch_rows_filters_by_langs_and_limit():
    captured = {}

    def fake_query_all(sql, args):
        captured["sql"] = sql
        captured["args"] = args
        # 返回 ja x3 + it x2
        return [
            {"id": i, "lang": "ja"} for i in (1, 2, 3)
        ] + [
            {"id": i, "lang": "it"} for i in (4, 5)
        ]

    rows = mod.fetch_rows(fake_query_all, ["ja", "it"], limit_per_lang=2)
    assert {r["id"] for r in rows} == {1, 2, 4, 5}
    assert "%s,%s" in captured["sql"]
    assert captured["args"] == ("ja", "it")


def test_fetch_rows_no_limit_returns_all():
    def fake_query_all(sql, args):
        return [{"id": 1, "lang": "ja"}, {"id": 2, "lang": "ja"}]

    rows = mod.fetch_rows(fake_query_all, ["ja"], limit_per_lang=None)
    assert len(rows) == 2
