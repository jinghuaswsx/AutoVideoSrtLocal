import pytest


def _mock_languages(monkeypatch, rows):
    import appcore.medias as medias

    monkeypatch.setattr(medias, "list_languages", lambda: rows)


def _assert_structured_prompt(prompt):
    assert "标题:" in prompt
    assert "文案:" in prompt
    assert "描述:" in prompt
    assert "标题:[...]" not in prompt
    assert "文案:[...]" not in prompt
    assert "描述:[...]" not in prompt
    assert "方括号" in prompt
    assert "不允许" in prompt and "保留" in prompt and "英文" in prompt
    assert "- 标题最多 100 个字符。" in prompt
    assert "- 文案最多 200 个字符。" in prompt
    assert "- 描述最多 50 个字符。" in prompt
    assert "{{SOURCE_TEXT}}" in prompt
    assert prompt.count("{{SOURCE_TEXT}}") == 1
    # 防 Japanese「標題: ...」复发：行首三段中文标签必须强制保留为中文，
    # 并且明示禁止把它们改写成 Title/Titel/Titolo/Título/Titre/タイトル/標題 等形式。
    assert "逐字保留" in prompt
    for forbidden_label in ("Title", "Titel", "Titolo", "Título", "Titre", "タイトル", "標題"):
        assert forbidden_label in prompt, f"prompt 必须显式禁止 {forbidden_label!r} 这种本地化标签"
    # 必须显式禁止「冒号后再加字段名前缀」这种 Japanese 实际复现的双标签格式。
    assert "字段名前缀" in prompt


def test_list_title_translate_languages_filters_out_en_and_disabled(monkeypatch):
    from appcore import title_translate_settings as tts

    _mock_languages(
        monkeypatch,
        [
            {"code": "en", "name_zh": "英语", "enabled": 1},
            {"code": "de", "name_zh": "德语", "enabled": 1},
            {"code": "fr", "name_zh": "法语", "enabled": 0},
            {"code": "nl", "name_zh": "荷兰语", "enabled": True},
        ],
    )

    langs = tts.list_title_translate_languages()
    assert [lang["code"] for lang in langs] == ["de", "nl"]


def test_get_title_translate_language_rejects_en_unknown_and_disabled(monkeypatch):
    from appcore import title_translate_settings as tts

    _mock_languages(
        monkeypatch,
        [
            {"code": "en", "name_zh": "英语", "enabled": 1},
            {"code": "de", "name_zh": "德语", "enabled": 1},
            {"code": "nl", "name_zh": "荷兰语", "enabled": 0},
        ],
    )

    assert tts.get_title_translate_language("  DE ").get("code") == "de"

    with pytest.raises(ValueError):
        tts.get_title_translate_language("en")
    with pytest.raises(ValueError):
        tts.get_title_translate_language("xx")
    with pytest.raises(ValueError):
        tts.get_title_translate_language("nl")


def test_get_prompt_requires_structured_three_part_input_and_output(monkeypatch):
    from appcore import title_translate_settings as tts

    _mock_languages(
        monkeypatch,
        [{"code": "de", "name_zh": "德语", "enabled": 1}],
    )

    prompt = tts.get_prompt("de")
    _assert_structured_prompt(prompt)


@pytest.mark.parametrize(
    ("code", "name_zh", "expected_bits"),
    [
        ("de", "德语", ["德语本土化专家", "Bundesdeutsch"]),
        ("fr", "法语", ["法语本土化专家", "法语用户"]),
        ("es", "西班牙语", ["西班牙语本土化专家", "西语用户"]),
        ("it", "意大利语", ["意大利语本土化专家", "意大利用户"]),
        ("ja", "日语", ["日语本土化专家", "日本用户"]),
        ("pt", "葡萄牙语", ["葡萄牙语本土化专家", "葡语用户"]),
        ("sv", "瑞典语", ["瑞典语本土化专家", "瑞典用户", "naturlig svenska"]),
    ],
)
def test_get_prompt_special_languages_keep_localized_signals_and_placeholder(monkeypatch, code, name_zh, expected_bits):
    from appcore import title_translate_settings as tts

    _mock_languages(
        monkeypatch,
        [{"code": code, "name_zh": name_zh, "enabled": 1}],
    )

    prompt = tts.get_prompt(code)
    _assert_structured_prompt(prompt)
    for bit in expected_bits:
        assert bit in prompt
    # 特化 prompt 必须附 few-shot 示例：英文输入 + 目标语言示例输出。
    assert "示例" in prompt
    assert "What's on Your Keychain?" in prompt
    # 示例输出每一行也必须以中文「标题: 」「文案: 」「描述: 」开头，
    # 不允许把示例标签本地化（防止模型把示例当成允许本地化标签的暗示）。
    assert prompt.count("\n标题: ") >= 2  # 输入示例 + 输出示例
    assert prompt.count("\n文案: ") >= 2
    assert prompt.count("\n描述: ") >= 2


def test_get_prompt_japanese_example_output_uses_chinese_labels(monkeypatch):
    """日语 few-shot 示例输出必须以中文「标题:」「文案:」「描述:」开头，
    绝不能演示成「タイトル:」「標題:」等本地化标签——这是 ja 复现 bug 的根源。
    """
    from appcore import title_translate_settings as tts

    _mock_languages(
        monkeypatch,
        [{"code": "ja", "name_zh": "日语", "enabled": 1}],
    )

    prompt = tts.get_prompt("ja")
    # 找到「正确的日语输出」段落，检查它的三行开头都是中文标签。
    marker = "正确的日语输出："
    assert marker in prompt
    body = prompt.split(marker, 1)[1]
    example_lines = [line for line in body.splitlines() if line.strip()][:3]
    assert len(example_lines) == 3
    assert example_lines[0].startswith("标题: ")
    assert example_lines[1].startswith("文案: ")
    assert example_lines[2].startswith("描述: ")
    # 任何「タイトル:」「標題:」开头都属于复现 bug 的格式。
    for line in example_lines:
        assert not line.startswith("タイトル"), "日语示例输出不能以「タイトル」开头"
        assert not line.startswith("標題"), "日语示例输出不能以「標題」开头"


def test_get_prompt_returns_generic_fallback_for_dynamic_language(monkeypatch):
    from appcore import title_translate_settings as tts

    _mock_languages(
        monkeypatch,
        [{"code": "nl", "name_zh": "荷兰语", "enabled": 1}],
    )

    prompt = tts.get_prompt(" nl ")
    _assert_structured_prompt(prompt)
    assert "荷兰语" in prompt
    assert "本土化专家" not in prompt
