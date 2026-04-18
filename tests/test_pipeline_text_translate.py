"""纯文本翻译层单元测试(mock LLM,不打真实 API)。"""
import pytest


def test_translate_empty_returns_empty_no_llm_call(monkeypatch):
    """空输入直接短路,不调 resolve_provider_config。"""
    called = {"count": 0}

    def fake_resolve(*a, **kw):
        called["count"] += 1
        return None, None

    from pipeline import text_translate as mod
    monkeypatch.setattr(mod, "resolve_provider_config", fake_resolve)

    result = mod.translate_text("", "en", "de")
    assert result == {"text": "", "input_tokens": 0, "output_tokens": 0}
    assert called["count"] == 0


def test_translate_whitespace_only_short_circuits(monkeypatch):
    """只含空格的输入也短路。"""
    from pipeline import text_translate as mod
    monkeypatch.setattr(mod, "resolve_provider_config",
                         lambda *a, **kw: pytest.fail("不应被调用"))

    result = mod.translate_text("   \n\t  ", "en", "de")
    assert result["text"] == ""


def test_translate_builds_correct_prompt_and_parses_response(monkeypatch):
    """System prompt 包含源语言和目标语言名,usage 正确解析。"""
    captured = {}

    class FakeMessage:
        def __init__(self, content):
            self.content = content

    class FakeChoice:
        def __init__(self, content):
            self.message = FakeMessage(content)

    class FakeUsage:
        prompt_tokens = 42
        completion_tokens = 18

    class FakeResponse:
        choices = [FakeChoice("Willkommen zu unserem Produkt")]
        usage = FakeUsage()

    class FakeCompletions:
        @staticmethod
        def create(**kw):
            captured.update(kw)
            return FakeResponse()

    class FakeChat:
        completions = FakeCompletions()

    class FakeClient:
        chat = FakeChat()

    from pipeline import text_translate as mod
    monkeypatch.setattr(mod, "resolve_provider_config",
                         lambda *a, **kw: (FakeClient(), "gemini-2.5-flash"))

    result = mod.translate_text("Welcome to our product", "en", "de")

    assert result["text"] == "Willkommen zu unserem Produkt"
    assert result["input_tokens"] == 42
    assert result["output_tokens"] == 18
    # 核对 prompt 构建
    system_msg = captured["messages"][0]["content"]
    assert "English" in system_msg
    assert "German" in system_msg
    user_msg = captured["messages"][1]["content"]
    assert user_msg == "Welcome to our product"
    # 温度应该保守
    assert captured["temperature"] == 0.2


def test_translate_handles_missing_usage(monkeypatch):
    """LLM response 没有 usage 时,token 字段回退 0。"""
    class FakeResponse:
        class choices:
            pass

    FakeResponse.choices = [type("C", (), {
        "message": type("M", (), {"content": "Willkommen"})()
    })()]
    FakeResponse.usage = None

    class FakeClient:
        class chat:
            class completions:
                @staticmethod
                def create(**kw):
                    return FakeResponse

    from pipeline import text_translate as mod
    monkeypatch.setattr(mod, "resolve_provider_config",
                         lambda *a, **kw: (FakeClient, "m"))

    result = mod.translate_text("Welcome", "en", "de")
    assert result["text"] == "Willkommen"
    assert result["input_tokens"] == 0
    assert result["output_tokens"] == 0


def test_translate_unknown_lang_code_falls_through(monkeypatch):
    """未映射的 lang code(如 'xx')直接作为 prompt 内容原样透传。"""
    captured = {}

    class FakeResponse:
        choices = [type("C", (), {
            "message": type("M", (), {"content": "hi"})()
        })()]
        usage = None

    class FakeClient:
        class chat:
            class completions:
                @staticmethod
                def create(**kw):
                    captured.update(kw)
                    return FakeResponse()

    from pipeline import text_translate as mod
    monkeypatch.setattr(mod, "resolve_provider_config",
                         lambda *a, **kw: (FakeClient(), "m"))

    mod.translate_text("hello", "xx", "yy")
    system_msg = captured["messages"][0]["content"]
    assert "xx" in system_msg
    assert "yy" in system_msg
