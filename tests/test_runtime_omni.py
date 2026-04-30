"""OmniTranslateRunner 关键不变量测试。"""
from __future__ import annotations

import inspect


def test_step_asr_never_calls_lid_to_override_manual_source_language():
    """源语言由人工选择，_step_asr 不能再调用 LID 改写 source_language。"""
    from appcore.runtime_omni import OmniTranslateRunner

    src = inspect.getsource(OmniTranslateRunner._step_asr)
    assert "detect_language_llm" not in src
    assert "omni-lid-override" not in src
