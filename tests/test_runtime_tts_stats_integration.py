"""验证 BaseRunner._step_tts 在收尾时调用 tts_generation_stats.finalize。

不实际跑 ElevenLabs / 任何 LLM。只做白盒源码断言 + 同模块引用断言。
"""
from __future__ import annotations


def _pipeline_runner_source() -> str:
    import inspect
    from pathlib import Path

    import appcore.runtime as runtime_mod

    source_path = inspect.getsourcefile(runtime_mod.PipelineRunner)
    assert source_path is not None
    return Path(source_path).read_text(encoding="utf-8")


def test_runtime_imports_finalize_from_stats_module():
    """硬性断言：runtime.py 通过 module 级引用调用 finalize（不是 from ... import finalize）。

    这样 monkeypatch.setattr(stats_mod, "finalize", ...) 才能在测试里生效。
    """
    src = _pipeline_runner_source()
    assert (
        "from appcore import tts_generation_stats" in src
        or "import appcore.tts_generation_stats" in src
    )
    assert "tts_generation_stats.finalize(" in src


def test_step_tts_calls_finalize_for_both_return_paths():
    """白盒：runtime.py 源码里必须有两处 finalize 调用（converged + best_pick）。"""
    src = _pipeline_runner_source()
    occurrences = src.count("tts_generation_stats.finalize(")
    assert occurrences >= 2, (
        f"_step_tts 必须在 converged 和 best_pick 两条 return 路径前都调用 finalize，"
        f"当前只看到 {occurrences} 处"
    )


def test_runtime_finalize_is_same_object_as_stats_module():
    """runtime 看到的 finalize 必须是 stats_mod 上同一个对象，monkeypatch 才会跨过去。"""
    from appcore import tts_generation_stats as stats_mod
    import appcore.runtime as runtime_mod
    assert hasattr(runtime_mod, "tts_generation_stats")
    assert runtime_mod.tts_generation_stats.finalize is stats_mod.finalize
