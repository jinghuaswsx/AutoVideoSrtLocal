"""OmniTranslateRunner 关键不变量测试。

聚焦本次新增的：_step_asr 在 user_specified_source_language=True 时跳过
LID（detect_language_llm）。完整的 _step_asr 行为需要 mock 整套 ASR 引擎
依赖；这里只做源码级 sanity check 防止 LID 闸门被无意回退。
"""
from __future__ import annotations

import inspect


def test_step_asr_lid_call_is_gated_by_user_specified_source_language():
    """LID 调用必须在 user_specified_source_language 闸门之内。

    关键不变量：用户明确选了语言（user_specified_source_language=True）时，
    _step_asr 不能再调用 detect_language_llm 改写 source_language——否则
    用户的明确选择会被 LLM 静默覆盖。
    """
    from appcore.runtime_omni import OmniTranslateRunner
    src = inspect.getsource(OmniTranslateRunner._step_asr)
    assert "detect_language_llm" in src, "LID 调用必须存在（自动检测路径仍要跑）"
    assert "user_specified_source_language" in src, (
        "LID 闸门必须存在；user_specified=True 时不能调 LID"
    )
    user_spec_idx = src.index("user_specified_source_language")
    detect_idx = src.index("detect_language_llm")
    assert user_spec_idx < detect_idx, (
        "user_specified_source_language 必须在 detect_language_llm 之前出现，"
        "才能作为闸门保护"
    )
