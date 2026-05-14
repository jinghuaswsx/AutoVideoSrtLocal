from appcore.meta_hot_posts import message_translation


def test_translate_message_html_extracts_plain_text_and_sanitizes_output():
    calls = []

    def fake_invoke_chat(use_case_code, **kwargs):
        calls.append((use_case_code, kwargs))
        return {"text": "深度清洁<script>alert(1)</script>\n第二行"}

    translated = message_translation.translate_message_html(
        "<p>Deep Clean. <b>Zero Chemicals.</b></p>",
        user_id=7,
        invoke_chat_fn=fake_invoke_chat,
    )

    assert translated == "深度清洁&lt;script&gt;alert(1)&lt;/script&gt;<br>第二行"
    assert calls[0][0] == "meta_hot_posts.translate_message"
    assert calls[0][1]["user_id"] == 7
    assert "Deep Clean." in calls[0][1]["messages"][1]["content"]
    assert "只输出中文翻译" in calls[0][1]["messages"][0]["content"]


def test_translate_message_html_skips_blank_message_without_llm():
    def fail_invoke_chat(*args, **kwargs):
        raise AssertionError("blank message must not call llm")

    assert (
        message_translation.translate_message_html(
            "  <p> </p>  ",
            invoke_chat_fn=fail_invoke_chat,
        )
        == ""
    )


def test_translate_message_html_keeps_existing_chinese_without_llm():
    def fail_invoke_chat(*args, **kwargs):
        raise AssertionError("existing chinese message must not call llm")

    translated = message_translation.translate_message_html(
        "<p>已经是中文<br>第二行</p>",
        invoke_chat_fn=fail_invoke_chat,
    )

    assert translated == "已经是中文<br>第二行"
