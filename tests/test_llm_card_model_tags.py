from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_japanese_translate_runtime_does_not_use_use_case_as_model_tag():
    source = (ROOT / "appcore/runtime_ja.py").read_text(encoding="utf-8")

    assert 'model_tag="ja_translate.localize"' not in source
    assert "resolve_use_case_provider_model" in source
    assert 'f"{ja_provider} · {ja_model}"' in source


def test_multi_japanese_translate_debug_refs_use_resolved_provider_model():
    source = (ROOT / "appcore/runtime_multi.py").read_text(encoding="utf-8")

    assert 'model_tag="ja_translate.localize"' not in source
    assert 'provider="ja_translate.localize"' not in source
    assert '"provider": "ja_translate.localize"' not in source
    assert 'model="ja_translate.localize"' not in source
    assert '"model": "ja_translate.localize"' not in source
    assert 'ja_provider, ja_model = resolve_use_case_provider_model("ja_translate.localize")' in source
    assert 'model_tag=f"{ja_provider} · {ja_model}"' in source
