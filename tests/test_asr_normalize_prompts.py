"""pipeline.languages.prompt_defaults 中 4 条 asr_normalize.* prompt 守护测试。"""
from pipeline.languages.prompt_defaults import DEFAULTS


def test_four_new_prompts_registered_with_empty_lang_key():
    for slot in (
        "asr_normalize.detect",
        "asr_normalize.translate_zh_en",
        "asr_normalize.translate_es_en",
        "asr_normalize.translate_generic_en",
    ):
        assert (slot, "") in DEFAULTS, f"missing default prompt: ({slot!r}, '')"


def test_detect_prompt_includes_supported_lang_enum():
    content = DEFAULTS[("asr_normalize.detect", "")]["content"]
    # 必须告诉模型可选 enum 包含全部 10 种白名单 + other
    for code in ("en", "zh", "es", "pt", "fr", "it", "ja", "nl", "sv", "fi", "other"):
        assert f'"{code}"' in content, f"detect prompt missing language code {code!r}"
    # 必须明确 JSON 输出 schema 字段
    for field in ("language", "confidence", "is_mixed"):
        assert field in content


def test_es_translate_prompt_includes_en_us_vocab_anchors():
    content = DEFAULTS[("asr_normalize.translate_es_en", "")]["content"]
    # 必须含 en-US 反翻译陷阱锚点
    for token in ("sneakers", "apartment", "elevator"):
        assert token in content
    # 必须明确 1:1 映射要求
    assert "1:1 mapping by index" in content
    # 必须明确 ASCII 标点要求
    assert "ASCII punctuation only" in content


def test_generic_translate_prompt_handles_is_mixed_and_low_confidence_flags():
    content = DEFAULTS[("asr_normalize.translate_generic_en", "")]["content"]
    assert "is_mixed" in content
    assert "low_confidence" in content
    assert "1:1 mapping by index" in content
    assert "ASCII punctuation only" in content


def test_zh_translate_prompt_keeps_us_voice():
    """zh→en prompt 注册保留（runner 当前不路由），仅校验最低结构性关键词存在。"""
    content = DEFAULTS[("asr_normalize.translate_zh_en", "")]["content"]
    assert "1:1 mapping by index" in content
    assert "en-US" in content
