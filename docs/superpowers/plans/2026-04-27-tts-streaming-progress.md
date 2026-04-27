# TTS 流式进度 + 通用折叠卡片 — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 TTS 步骤"正在做什么"实时透出来：顶部 step-msg 子任务级刷新 + Duration Log 内 ElevenLabs per-segment 计数 + 通用折叠卡片（Duration Log 首发使用，后续其他卡片可复用）。

**Architecture:** 复用现有 `EVT_STEP_UPDATE` / `EVT_TTS_DURATION_ROUND` 两条 socket 通道；后端在 `pipeline/tts.py:generate_full_audio` 加可选 `on_segment_done` 回调，在 `appcore/runtime.py` 加不写盘的 `_emit_substep_msg` 帮手；前端 `renderTtsDurationLog` 输出 `collapsible-card` 标记，挂上一段全局 `bootCollapsibleCards` JS。日语流水线（`runtime_ja.py`）独立改造，其他流水线继承自 `Runtime` 自动跟上。

**Tech Stack:** Python 3.11 / pytest / Flask + SocketIO / Jinja2 templates / 原生 JavaScript（无构建工具，模板内嵌脚本）

**Spec:** `docs/superpowers/specs/2026-04-27-tts-streaming-progress-design.md`（已 commit 至 master，9abd546）

**Worktree:** `.worktrees/tts-streaming-progress` · branch `feature/tts-streaming-progress`

---

## 文件结构

| 文件 | 改动类型 | 责任 |
|------|----------|------|
| `pipeline/tts.py` | Modify | `generate_full_audio` 接 `on_segment_done` 回调；契约稳定不破坏现有调用 |
| `appcore/runtime.py` | Modify | 加 `_emit_substep_msg` 帮手；TTS 入口 + duration loop 各 phase 挂 substep + per-segment 回调 |
| `appcore/runtime_ja.py` | Modify | 日语 `_step_tts` 同步加 substep + per-segment 回调 |
| `web/templates/_task_workbench_styles.html` | Modify | 追加 `.collapsible-card` / `.collapsible-header` / `.collapsible-toggle` CSS |
| `web/templates/_task_workbench_scripts.html` | Modify | `renderTtsDurationLog` 输出 collapsible 结构；`_phaseLabel` 支持 audio_gen 动态计数；新增 `bootCollapsibleCards` 全局函数 |
| `tests/test_tts_pipeline.py` | Modify | 加 `on_segment_done` 行为测试 |
| `tests/test_tts_duration_loop.py` | Modify | 加 substep / audio_segments_done 字段断言 |

不动 `runtime_de.py / runtime_fr.py / runtime_multi.py / runtime_omni.py`——它们继承自 `Runtime`，自动跟着升级。

---

## Task 1 — `generate_full_audio` 接 `on_segment_done` 回调

**Files:**
- Modify: `pipeline/tts.py:90-136`
- Test: `tests/test_tts_pipeline.py`

- [ ] **Step 1: 写失败测试 — 回调按段被调用**

加到 `tests/test_tts_pipeline.py` 末尾：

```python
def test_generate_full_audio_invokes_on_segment_done_per_segment(tmp_path, monkeypatch):
    """每完成一段 TTS 都应该调一次回调，参数为 (done, total, info)，
    done 从 1 递增到 total。"""
    import pipeline.tts as tts

    def fake_segment_audio(text, voice_id, output_path, **kwargs):
        with open(output_path, "wb") as f:
            f.write(b"x")
        return output_path

    monkeypatch.setattr(tts, "generate_segment_audio", fake_segment_audio)
    monkeypatch.setattr(tts, "_get_audio_duration", lambda p: 1.5)
    monkeypatch.setattr(
        "subprocess.run",
        lambda *a, **kw: type("R", (), {"returncode": 0, "stderr": ""})(),
    )

    segments = [{"tts_text": "a"}, {"tts_text": "b"}, {"tts_text": "c"}]
    calls: list[tuple[int, int, dict]] = []

    tts.generate_full_audio(
        segments,
        voice_id="v1",
        output_dir=str(tmp_path),
        on_segment_done=lambda done, total, info: calls.append((done, total, info)),
    )

    assert [c[0] for c in calls] == [1, 2, 3]
    assert all(c[1] == 3 for c in calls)
    assert calls[0][2]["segment_index"] == 0
    assert calls[0][2]["tts_duration"] == 1.5
```

- [ ] **Step 2: 验证测试失败**

Run: `pytest tests/test_tts_pipeline.py::test_generate_full_audio_invokes_on_segment_done_per_segment -v`
Expected: FAIL，错误是 `TypeError: generate_full_audio() got an unexpected keyword argument 'on_segment_done'`

- [ ] **Step 3: 写最小实现**

修改 `pipeline/tts.py:90-98` 函数签名加参数；修改 `pipeline/tts.py:111-125` 循环体在每段完成后调回调：

```python
from typing import Callable, Optional, List, Dict
import logging

log = logging.getLogger(__name__)


def generate_full_audio(
    segments: List[Dict],
    voice_id: str,
    output_dir: str,
    variant: str | None = None,
    elevenlabs_api_key: str | None = None,
    model_id: str = "eleven_turbo_v2_5",
    language_code: str | None = None,
    on_segment_done: Optional[Callable[[int, int, dict], None]] = None,
) -> Dict:
    seg_dir = os.path.join(output_dir, "tts_segments", variant) if variant else os.path.join(output_dir, "tts_segments")
    os.makedirs(seg_dir, exist_ok=True)

    updated_segments = []
    concat_list_path = os.path.join(seg_dir, "concat.txt")
    total = len(segments)

    with open(concat_list_path, "w", encoding="utf-8") as concat_f:
        for i, seg in enumerate(segments):
            text = seg.get("tts_text") or seg.get("translated") or seg.get("text", "")
            seg_path = os.path.join(seg_dir, f"seg_{i:04d}.mp3")

            generate_segment_audio(text, voice_id, seg_path, elevenlabs_api_key=elevenlabs_api_key,
                                   model_id=model_id, language_code=language_code)
            duration = _get_audio_duration(seg_path)

            seg_copy = dict(seg)
            seg_copy["tts_path"] = seg_path
            seg_copy["tts_duration"] = duration
            updated_segments.append(seg_copy)

            concat_f.write(f"file '{os.path.abspath(seg_path)}'\n")

            if on_segment_done is not None:
                try:
                    on_segment_done(i + 1, total, {
                        "segment_index": i,
                        "tts_duration": duration,
                        "tts_text_preview": (text or "")[:60],
                    })
                except Exception:
                    log.exception("on_segment_done callback raised; ignoring")

    full_audio_name = f"tts_full.{variant}.mp3" if variant else "tts_full.mp3"
    full_audio_path = os.path.join(output_dir, full_audio_name)
    result = subprocess.run(
        ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", concat_list_path, "-c", "copy", full_audio_path],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        raise RuntimeError(f"音频拼接失败: {result.stderr}")

    return {"full_audio_path": full_audio_path, "segments": updated_segments}
```

> 注意：`from typing import ...` 已在文件顶部，新加的 `Callable, Optional` 加进去；`logging` 也是标准库导入，加在顶部。

- [ ] **Step 4: 验证测试通过**

Run: `pytest tests/test_tts_pipeline.py::test_generate_full_audio_invokes_on_segment_done_per_segment -v`
Expected: PASS

- [ ] **Step 5: 写第二个测试 — 回调抛错不破坏主流程**

```python
def test_generate_full_audio_swallows_callback_exceptions(tmp_path, monkeypatch):
    """回调抛错不能让主流程失败。返回值仍然完整。"""
    import pipeline.tts as tts

    def fake_segment_audio(text, voice_id, output_path, **kwargs):
        with open(output_path, "wb") as f:
            f.write(b"x")
        return output_path

    monkeypatch.setattr(tts, "generate_segment_audio", fake_segment_audio)
    monkeypatch.setattr(tts, "_get_audio_duration", lambda p: 1.0)
    monkeypatch.setattr(
        "subprocess.run",
        lambda *a, **kw: type("R", (), {"returncode": 0, "stderr": ""})(),
    )

    def boom(done, total, info):
        raise RuntimeError("callback explodes")

    out = tts.generate_full_audio(
        [{"tts_text": "x"}],
        voice_id="v1",
        output_dir=str(tmp_path),
        on_segment_done=boom,
    )
    assert "full_audio_path" in out
    assert len(out["segments"]) == 1
```

Run: `pytest tests/test_tts_pipeline.py::test_generate_full_audio_swallows_callback_exceptions -v`
Expected: PASS（实现里已经 try/except）

- [ ] **Step 6: 跑完整 tts pipeline 套件确保没破坏旧测试**

Run: `pytest tests/test_tts_pipeline.py -v`
Expected: 全 PASS

- [ ] **Step 7: Commit**

```bash
git add pipeline/tts.py tests/test_tts_pipeline.py
git commit -m "$(cat <<'EOF'
feat(tts): generate_full_audio accepts on_segment_done callback

Per-segment progress hook for streaming TTS UI. Errors in callback are
swallowed so the audio pipeline can't be broken by an upstream emit bug.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 2 — `PipelineRunner._emit_substep_msg` 帮手

**Files:**
- Modify: `appcore/runtime.py:431` 之后（紧挨现有 `_set_step`）
- Test: `tests/test_appcore_runtime.py`（如果不存在则新建）

- [ ] **Step 1: 检查测试文件是否存在**

Run: `ls tests/test_appcore_runtime.py`
Expected: 文件存在（之前 grep 确认过）

- [ ] **Step 2: 写失败测试 — 事件发出但不写盘**

加到 `tests/test_appcore_runtime.py`（按现有风格放在合适位置，文件如果使用 class 风格就开新 class）：

```python
def test_emit_substep_msg_publishes_event_without_persisting(tmp_path, monkeypatch):
    """_emit_substep_msg 应该 publish EVT_STEP_UPDATE 但不调
    task_state.set_step_message / set_step（避免每段一次磁盘写入）。"""
    from appcore import task_state
    from appcore.events import EventBus, EVT_STEP_UPDATE
    from appcore.runtime import PipelineRunner

    bus = EventBus()
    captured = []
    bus.subscribe(lambda e: captured.append(e))

    task_state.create("substep-task", "v.mp4", str(tmp_path),
                      original_filename="v.mp4", user_id=1)
    runner = PipelineRunner(bus=bus, user_id=1)

    set_step_calls = []
    set_msg_calls = []
    monkeypatch.setattr(task_state, "set_step",
                        lambda *a, **kw: set_step_calls.append((a, kw)))
    monkeypatch.setattr(task_state, "set_step_message",
                        lambda *a, **kw: set_msg_calls.append((a, kw)))

    runner._emit_substep_msg("substep-task", "tts", "正在生成英语配音 · 第 1 轮 · 切分朗读文案中")

    step_events = [e for e in captured if e.type == EVT_STEP_UPDATE]
    assert len(step_events) == 1
    assert step_events[0].payload["step"] == "tts"
    assert step_events[0].payload["message"] == "正在生成英语配音 · 第 1 轮 · 切分朗读文案中"
    assert set_step_calls == []
    assert set_msg_calls == []
```

- [ ] **Step 3: 验证测试失败**

Run: `pytest tests/test_appcore_runtime.py::test_emit_substep_msg_publishes_event_without_persisting -v`
Expected: FAIL，`AttributeError: 'PipelineRunner' object has no attribute '_emit_substep_msg'`

- [ ] **Step 4: 实现 `_emit_substep_msg`**

在 `appcore/runtime.py:440` 之后（`_set_step` 函数末尾、`_get_localization_module` 之前）加：

```python
    def _emit_substep_msg(self, task_id: str, step: str, sub_msg: str) -> None:
        """Emit EVT_STEP_UPDATE with a refreshed message but DO NOT persist.

        Use for high-frequency sub-step progress (per ElevenLabs segment, etc.)
        where persisting every event would thrash task_state.
        """
        task = task_state.get(task_id) or {}
        status = (task.get("steps") or {}).get(step, "running")
        payload = {"step": step, "status": status, "message": sub_msg}
        existing_tag = (task.get("step_model_tags") or {}).get(step, "")
        if existing_tag:
            payload["model_tag"] = existing_tag
        self._emit(task_id, EVT_STEP_UPDATE, payload)
```

- [ ] **Step 5: 验证测试通过**

Run: `pytest tests/test_appcore_runtime.py::test_emit_substep_msg_publishes_event_without_persisting -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add appcore/runtime.py tests/test_appcore_runtime.py
git commit -m "$(cat <<'EOF'
feat(runtime): add _emit_substep_msg for high-frequency progress updates

Sister to _set_step but skips task_state persistence. Targets per-segment
TTS progress where 50-80 events per task would otherwise thrash disk.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 3 — `_step_tts` 入口子步骤标记

**Files:**
- Modify: `appcore/runtime.py:1481-1510`（`_step_tts` 开头）
- Test: `tests/test_appcore_runtime.py`

- [ ] **Step 1: 写失败测试 — 进入 TTS 步骤即发"加载配音模板"子步骤**

加到 `tests/test_appcore_runtime.py`：

```python
def test_step_tts_emits_loading_voice_substep(tmp_path, monkeypatch):
    """_step_tts 一进来就应该立即发一条 EVT_STEP_UPDATE，message 包含"加载配音模板"，
    覆盖首轮 LLM 调用前的几百毫秒空白。"""
    from appcore import task_state
    from appcore.events import EventBus, EVT_STEP_UPDATE
    from appcore.runtime import PipelineRunner

    bus = EventBus()
    captured = []
    bus.subscribe(lambda e: captured.append(e))

    task_state.create("loading-msg-task", "v.mp4", str(tmp_path),
                      original_filename="v.mp4", user_id=1)
    task_state.update("loading-msg-task", source_full_text="hi",
                      script_segments=[{"index": 0, "text": "hi", "start_time": 0.0, "end_time": 1.0}],
                      localized_translation={"full_text": "hola", "sentences": [{"text": "hola"}]},
                      variants={"normal": {"localized_translation": {"full_text": "hola", "sentences": [{"text": "hola"}]}}})

    runner = PipelineRunner(bus=bus, user_id=1)
    # 强制让 _run_tts_duration_loop 抛 RuntimeError，使 _step_tts 在
    # 发完入口 substep 后立刻退出，避免依赖完整 ElevenLabs / LLM mock。
    monkeypatch.setattr(
        runner, "_run_tts_duration_loop",
        lambda **kw: (_ for _ in ()).throw(RuntimeError("stop here")),
    )
    monkeypatch.setattr("pipeline.extract.get_video_duration", lambda p: 30.0)
    monkeypatch.setattr(runner, "_resolve_voice", lambda task, mod: {
        "id": 1, "elevenlabs_voice_id": "vid"})
    monkeypatch.setattr("appcore.api_keys.resolve_key", lambda *a, **kw: "fake")

    try:
        runner._step_tts("loading-msg-task", str(tmp_path))
    except RuntimeError:
        pass

    msgs = [e.payload["message"] for e in captured if e.type == EVT_STEP_UPDATE]
    assert any("加载配音模板" in m for m in msgs), f"got messages: {msgs}"
```

- [ ] **Step 2: 验证测试失败**

Run: `pytest tests/test_appcore_runtime.py::test_step_tts_emits_loading_voice_substep -v`
Expected: FAIL，没有任何 message 包含"加载配音模板"

- [ ] **Step 3: 实现 — `_step_tts` 入口加 substep**

在 `appcore/runtime.py:1510` 现有这行：
```python
        self._set_step(task_id, "tts", "running", f"正在生成{lang_display}配音...", model_tag=_tts_model_tag)
```
**之后**追加：
```python
        self._emit_substep_msg(task_id, "tts",
            f"正在生成{lang_display}配音 · 加载配音模板")
```

- [ ] **Step 4: 验证测试通过**

Run: `pytest tests/test_appcore_runtime.py::test_step_tts_emits_loading_voice_substep -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add appcore/runtime.py tests/test_appcore_runtime.py
git commit -m "$(cat <<'EOF'
feat(tts-step): emit '加载配音模板' substep on TTS step entry

Closes the dead air between '正在生成英语配音…' and the first
duration-round event (round 1 tts_script_regen).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 4 — `_run_tts_duration_loop` per-phase substep + per-segment 实时进度

**Files:**
- Modify: `appcore/runtime.py:514-836`（duration loop 主循环）
- Test: `tests/test_tts_duration_loop.py`

> 这一 task 同时做两件事：①在每个 phase emit 旁挂一条 substep msg；②给 `generate_full_audio` 传 `on_segment_done` 回调，每段更新 `round_record.audio_segments_done/total` 并 re-emit `audio_gen` phase。

- [ ] **Step 1: 写失败测试 — substep 序列覆盖每个 phase**

加到 `tests/test_tts_duration_loop.py` 末尾（class `TestDurationLoopRound1Only` 内，复用现有 fixture）：

```python
    def test_round1_emits_substep_msgs_for_each_phase(self, tmp_path, monkeypatch):
        """Round 1 应该发出 4 条以上 substep msg（加载/切分/audio_gen 0段/audio_gen N段
        多次/校验测量），覆盖整个流程，避免任何 5s+ 的静默期。"""
        runner = self._make_runner()
        from appcore import task_state
        from appcore.events import EVT_STEP_UPDATE
        captured = []
        runner.bus.subscribe(lambda e: captured.append(e))

        def fake_gen_full_audio(tts_segments, voice_id, task_dir, variant=None,
                                 on_segment_done=None, **kw):
            out = os.path.join(task_dir, f"tts_full.{variant}.mp3")
            with open(out, "wb") as f:
                f.write(b"fake")
            # 模拟两段 ElevenLabs 调用，触发 2 次 callback
            if on_segment_done:
                on_segment_done(1, 2, {"segment_index": 0, "tts_duration": 1.0})
                on_segment_done(2, 2, {"segment_index": 1, "tts_duration": 1.5})
            return {"full_audio_path": out, "segments": [
                {"index": 0, "tts_path": out, "tts_duration": 1.0},
                {"index": 1, "tts_path": out, "tts_duration": 1.5},
            ]}

        monkeypatch.setattr("pipeline.tts.generate_full_audio", fake_gen_full_audio)
        monkeypatch.setattr("pipeline.tts._get_audio_duration", lambda p: 31.5)
        monkeypatch.setattr(
            "pipeline.translate.generate_tts_script",
            lambda loc, **kw: {
                "full_text": "Short text.",
                "blocks": [{"index": 0, "text": "Short.",
                             "sentence_indices": [0], "source_segment_indices": [0]}],
                "subtitle_chunks": []},
        )
        monkeypatch.setattr("pipeline.speech_rate_model.get_rate", lambda v, l: 15.0)
        monkeypatch.setattr("pipeline.speech_rate_model.update_rate", lambda *a, **kw: None)

        task_state.create("substep-loop-task", "v.mp4", str(tmp_path),
                          original_filename="v.mp4", user_id=1)

        import importlib
        loc_mod = importlib.import_module("pipeline.localization")
        runner._run_tts_duration_loop(
            task_id="substep-loop-task", task_dir=str(tmp_path),
            loc_mod=loc_mod, provider="openrouter",
            video_duration=30.0, voice={"elevenlabs_voice_id": "v1"},
            initial_localized_translation={"full_text": "hi.", "sentences": [{"text": "hi."}]},
            source_full_text="hi.", source_language="en",
            elevenlabs_api_key="fake-key",
            script_segments=[{"index": 0, "text": "hi", "start_time": 0.0, "end_time": 1.0}],
            variant="normal", target_language_label="en",
        )

        msgs = [e.payload["message"] for e in captured if e.type == EVT_STEP_UPDATE]
        assert any("切分朗读文案" in m for m in msgs), f"got: {msgs}"
        assert any("ElevenLabs 音频 1/2" in m for m in msgs), f"got: {msgs}"
        assert any("ElevenLabs 音频 2/2" in m for m in msgs), f"got: {msgs}"
        assert any("测量" in m or "校验" in m for m in msgs), f"got: {msgs}"
```

- [ ] **Step 2: 写第二个测试 — round_record 含 audio_segments 字段**

```python
    def test_round1_record_has_audio_segments_total_after_audio_gen(self, tmp_path, monkeypatch):
        """audio_gen phase 必须把 audio_segments_total 落到 round 上，
        前端 _phaseLabel 才能显示 X/Y 计数。"""
        runner = self._make_runner()
        from appcore import task_state
        from appcore.events import EVT_TTS_DURATION_ROUND
        captured = []
        runner.bus.subscribe(lambda e: captured.append(e))

        def fake_gen_full_audio(tts_segments, voice_id, task_dir, variant=None,
                                 on_segment_done=None, **kw):
            out = os.path.join(task_dir, f"tts_full.{variant}.mp3")
            with open(out, "wb") as f:
                f.write(b"fake")
            if on_segment_done:
                on_segment_done(1, 3, {"segment_index": 0})
                on_segment_done(2, 3, {"segment_index": 1})
                on_segment_done(3, 3, {"segment_index": 2})
            return {"full_audio_path": out, "segments": [
                {"index": i, "tts_path": out, "tts_duration": 1.0} for i in range(3)
            ]}

        monkeypatch.setattr("pipeline.tts.generate_full_audio", fake_gen_full_audio)
        monkeypatch.setattr("pipeline.tts._get_audio_duration", lambda p: 31.5)
        monkeypatch.setattr(
            "pipeline.translate.generate_tts_script",
            lambda loc, **kw: {
                "full_text": "x.",
                "blocks": [{"index": 0, "text": "x", "sentence_indices": [0],
                             "source_segment_indices": [0]}],
                "subtitle_chunks": []},
        )
        monkeypatch.setattr("pipeline.speech_rate_model.get_rate", lambda v, l: 15.0)
        monkeypatch.setattr("pipeline.speech_rate_model.update_rate", lambda *a, **kw: None)

        task_state.create("seg-fields-task", "v.mp4", str(tmp_path),
                          original_filename="v.mp4", user_id=1)

        import importlib
        loc_mod = importlib.import_module("pipeline.localization")
        runner._run_tts_duration_loop(
            task_id="seg-fields-task", task_dir=str(tmp_path),
            loc_mod=loc_mod, provider="openrouter",
            video_duration=30.0, voice={"elevenlabs_voice_id": "v1"},
            initial_localized_translation={"full_text": "hi.", "sentences": [{"text": "hi."}]},
            source_full_text="hi.", source_language="en",
            elevenlabs_api_key="fake-key",
            script_segments=[{"index": 0, "text": "hi", "start_time": 0.0, "end_time": 1.0}],
            variant="normal", target_language_label="en",
        )

        audio_gen_events = [e for e in captured
                             if e.type == EVT_TTS_DURATION_ROUND
                             and e.payload.get("phase") == "audio_gen"]
        # 至少 4 次：1 次 pre-call + 3 次 per-segment
        assert len(audio_gen_events) >= 4
        last = audio_gen_events[-1].payload
        assert last["audio_segments_total"] == 3
        assert last["audio_segments_done"] == 3
```

- [ ] **Step 3: 验证两个测试都失败**

Run: `pytest tests/test_tts_duration_loop.py -k "substep_msgs or audio_segments_total" -v`
Expected: 两个 FAIL

- [ ] **Step 4: 实现 — duration loop 注入 substep + per-segment 回调**

在 `appcore/runtime.py:_run_tts_duration_loop` 内：

**(a)** 函数顶部、`MAX_ROUNDS = 5` 之后、`for round_index ...` 循环之前，加一个 helper：

```python
        def _substep(sub: str) -> None:
            self._emit_substep_msg(
                task_id, "tts",
                f"正在生成{_lang_display(target_language_label)}配音 · 第 {round_index} 轮 · {sub}",
            )
```

> 注意：`round_index` 是循环变量，闭包按引用捕获，每次调用 `_substep` 时读到的是当前 round。

**(b)** 在每个现有 `_emit_duration_round` 旁挂 substep（按现有行号映射）：

| 位置 | 现有 emit | 紧前/紧后追加 |
|------|----------|---------------|
| `runtime.py:570` `translate_rewrite` 之前 | — | `_substep("准备重写译文")`（在 `else:` 分支顶部，attempts 循环之前） |
| `runtime.py:724` `tts_script_regen` 之前 | `_emit_duration_round(... "tts_script_regen" ...)` | 紧前加 `_substep("切分朗读文案中")` |
| `runtime.py:751` `audio_gen` 之前 | `_emit_duration_round(... "audio_gen" ...)` | 紧前加 `_substep(f"生成 ElevenLabs 音频 0/{len(tts_segments)}")` |
| `runtime.py:763` `language_check` 之前 | — | 紧前加 `_substep("校验语言 / 测量时长")` |

**(c)** `audio_gen` phase 的 round_record 加 segments_total，并改 `generate_full_audio` 调用挂回调：

把 `runtime.py:751-758` 改为：

```python
            # Phase 3: audio_gen
            tts_segments = loc_mod.build_tts_segments(tts_script, script_segments)
            round_record["audio_segments_total"] = len(tts_segments)
            round_record["audio_segments_done"] = 0
            _substep(f"生成 ElevenLabs 音频 0/{len(tts_segments)}")
            self._emit_duration_round(task_id, round_index, "audio_gen", round_record)

            def _on_seg_done(done, total, info):
                round_record["audio_segments_done"] = done
                round_record["audio_segments_total"] = total
                self._emit_substep_msg(
                    task_id, "tts",
                    f"正在生成{_lang_display(target_language_label)}配音 · 第 {round_index} 轮 · 生成 ElevenLabs 音频 {done}/{total}",
                )
                self._emit_duration_round(task_id, round_index, "audio_gen", round_record)

            result = generate_full_audio(
                tts_segments, voice["elevenlabs_voice_id"], task_dir,
                variant=f"round_{round_index}",
                elevenlabs_api_key=elevenlabs_api_key,
                model_id=tts_model_id,
                language_code=tts_language_code,
                on_segment_done=_on_seg_done,
            )
            round_record["artifact_paths"]["tts_full_audio"] = f"tts_full.round_{round_index}.mp3"
```

> 注意：`_on_seg_done` 闭包引用 `round_record` / `round_index` / `target_language_label`，都是循环作用域里的变量，每轮重新绑定，符合预期。

- [ ] **Step 5: 验证两个测试通过**

Run: `pytest tests/test_tts_duration_loop.py -k "substep_msgs or audio_segments_total" -v`
Expected: 两个 PASS

- [ ] **Step 6: 跑整个 duration loop 套件，确保没破坏旧测试**

Run: `pytest tests/test_tts_duration_loop.py -v`
Expected: 全 PASS

- [ ] **Step 7: Commit**

```bash
git add appcore/runtime.py tests/test_tts_duration_loop.py
git commit -m "$(cat <<'EOF'
feat(tts-loop): emit per-phase substep + per-segment audio progress

Instruments _run_tts_duration_loop with:
- substep msgs at each phase (准备重写 / 切分朗读 / ElevenLabs N/M / 校验)
- on_segment_done callback updating round.audio_segments_done/total and
  re-emitting audio_gen phase so the UI can render '5/15' counter live

Round-record persistence is unchanged — per-segment events go socket-only.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 5 — Rewrite attempts substep messages

**Files:**
- Modify: `appcore/runtime.py:584-685`（rewrite 内循环）
- Test: `tests/test_tts_duration_loop.py`

- [ ] **Step 1: 写失败测试 — 每次 attempt 进入循环时发一条 substep**

```python
    def test_round2_emits_substep_per_rewrite_attempt(self, tmp_path, monkeypatch):
        """Round 2+ 的每次 rewrite attempt 应当各发一条 substep，
        让用户能看到"attempt 2/5（目标 75 词）"这种实时刷新。"""
        runner = self._make_runner()
        from appcore import task_state
        from appcore.events import EVT_STEP_UPDATE

        captured = []
        runner.bus.subscribe(lambda e: captured.append(e))

        # round1 → 25s（短，触发 expand），round2 attempt1 → 30s（命中区间）
        durations = iter([25.0, 30.0])

        def fake_gen_full_audio(tts_segments, voice_id, task_dir, variant=None,
                                 on_segment_done=None, **kw):
            out = os.path.join(task_dir, f"tts_full.{variant}.mp3")
            with open(out, "wb") as f:
                f.write(b"fake")
            return {"full_audio_path": out, "segments": [
                {"index": 0, "tts_path": out, "tts_duration": 1.0}]}

        monkeypatch.setattr("pipeline.tts.generate_full_audio", fake_gen_full_audio)
        monkeypatch.setattr("pipeline.tts._get_audio_duration", lambda p: next(durations))
        monkeypatch.setattr(
            "pipeline.translate.generate_tts_script",
            lambda loc, **kw: {
                "full_text": "x.",
                "blocks": [{"index": 0, "text": "x", "sentence_indices": [0],
                             "source_segment_indices": [0]}],
                "subtitle_chunks": []},
        )
        # rewrite 一次成功：attempt 1 给 75 词（target=75）
        rewrite_calls = {"n": 0}
        def fake_rewrite(**kwargs):
            rewrite_calls["n"] += 1
            return {
                "full_text": " ".join(["w"] * kwargs["target_words"]),
                "sentences": [{"text": " ".join(["w"] * kwargs["target_words"])}],
            }
        monkeypatch.setattr("pipeline.translate.generate_localized_rewrite", fake_rewrite)
        monkeypatch.setattr("pipeline.speech_rate_model.get_rate", lambda v, l: 15.0)
        monkeypatch.setattr("pipeline.speech_rate_model.update_rate", lambda *a, **kw: None)

        task_state.create("rewrite-substep-task", "v.mp4", str(tmp_path),
                          original_filename="v.mp4", user_id=1)

        import importlib
        loc_mod = importlib.import_module("pipeline.localization")
        runner._run_tts_duration_loop(
            task_id="rewrite-substep-task", task_dir=str(tmp_path),
            loc_mod=loc_mod, provider="openrouter",
            video_duration=30.0, voice={"elevenlabs_voice_id": "v1"},
            initial_localized_translation={"full_text": "hi.", "sentences": [{"text": "hi."}]},
            source_full_text="hi.", source_language="en",
            elevenlabs_api_key="fake-key",
            script_segments=[{"index": 0, "text": "hi", "start_time": 0.0, "end_time": 1.0}],
            variant="normal", target_language_label="en",
        )

        msgs = [e.payload["message"] for e in captured if e.type == EVT_STEP_UPDATE]
        assert any("重写译文 attempt 1" in m for m in msgs), f"got: {msgs}"
```

- [ ] **Step 2: 验证测试失败**

Run: `pytest tests/test_tts_duration_loop.py::TestDurationLoopRound1Only::test_round2_emits_substep_per_rewrite_attempt -v`
Expected: FAIL

- [ ] **Step 3: 实现**

在 `appcore/runtime.py:591`（`for attempt in range(1, MAX_REWRITE_ATTEMPTS + 1):`）循环顶部，紧挨 `attempt_temperature = 0.6 if attempt == 1 else 1.0` 之后，加：

```python
                    _substep(
                        f"重写译文 attempt {attempt}/{MAX_REWRITE_ATTEMPTS}"
                        f"（目标 {target_words} 词，{direction}）"
                    )
```

- [ ] **Step 4: 验证测试通过**

Run: `pytest tests/test_tts_duration_loop.py::TestDurationLoopRound1Only::test_round2_emits_substep_per_rewrite_attempt -v`
Expected: PASS

- [ ] **Step 5: 跑整个 duration loop 套件**

Run: `pytest tests/test_tts_duration_loop.py -v`
Expected: 全 PASS

- [ ] **Step 6: Commit**

```bash
git add appcore/runtime.py tests/test_tts_duration_loop.py
git commit -m "$(cat <<'EOF'
feat(tts-loop): emit substep msg per rewrite attempt

Each attempt in the word-count convergence inner loop now publishes a
substep msg so the user sees 'attempt 2/5（目标 75 词，shrink）' instead
of staring at a static '正在生成英语配音…' for the full retry chain.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 6 — `runtime_ja._step_tts` 同步加 substep + per-segment

**Files:**
- Modify: `appcore/runtime_ja.py:233-360`
- Test: `tests/test_runtime_ja_shared_shell.py`（新加测试）

- [ ] **Step 1: 检查日语测试现状**

Run: `pytest tests/test_runtime_ja_shared_shell.py -v --collect-only`
Expected: 列出现有测试

- [ ] **Step 2: 写失败测试**

加到 `tests/test_runtime_ja_shared_shell.py`（按现有风格放在合适位置）：

```python
def test_ja_step_tts_emits_per_segment_substeps(tmp_path, monkeypatch):
    """日语 TTS 也应该在每段 ElevenLabs 完成时发一条 substep msg。"""
    from appcore import task_state
    from appcore.events import EventBus, EVT_STEP_UPDATE
    from appcore.runtime_ja import JapaneseTranslateRunner

    bus = EventBus()
    captured = []
    bus.subscribe(lambda e: captured.append(e))

    def fake_gen_full_audio(tts_segments, voice_id, output_dir, variant=None,
                             on_segment_done=None, **kw):
        out = os.path.join(output_dir, f"tts_full.{variant}.mp3")
        with open(out, "wb") as f:
            f.write(b"fake")
        if on_segment_done:
            on_segment_done(1, 2, {"segment_index": 0})
            on_segment_done(2, 2, {"segment_index": 1})
        return {"full_audio_path": out, "segments": [
            {"index": 0, "tts_path": out, "tts_duration": 1.0},
            {"index": 1, "tts_path": out, "tts_duration": 1.5},
        ]}

    # 让循环在第一轮就 in_range，避免依赖完整 rewrite mock
    monkeypatch.setattr("appcore.runtime_ja.generate_full_audio", fake_gen_full_audio)
    monkeypatch.setattr("appcore.runtime_ja._get_audio_duration", lambda p: 30.0)
    monkeypatch.setattr("appcore.runtime_ja.get_video_duration", lambda p: 30.0)
    monkeypatch.setattr("appcore.runtime_ja.resolve_key", lambda *a, **kw: "fake")

    import appcore.runtime_ja as rt_ja
    monkeypatch.setattr(rt_ja.ja_translate, "build_ja_tts_script",
                        lambda loc: {"full_text": "ハロー", "blocks": [],
                                     "subtitle_chunks": []})
    monkeypatch.setattr(rt_ja.ja_translate, "build_ja_tts_segments",
                        lambda script, segs: [
                            {"index": 0, "tts_text": "ハロー"},
                            {"index": 1, "tts_text": "ワールド"},
                        ])
    monkeypatch.setattr(rt_ja.ja_translate, "count_visible_japanese_chars",
                        lambda txt: 5)
    monkeypatch.setattr("pipeline.speech_rate_model.update_rate",
                        lambda *a, **kw: None)
    monkeypatch.setattr("appcore.runtime_ja.ai_billing.log_request",
                        lambda **kw: None)

    task_state.create("ja-substep-task", "v.mp4", str(tmp_path),
                      original_filename="v.mp4", user_id=1,
                      video_path=str(tmp_path / "v.mp4"))
    task_state.update("ja-substep-task",
                      script_segments=[{"index": 0, "text": "hi",
                                         "start_time": 0.0, "end_time": 1.0}],
                      localized_translation={"full_text": "ハロー",
                                              "sentences": [{"text": "ハロー"}]})

    runner = JapaneseTranslateRunner(bus=bus, user_id=1)
    monkeypatch.setattr(runner, "_resolve_voice", lambda task, mod: {
        "id": 1, "elevenlabs_voice_id": "vid"})

    runner._step_tts("ja-substep-task", str(tmp_path))

    msgs = [e.payload["message"] for e in captured if e.type == EVT_STEP_UPDATE]
    assert any("ElevenLabs 音频 1/2" in m for m in msgs), f"got: {msgs}"
    assert any("ElevenLabs 音频 2/2" in m for m in msgs), f"got: {msgs}"
```

- [ ] **Step 3: 验证测试失败**

Run: `pytest tests/test_runtime_ja_shared_shell.py::test_ja_step_tts_emits_per_segment_substeps -v`
Expected: FAIL

- [ ] **Step 4: 实现 — `runtime_ja._step_tts` 加 substep + per-segment**

在 `appcore/runtime_ja.py:240` 现有这行：
```python
        self._set_step(task_id, "tts", "running", "正在生成日语配音并执行时长收敛...", model_tag="ElevenLabs · ja")
```
**之后**追加：
```python
        self._emit_substep_msg(task_id, "tts",
            "正在生成日语配音 · 加载配音模板")
```

`appcore/runtime_ja.py:288-296` 现有 `generate_full_audio(...)` 调用前后做改造：

```python
            tts_script = ja_translate.build_ja_tts_script(current_localized)
            self._emit_substep_msg(task_id, "tts",
                f"正在生成日语配音 · 第 {round_index} 轮 · 切分朗读文案完成")
            tts_segments = ja_translate.build_ja_tts_segments(tts_script, task.get("script_segments", []))
            round_variant = f"ja_round_{round_index}"

            def _on_seg_done(done, total, info, _round=round_index):
                self._emit_substep_msg(
                    task_id, "tts",
                    f"正在生成日语配音 · 第 {_round} 轮 · 生成 ElevenLabs 音频 {done}/{total}",
                )

            self._emit_substep_msg(task_id, "tts",
                f"正在生成日语配音 · 第 {round_index} 轮 · 生成 ElevenLabs 音频 0/{len(tts_segments)}")
            tts_output = generate_full_audio(
                tts_segments,
                voice_id=voice_id,
                output_dir=task_dir,
                variant=round_variant,
                elevenlabs_api_key=elevenlabs_api_key,
                model_id=self.tts_model_id,
                language_code=self.tts_language_code,
                on_segment_done=_on_seg_done,
            )
```

> 注意 `_on_seg_done` 用默认参数 `_round=round_index` 把当前轮号绑定进闭包（避免循环结束后所有闭包都引用最后一轮的值）；`runtime.py` 的 `_substep` 因为是 helper 而不是 callback 不存在这个问题。

- [ ] **Step 5: 验证测试通过**

Run: `pytest tests/test_runtime_ja_shared_shell.py::test_ja_step_tts_emits_per_segment_substeps -v`
Expected: PASS

- [ ] **Step 6: 跑日语流水线全套测试**

Run: `pytest tests/test_runtime_ja_shared_shell.py -v`
Expected: 全 PASS

- [ ] **Step 7: Commit**

```bash
git add appcore/runtime_ja.py tests/test_runtime_ja_shared_shell.py
git commit -m "$(cat <<'EOF'
feat(tts-ja): substep + per-segment progress for Japanese pipeline

Mirrors the English/multi-lang instrumentation done in runtime.py so
Japanese tasks also stream sub-progress through the existing UI.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 7 — 前端 `_phaseLabel` 接受 round 参数，audio_gen 显示 X/Y

**Files:**
- Modify: `web/templates/_task_workbench_scripts.html:1176-1190` 函数定义
- Modify: `web/templates/_task_workbench_scripts.html:1549` 调用点

> 前端无 JS 单测基建，本 task 改完后通过手动跑一条任务做视觉验证。

- [ ] **Step 1: 修改 `_phaseLabel` 函数签名 + audio_gen 分支**

在 `web/templates/_task_workbench_scripts.html:1176`，把：

```javascript
  function _phaseLabel(phase) {
    return ({
      translate_rewrite: '正在重写译文',
      tts_script_regen:  '正在切分朗读块',
      audio_gen:         '正在生成 TTS 音频',
      ...
```

改成：

```javascript
  function _phaseLabel(phase, round) {
    if (phase === 'audio_gen' && round && round.audio_segments_total) {
      const done = round.audio_segments_done || 0;
      return `正在生成 TTS 音频 ${done}/${round.audio_segments_total}`;
    }
    return ({
      translate_rewrite: '正在重写译文',
      tts_script_regen:  '正在切分朗读块',
      audio_gen:         '正在生成 TTS 音频',
      ...
```

（保留原函数体后续部分不变。）

- [ ] **Step 2: 修改调用点传入 `round`**

`_task_workbench_scripts.html:1549`：

```javascript
const phaseLabel = isLast && status === 'running' ? _phaseLabel(round.__current_phase) : '';
```
改成：
```javascript
const phaseLabel = isLast && status === 'running' ? _phaseLabel(round.__current_phase, round) : '';
```

- [ ] **Step 3: 静态语法检查**

Run: `python -c "import re; src = open('web/templates/_task_workbench_scripts.html', encoding='utf-8').read(); print('OK' if 'function _phaseLabel(phase, round)' in src else 'MISSING')"`
Expected: 输出 `OK`

- [ ] **Step 4: Commit**

```bash
git add web/templates/_task_workbench_scripts.html
git commit -m "$(cat <<'EOF'
feat(duration-log): show audio_gen X/Y counter live

_phaseLabel now consults round.audio_segments_done/total so the active
round shows '正在生成 TTS 音频 5/15' instead of a static label during
the long ElevenLabs sequential phase.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 8 — 通用 `collapsible-card` CSS + JS

**Files:**
- Modify: `web/templates/_task_workbench_styles.html` 第 481 行附近（已有 `.duration-log` 规则）
- Modify: `web/templates/_task_workbench_scripts.html` 文件末尾（新增 `bootCollapsibleCards`）

- [ ] **Step 1: 在 `_task_workbench_styles.html` 追加 CSS**

在 `<style>` 块里 `.duration-log` 规则之前（或紧邻后面）追加：

```css
/* ============================================================
 * 通用可折叠卡片 (Duration Log 是首个使用者；后续模块按同款
 * markup 接入即可)
 * ============================================================ */
.collapsible-card[data-collapsed="true"] > .collapsible-body {
  display: none;
}

.collapsible-card .collapsible-header {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  cursor: pointer;
  user-select: none;
}

.collapsible-card .collapsible-toggle {
  margin-left: auto;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 24px;
  height: 24px;
  padding: 0;
  border: 1px solid var(--border);
  border-radius: var(--radius);
  background: var(--bg);
  color: var(--fg-muted);
  cursor: pointer;
  transition: transform var(--duration) var(--ease-out),
              background-color var(--duration-fast) var(--ease);
}

.collapsible-card .collapsible-toggle:hover {
  background: var(--bg-muted);
  color: var(--fg);
}

.collapsible-card[data-collapsed="true"] .collapsible-toggle {
  transform: rotate(-90deg);
}
```

- [ ] **Step 2: 在 `_task_workbench_scripts.html` 文件末尾（最后一个 `</script>` 之前）追加 `bootCollapsibleCards`**

```javascript
  // ============================================================
  // 通用可折叠卡片：任意带 .collapsible-card[data-collapsible="<key>"]
  // 的容器都会被绑定 toggle 行为；折叠状态走 localStorage，按 taskId 隔离。
  // ============================================================
  function bootCollapsibleCards(root, taskId) {
    root = root || document;
    taskId = taskId || (window.taskId) || 'global';
    root.querySelectorAll('.collapsible-card[data-collapsible]').forEach(card => {
      if (card.dataset.collapsibleBound === '1') return;
      card.dataset.collapsibleBound = '1';
      const key = card.dataset.collapsible;
      const storageKey = `collapsibleCard:${key}:${taskId}`;
      const stored = localStorage.getItem(storageKey);
      if (stored === '1') card.dataset.collapsed = 'true';

      const toggle = (ev) => {
        if (ev && ev.target && ev.target.closest('a, button:not(.collapsible-toggle), input, select')) return;
        const collapsed = card.dataset.collapsed === 'true';
        card.dataset.collapsed = collapsed ? 'false' : 'true';
        try {
          localStorage.setItem(storageKey, collapsed ? '0' : '1');
        } catch (_) { /* quota / private mode → silently ignore */ }
        const btn = card.querySelector('.collapsible-toggle');
        if (btn) btn.setAttribute('aria-expanded', collapsed ? 'true' : 'false');
      };

      const header = card.querySelector('.collapsible-header');
      if (header) header.addEventListener('click', toggle);
    });
  }
  // 暴露到 window 方便后续扩展（其他模块也可调用）
  window.bootCollapsibleCards = bootCollapsibleCards;
```

> 这一步 Task 8 不接入 Duration Log——只先把 CSS + JS 准备好。Task 9 再做 Duration Log 的 markup 改造。

- [ ] **Step 3: 启动 Flask 本地确认页面没语法错**

Run（前台启动，跑 5 秒看是否报错；端口冲突时自动换端口）：
```bash
python -m flask --app web.app run --port 5099 --no-reload &
sleep 3
curl -fsSI http://127.0.0.1:5099/login || echo "(未启动也无所谓 — 静态资源未坏即可)"
kill %1 2>/dev/null || true
```
Expected: 不出现 Jinja 模板语法错误；HTML 静态文件加载正常。

> 如果本地无法 spin Flask（依赖太重），跳过 Step 3，靠 grep 验证语法即可：
> `grep -c "function bootCollapsibleCards" web/templates/_task_workbench_scripts.html` 应输出 `1`。

- [ ] **Step 4: Commit**

```bash
git add web/templates/_task_workbench_styles.html web/templates/_task_workbench_scripts.html
git commit -m "$(cat <<'EOF'
feat(ui): generic collapsible-card pattern (CSS + bootstrap JS)

Reusable across modules. Markup contract: .collapsible-card with
[data-collapsible="<key>"]; child .collapsible-header is the click
target, .collapsible-body is the toggleable region. State persists in
localStorage scoped per taskId.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 9 — Duration Log 接入 collapsible-card

**Files:**
- Modify: `web/templates/_task_workbench_scripts.html:1480-1700`（`renderTtsDurationLog` 函数）

- [ ] **Step 1: 改造 HTML 结构**

`renderTtsDurationLog` 内的 `parts.push` 第一段（`_task_workbench_scripts.html:1521-1529`），把：

```javascript
    parts.push(`
      <div class="duration-log-header">
        <span>翻译本土化 · 时长控制迭代（Duration Loop）</span>
        ${statusTag}
        ${modelTag}
        <span class="meta">${metaStr}</span>
      </div>
    `);
```

改成：

```javascript
    parts.push(`
      <div class="collapsible-header duration-log-header">
        <span class="collapsible-title">翻译本土化 · 时长控制迭代（Duration Loop）</span>
        ${statusTag}
        ${modelTag}
        <span class="meta">${metaStr}</span>
        <button type="button" class="collapsible-toggle" aria-label="展开/折叠" aria-expanded="true">
          <svg width="14" height="14" viewBox="0 0 16 16" fill="none">
            <path d="M4 6l4 4 4-4" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>
          </svg>
        </button>
      </div>
      <div class="collapsible-body">
    `);
```

> 注意：现在 header 之后多了一个 `<div class="collapsible-body">`（不闭合），它要在函数最后 push 一个闭合标签。

- [ ] **Step 2: 在函数最末（`container.innerHTML = ...` 之前）补 body 闭合 + 容器属性**

`_task_workbench_scripts.html` 的 `renderTtsDurationLog` 末尾（在 `parts.push(...)` 完成后、设置 `container.innerHTML` 之前），追加：

```javascript
    parts.push('</div>'); // close .collapsible-body

    container.classList.add('collapsible-card');
    container.dataset.collapsible = 'duration-log';
    container.innerHTML = parts.join('');
    container.hidden = false;

    if (window.bootCollapsibleCards) {
      window.bootCollapsibleCards(container.parentNode || document, taskId);
    }
```

> 把这段加到现有 `container.innerHTML = parts.join('');` 这行的位置，**整体替换** —— 上一行 `container.hidden = false;`（如果有）也合并进来。读现场代码再对齐。

- [ ] **Step 3: grep 检查**

```bash
grep -n "collapsible-body" web/templates/_task_workbench_scripts.html
grep -n "data-collapsible" web/templates/_task_workbench_scripts.html
grep -n "bootCollapsibleCards" web/templates/_task_workbench_scripts.html
```
Expected：
- `collapsible-body` 至少出现 2 次（push 开头和 `'</div>'`）
- `data-collapsible` 至少 1 次（`container.dataset.collapsible = 'duration-log'`）
- `bootCollapsibleCards` 至少 2 次（定义 + 调用）

- [ ] **Step 4: Commit**

```bash
git add web/templates/_task_workbench_scripts.html
git commit -m "$(cat <<'EOF'
feat(duration-log): collapsible card with localStorage-persisted state

Wraps the rendered Duration Log in the new generic collapsible-card
markup. Default expanded; user-toggled state persists per taskId.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 10 — 端到端手动验收

**Files:** —（无代码改动）

> 最后一道闸：必须在浏览器里实际跑一条多语种翻译任务，肉眼确认所有改动符合 spec 的 §8 验证条目。

- [ ] **Step 1: 部署到 LocalServer（172.30.254.14）**

> 这一步得等 master 合并后才能做。先合并、再部署。

```bash
# 切回主 worktree
git switch master  # 在 G:/Code/AutoVideoSrtLocal 主目录里
git merge --no-ff feature/tts-streaming-progress -m "merge feature/tts-streaming-progress"
git push origin master
ssh -i C:/Users/admin/.ssh/CC.pem -o StrictHostKeyChecking=no root@172.30.254.14 \
  'cd /opt/autovideosrt && git pull && systemctl restart autovideosrt-web && systemctl status autovideosrt-web --no-pager | head -20'
```

- [ ] **Step 2: 浏览器跑一条任务 — 流式进度可见**

打开 `http://172.30.254.14/multi-translate/<task_id>`，新建或重跑一条 30s 左右的英→中翻译任务。
进入 TTS 步骤后 1 分钟内观察：

- [ ] step-msg 那行从 `加载配音模板` → `第 1 轮 · 切分朗读文案中` → `第 1 轮 · 生成 ElevenLabs 音频 1/N` … `N/N` → `校验语言 / 测量时长`，每条变化间隔 ≤ 5s。
- [ ] Duration Log 当前 round 卡片"正在生成 TTS 音频"那行**带 X/Y 计数**且**实时递增**。
- [ ] 任务完成后，最终摘要"✓ 收敛成功"或"✨ 最终采用"标志正常显示。

- [ ] **Step 3: 折叠 / 展开行为**

- [ ] 点 Duration Log header 任意位置 → 卡片折叠（仅剩 header）。
- [ ] 再点 → 展开。
- [ ] 折叠态下刷新页面 → 仍折叠。
- [ ] 切到另一个任务 ID → 默认展开（互不影响）。

- [ ] **Step 4: 日语任务回归**

任意跑一条日语翻译任务，确认：
- [ ] step-msg 也有 `生成 ElevenLabs 音频 X/Y` 实时刷新
- [ ] Duration Log 行为与多语种任务一致

- [ ] **Step 5: 失败路径回归**

人工杀掉 ElevenLabs API key（admin 后台配错一个），跑一条任务，确认：
- [ ] TTS 步骤进入 error 状态，错误信息正常显示
- [ ] step-msg 不会卡在某条子步骤上（最后一条 EVT_STEP_UPDATE 是错误信息）

- [ ] **Step 6: worktree 清理**

按 CLAUDE.md「worktree 完成后的固定收尾顺序」：

```bash
# 在主 worktree 里
git worktree remove .worktrees/tts-streaming-progress
git branch -d feature/tts-streaming-progress
```

---

## Self-Review 检查表（写完计划后自检）

- ✅ Spec §3 总体方案 → Task 1-9 全覆盖
- ✅ Spec §4.1 `on_segment_done` 契约 → Task 1
- ✅ Spec §4.2 `_emit_substep_msg` → Task 2
- ✅ Spec §4.3 入口 `加载配音模板` → Task 3
- ✅ Spec §4.4 各 phase substep → Task 4 + 5
- ✅ Spec §4.5 audio_segments_done/total 字段 → Task 4
- ✅ Spec §4.6 日语 runtime → Task 6
- ✅ Spec §5.2 `_phaseLabel` 计数 → Task 7
- ✅ Spec §5.3 通用 `collapsible-card` → Task 8 + 9
- ✅ 端到端验收 → Task 10
- ✅ 无 TBD / TODO / 占位
- ✅ 类型/方法名前后一致：`_emit_substep_msg`、`on_segment_done`、`audio_segments_done/total`、`bootCollapsibleCards`、`collapsible-card[data-collapsible]`
- ✅ 全部步骤都有具体代码 / 命令 / 期望输出
