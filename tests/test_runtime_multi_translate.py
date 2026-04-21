from unittest.mock import patch

from appcore.events import EventBus
from appcore.runtime_multi import MultiTranslateRunner


def _make_runner():
    return MultiTranslateRunner(bus=EventBus(), user_id=1)


def test_step_translate_calls_resolver_with_base_plus_plugin():
    runner = _make_runner()
    task = {
        "task_dir": "/tmp/x",
        "target_lang": "de",
        "source_language": "en",
        "script_segments": [{"index": 0, "text": "hello"}],
        "interactive_review": False,
        "variants": {},
    }
    with patch("appcore.task_state.get", return_value=task), \
         patch("appcore.task_state.update"), \
         patch("appcore.task_state.set_artifact"), \
         patch("appcore.task_state.set_current_review_step"), \
         patch("appcore.runtime_multi.resolve_prompt_config") as m_resolve, \
         patch("appcore.runtime_multi.generate_localized_translation") as m_gen, \
         patch("appcore.runtime_multi._save_json"), \
         patch("appcore.runtime.ai_billing.log_request") as m_log_request, \
         patch("appcore.runtime_multi._build_review_segments", return_value=[]), \
         patch("appcore.runtime._translate_billing_model", return_value="gpt"), \
         patch("appcore.runtime_multi._resolve_translate_provider", return_value="openrouter"), \
         patch("appcore.runtime_multi.get_model_display_name", return_value="gpt"), \
         patch("appcore.runtime_multi.build_asr_artifact", return_value={}), \
         patch("appcore.runtime_multi.build_translate_artifact", return_value={}):
        m_resolve.side_effect = [
            {"provider": "openrouter", "model": "gpt", "content": "BASE_DE"},
            {"provider": "openrouter", "model": "gpt", "content": "ECOM_PLUGIN"},
        ]
        m_gen.return_value = {"full_text": "hi", "sentences": [], "_usage": {}}
        runner._step_translate("t1")

    assert m_resolve.call_args_list[0].args == ("base_translation", "de")
    assert m_resolve.call_args_list[1].args == ("ecommerce_plugin", None)

    kwargs = m_gen.call_args.kwargs
    assert "BASE_DE" in kwargs["custom_system_prompt"]
    assert "ECOM_PLUGIN" in kwargs["custom_system_prompt"]
    billing = m_log_request.call_args.kwargs
    assert billing["use_case_code"] == "video_translate.localize"
    assert billing["provider"] == "openrouter"
    assert billing["model"] == "gpt"
    assert billing["units_type"] == "tokens"
