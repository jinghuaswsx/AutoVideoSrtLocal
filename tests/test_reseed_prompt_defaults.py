"""Block1: reseed_prompt_defaults 工具单元测试。
Spec: docs/superpowers/specs/2026-06-12-omni-quality-block1-prompt-correctness-design.md
"""
from unittest.mock import patch
from scripts.reseed_prompt_defaults import diff_defaults, apply_defaults


def test_diff_reports_same_diff_missing():
    fake_defaults = {
        ("base_translation", "en"): {"provider": "p", "model": "m", "content": "NEW"},
        ("base_translation", "de"): {"provider": "p", "model": "m", "content": "X"},
    }
    def fake_get_one(slot, lang):
        if lang == "en":
            return {"content": "OLD", "model_provider": "p", "model_name": "m"}
        return None
    with patch("scripts.reseed_prompt_defaults.DEFAULTS", fake_defaults), \
         patch("scripts.reseed_prompt_defaults.get_one", side_effect=fake_get_one):
        rows = diff_defaults()
    status = {(r["slot"], r["lang"]): r["status"] for r in rows}
    assert status[("base_translation", "en")] == "DIFF"
    assert status[("base_translation", "de")] == "MISSING"


def test_apply_upserts_filtered_rows():
    fake_defaults = {("base_rewrite", "it"): {"provider": "p", "model": "m", "content": "C"}}
    with patch("scripts.reseed_prompt_defaults.DEFAULTS", fake_defaults), \
         patch("scripts.reseed_prompt_defaults.upsert") as up:
        n = apply_defaults(slot="base_rewrite", lang="it")
    assert n == 1
    up.assert_called_once()
