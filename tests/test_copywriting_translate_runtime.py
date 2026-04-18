"""copywriting_translate_runtime Task 6 单元测试。

只测 translate_copy_text 这一薄封装层(mock _llm_translate,不打真实 LLM)。
完整 Runner 集成测试在 Task 7 追加。
"""
import pytest


def test_translate_copy_text_empty_returns_empty(monkeypatch):
    """空/空白输入直接返回,不调 LLM。"""
    calls = []
    from appcore import copywriting_translate_runtime as mod
    monkeypatch.setattr(mod, "_llm_translate",
                         lambda *a, **kw: calls.append(a) or ("should_not_reach", 0))

    text, tokens = mod.translate_copy_text("", "en", "de")
    assert text == ""
    assert tokens == 0
    assert calls == []

    text, tokens = mod.translate_copy_text("   ", "en", "de")
    assert text == ""
    assert tokens == 0


def test_translate_copy_text_delegates_to_llm(monkeypatch):
    """非空输入调 _llm_translate,参数/返回值完整透传。"""
    captured = {}

    def fake(source_text, source_lang, target_lang):
        captured.update({
            "text": source_text, "src": source_lang, "tgt": target_lang,
        })
        return "Willkommen zu unserem Produkt", 120

    from appcore import copywriting_translate_runtime as mod
    monkeypatch.setattr(mod, "_llm_translate", fake)

    text, tokens = mod.translate_copy_text("Welcome to our product", "en", "de")
    assert text == "Willkommen zu unserem Produkt"
    assert tokens == 120
    assert captured == {"text": "Welcome to our product", "src": "en", "tgt": "de"}


def test_llm_translate_sums_input_and_output_tokens(monkeypatch):
    """内部 _llm_translate 把 input + output tokens 合成总数。"""
    def fake_translate_text(text, src, tgt, **kw):
        return {"text": "Willkommen", "input_tokens": 40, "output_tokens": 12}

    from appcore import copywriting_translate_runtime as mod
    monkeypatch.setattr(mod, "translate_text", fake_translate_text)

    text, tokens = mod._llm_translate("Welcome", "en", "de")
    assert text == "Willkommen"
    assert tokens == 52   # 40 + 12


def test_llm_translate_handles_missing_token_keys(monkeypatch):
    """pipeline 返回里缺 token 字段也不应崩溃。"""
    def fake_translate_text(text, src, tgt, **kw):
        return {"text": "Willkommen"}

    from appcore import copywriting_translate_runtime as mod
    monkeypatch.setattr(mod, "translate_text", fake_translate_text)

    text, tokens = mod._llm_translate("Welcome", "en", "de")
    assert text == "Willkommen"
    assert tokens == 0
