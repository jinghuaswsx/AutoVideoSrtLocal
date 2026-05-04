# TTS 并发生成 + 跨任务全局并发上限 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 ElevenLabs TTS 的串行调用改成进程级单例线程池并发，所有翻译任务共享同一个全局并发上限（默认 12，硬上限 15）；同时让"排队中"状态在前端可见，跨 5 个 TTS 调用方（多语言视频翻译 / 全能翻译 / 音画同步 / 日语 / 文案配音）统一展示。

**Architecture:**
- 进程级单例 `ThreadPoolExecutor(max_workers=12)` 定义在 [pipeline/tts.py](../../../pipeline/tts.py)。所有 runtime 调用 `generate_full_audio` 时把 segments submit 到这个池。
- `generate_full_audio` 维护 `{active, queued, done}` 计数 + `state_lock`，通过新的 `on_progress(snapshot)` 回调向外推 `submitted/started/completed` 状态。
- 公共 helper `make_tts_progress_emitter` 把 snapshot 转成统一中文 substep 文案；5 处 runtime 都改用它。

**Tech Stack:** Python 3.14, `concurrent.futures.ThreadPoolExecutor`, `threading.Lock`, ElevenLabs Python SDK, pytest, Flask（admin UI）。

**Spec:** [docs/superpowers/specs/2026-05-04-tts-concurrent-generation-design.md](../specs/2026-05-04-tts-concurrent-generation-design.md)

---

## Task 1: 全局 TTS 线程池 + 配置解析

**Files:**
- Modify: [pipeline/tts.py](../../../pipeline/tts.py)（新增模块级单例）
- Test: [tests/test_tts_concurrent_generation.py](../../../tests/test_tts_concurrent_generation.py)（新建）

- [ ] **Step 1: 写失败测试**

新建 `tests/test_tts_concurrent_generation.py`：

```python
"""TTS 并发生成相关单测：线程池单例、配置解析、429 退避、并发提交、回调状态机。"""
from __future__ import annotations

import threading
from unittest.mock import patch

import pytest

from pipeline import tts


def test_resolve_tts_max_concurrency_default(monkeypatch):
    monkeypatch.setattr("appcore.settings.get_setting", lambda key: None)
    assert tts._resolve_tts_max_concurrency() == 12


def test_resolve_tts_max_concurrency_user_override(monkeypatch):
    monkeypatch.setattr("appcore.settings.get_setting", lambda key: "8")
    assert tts._resolve_tts_max_concurrency() == 8


def test_resolve_tts_max_concurrency_clamps_above_hard_cap(monkeypatch):
    monkeypatch.setattr("appcore.settings.get_setting", lambda key: "100")
    assert tts._resolve_tts_max_concurrency() == 15  # ElevenLabs Business 套餐硬上限


def test_resolve_tts_max_concurrency_clamps_below_one(monkeypatch):
    monkeypatch.setattr("appcore.settings.get_setting", lambda key: "0")
    assert tts._resolve_tts_max_concurrency() == 1


def test_resolve_tts_max_concurrency_invalid_string(monkeypatch):
    monkeypatch.setattr("appcore.settings.get_setting", lambda key: "not-a-number")
    assert tts._resolve_tts_max_concurrency() == 12  # 兜底默认


def test_get_tts_pool_is_singleton(monkeypatch):
    monkeypatch.setattr("appcore.settings.get_setting", lambda key: None)
    # 重置模块级单例
    tts._TTS_POOL = None
    pool1 = tts._get_tts_pool()
    pool2 = tts._get_tts_pool()
    assert pool1 is pool2
    assert pool1._max_workers == 12  # 默认值


def test_get_tts_pool_uses_resolved_max_workers(monkeypatch):
    monkeypatch.setattr("appcore.settings.get_setting", lambda key: "5")
    tts._TTS_POOL = None
    pool = tts._get_tts_pool()
    assert pool._max_workers == 5
```

- [ ] **Step 2: 跑测试确认 fail**

```bash
cd .worktrees/tts-concurrent-generation
python -m pytest tests/test_tts_concurrent_generation.py -v 2>&1 | tail -20
```

Expected: 6 个测试全部 FAIL（`AttributeError: module 'pipeline.tts' has no attribute '_resolve_tts_max_concurrency'` / `_get_tts_pool` / `_TTS_POOL`）。

- [ ] **Step 3: 实现**

在 [pipeline/tts.py](../../../pipeline/tts.py) 顶部 import 区追加：

```python
import atexit
from concurrent.futures import ThreadPoolExecutor, as_completed, Future
```

在 `_NETWORK_RETRY_EXCEPTIONS` 元组定义之后追加：

```python
# ===== 进程级单例 TTS 线程池：跨任务共享 ElevenLabs 并发上限 =====
#
# ElevenLabs Business 套餐并发硬上限 15。默认 12 留 3 路 buffer 给声音库同步等
# 其他子系统。所有翻译任务都向同一个 pool submit segment，自然 FIFO 排队，
# 物理上不可能超过 max_workers，避免集体 429。

_TTS_POOL: ThreadPoolExecutor | None = None
_TTS_POOL_LOCK = threading.Lock()
_DEFAULT_TTS_MAX_CONCURRENCY = 12
_HARD_CAP_TTS_MAX_CONCURRENCY = 15  # ElevenLabs Business tier hard limit


def _resolve_tts_max_concurrency() -> int:
    """从 system settings 读 tts_max_concurrency，默认 12，硬上限 15。"""
    from appcore.settings import get_setting
    raw = get_setting("tts_max_concurrency")
    try:
        n = int(raw) if raw is not None else _DEFAULT_TTS_MAX_CONCURRENCY
    except (TypeError, ValueError):
        n = _DEFAULT_TTS_MAX_CONCURRENCY
    return max(1, min(n, _HARD_CAP_TTS_MAX_CONCURRENCY))


def _get_tts_pool() -> ThreadPoolExecutor:
    global _TTS_POOL
    if _TTS_POOL is None:
        with _TTS_POOL_LOCK:
            if _TTS_POOL is None:
                max_workers = _resolve_tts_max_concurrency()
                _TTS_POOL = ThreadPoolExecutor(
                    max_workers=max_workers,
                    thread_name_prefix="tts-elevenlabs",
                )
                atexit.register(_TTS_POOL.shutdown, wait=True)
    return _TTS_POOL
```

- [ ] **Step 4: 跑测试确认 pass**

```bash
python -m pytest tests/test_tts_concurrent_generation.py -v 2>&1 | tail -15
```

Expected: 6 个测试全部 PASS。

- [ ] **Step 5: Commit**

```bash
git add pipeline/tts.py tests/test_tts_concurrent_generation.py
git -c gc.auto=0 commit -m "$(cat <<'EOF'
feat(tts): add process-level singleton ThreadPoolExecutor for cross-task ElevenLabs concurrency

- _resolve_tts_max_concurrency: 从 system settings 读 tts_max_concurrency，默认 12 硬上限 15
- _get_tts_pool: 进程级单例 ThreadPoolExecutor，atexit drain
- 所有翻译任务共享同一个 pool，物理上不可能超过 max_workers

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: 429 / concurrent_limit_exceeded 退避重试

**Files:**
- Modify: [pipeline/tts.py](../../../pipeline/tts.py)
- Test: [tests/test_tts_concurrent_generation.py](../../../tests/test_tts_concurrent_generation.py)（追加）

- [ ] **Step 1: 写失败测试**

在 `tests/test_tts_concurrent_generation.py` 末尾追加：

```python
class _Fake429Error(Exception):
    """模拟 ElevenLabs SDK 抛出的 429 异常对象。"""
    def __init__(self, body: str = "concurrent_limit_exceeded"):
        super().__init__(body)
        self.status_code = 429
        self.body = body


class _Fake500Error(Exception):
    def __init__(self):
        super().__init__("server error")
        self.status_code = 500


def test_is_concurrent_limit_429_status_code():
    assert tts._is_concurrent_limit_429(_Fake429Error("concurrent_limit_exceeded")) is True
    assert tts._is_concurrent_limit_429(_Fake429Error("rate_limit_exceeded")) is True
    assert tts._is_concurrent_limit_429(_Fake500Error()) is False
    assert tts._is_concurrent_limit_429(ValueError("nope")) is False


def test_call_with_throttle_retry_succeeds_eventually(monkeypatch):
    """前两次 429，第三次成功。"""
    sleeps: list[float] = []
    monkeypatch.setattr(tts.time, "sleep", lambda s: sleeps.append(s))

    calls = {"n": 0}
    def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise _Fake429Error()
        return "ok"

    assert tts._call_with_throttle_retry(flaky) == "ok"
    assert calls["n"] == 3
    assert sleeps == [0.5, 1.0]  # 头两次失败的退避


def test_call_with_throttle_retry_exhausts(monkeypatch):
    """全部尝试都 429 → 最终抛 429 异常。"""
    monkeypatch.setattr(tts.time, "sleep", lambda s: None)

    def always_429():
        raise _Fake429Error()

    with pytest.raises(_Fake429Error):
        tts._call_with_throttle_retry(always_429)


def test_call_with_throttle_retry_passes_non_429_through(monkeypatch):
    """非 429 错误立即透传，不重试。"""
    sleeps: list[float] = []
    monkeypatch.setattr(tts.time, "sleep", lambda s: sleeps.append(s))

    calls = {"n": 0}
    def boom():
        calls["n"] += 1
        raise _Fake500Error()

    with pytest.raises(_Fake500Error):
        tts._call_with_throttle_retry(boom)
    assert calls["n"] == 1
    assert sleeps == []
```

- [ ] **Step 2: 跑测试确认 fail**

```bash
python -m pytest tests/test_tts_concurrent_generation.py -v -k "throttle or concurrent_limit" 2>&1 | tail -10
```

Expected: 4 个新测试 FAIL（`_is_concurrent_limit_429` / `_call_with_throttle_retry` 不存在）。

- [ ] **Step 3: 实现**

在 [pipeline/tts.py](../../../pipeline/tts.py) 的 `_call_with_network_retry` 函数定义之后追加：

```python
_THROTTLE_RETRY_DELAYS: tuple[float, ...] = (0.5, 1.0, 2.0, 4.0)


def _is_concurrent_limit_429(exc: BaseException) -> bool:
    """识别 ElevenLabs 的 HTTP 429（concurrent_limit_exceeded / rate_limit_exceeded）。"""
    status = getattr(exc, "status_code", None) or getattr(exc, "status", None)
    if status != 429:
        return False
    body = getattr(exc, "body", None) or getattr(exc, "response", None)
    text = str(body or exc).lower()
    return (
        "concurrent_limit_exceeded" in text
        or "rate_limit_exceeded" in text
        or status == 429  # 拿不到 body 时按 429 直接当节流
    )


def _call_with_throttle_retry(fn, *, label: str = "elevenlabs"):
    """ElevenLabs 429（多任务抢并发）专用退避：0.5/1/2/4s 总计 4 次。
    非 429 错误透传，由 _call_with_network_retry 再处理网络层瞬时故障。"""
    for attempt in range(len(_THROTTLE_RETRY_DELAYS)):
        try:
            return fn()
        except BaseException as exc:
            if not _is_concurrent_limit_429(exc):
                raise
            if attempt >= len(_THROTTLE_RETRY_DELAYS) - 1:
                log.exception("%s throttle retry exhausted: %s", label, exc)
                raise
            delay = _THROTTLE_RETRY_DELAYS[attempt]
            log.warning("%s 429 throttle, retry in %.1fs: %s", label, delay, exc)
            time.sleep(delay)
```

- [ ] **Step 4: 跑测试确认 pass**

```bash
python -m pytest tests/test_tts_concurrent_generation.py -v 2>&1 | tail -15
```

Expected: 全部 10 个测试 PASS。

- [ ] **Step 5: Commit**

```bash
git add pipeline/tts.py tests/test_tts_concurrent_generation.py
git -c gc.auto=0 commit -m "$(cat <<'EOF'
feat(tts): add 429 / concurrent_limit_exceeded throttle retry helper

_call_with_throttle_retry 识别 ElevenLabs 的 429 错误（concurrent_limit_exceeded /
rate_limit_exceeded），按 0.5/1/2/4s 退避重试，非 429 错误立即透传。

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: 把 throttle retry 接入 generate_segment_audio

**Files:**
- Modify: [pipeline/tts.py:118-163](../../../pipeline/tts.py)（generate_segment_audio）

- [ ] **Step 1: 写失败测试**

在 `tests/test_tts_concurrent_generation.py` 末尾追加：

```python
def test_generate_segment_audio_retries_on_429(monkeypatch, tmp_path):
    """generate_segment_audio 收到 429 应该走 throttle retry。"""
    monkeypatch.setattr(tts.time, "sleep", lambda s: None)

    call_count = {"n": 0}
    def fake_convert(**kwargs):
        call_count["n"] += 1
        if call_count["n"] < 2:
            raise _Fake429Error()
        return iter([b"audio-bytes"])

    class FakeClient:
        class text_to_speech:
            convert = staticmethod(fake_convert)

    monkeypatch.setattr(tts, "_get_client", lambda api_key=None: FakeClient())
    out = tmp_path / "seg.mp3"
    tts.generate_segment_audio(text="hi", voice_id="v1", output_path=str(out),
                                elevenlabs_api_key="fake")
    assert out.exists()
    assert out.read_bytes() == b"audio-bytes"
    assert call_count["n"] == 2  # 第 1 次 429 + 第 2 次成功
```

- [ ] **Step 2: 跑测试确认 fail**

```bash
python -m pytest tests/test_tts_concurrent_generation.py::test_generate_segment_audio_retries_on_429 -v 2>&1 | tail -10
```

Expected: FAIL（当前 `_call_with_network_retry` 不识别 429，第一次就抛）。

- [ ] **Step 3: 实现**

修改 [pipeline/tts.py:152-159](../../../pipeline/tts.py) 的 `generate_segment_audio` 内部调用，把 `_do_tts_call` 同时套上 throttle retry 和 network retry：

把：
```python
    def _do_tts_call() -> bytes:
        chunks = client.text_to_speech.convert(**kwargs)
        return b"".join(chunks)

    audio_bytes = _call_with_network_retry(
        _do_tts_call,
        label="elevenlabs.text_to_speech",
    )
```

改为：
```python
    def _do_tts_call() -> bytes:
        chunks = client.text_to_speech.convert(**kwargs)
        return b"".join(chunks)

    # 外层：429（多任务抢并发）退避；内层：网络瞬时抖动退避。
    # 顺序很重要——429 是 HTTP 层错误，网络 retry 不识别它。
    audio_bytes = _call_with_throttle_retry(
        lambda: _call_with_network_retry(
            _do_tts_call,
            label="elevenlabs.text_to_speech",
        ),
        label="elevenlabs.text_to_speech",
    )
```

- [ ] **Step 4: 跑测试确认 pass**

```bash
python -m pytest tests/test_tts_concurrent_generation.py -v 2>&1 | tail -15
```

Expected: 全部 11 个测试 PASS。同时跑现有 [tests/test_tts_pipeline.py](../../../tests/test_tts_pipeline.py) 确保没回归：

```bash
python -m pytest tests/test_tts_pipeline.py -v 2>&1 | tail -15
```

Expected: 现有 tts_pipeline 测试全绿（throttle retry 对原路径透明）。

- [ ] **Step 5: Commit**

```bash
git add pipeline/tts.py tests/test_tts_concurrent_generation.py
git -c gc.auto=0 commit -m "$(cat <<'EOF'
feat(tts): wire throttle retry into generate_segment_audio

把 _call_with_throttle_retry 包在 _call_with_network_retry 外层，让
ElevenLabs 429 / concurrent_limit_exceeded 走专用退避序列；网络瞬时
抖动仍走原来的 _call_with_network_retry 处理。

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: 改造 generate_full_audio 为并发提交 + on_progress 回调

**Files:**
- Modify: [pipeline/tts.py:166-230](../../../pipeline/tts.py)（generate_full_audio）
- Test: [tests/test_tts_concurrent_generation.py](../../../tests/test_tts_concurrent_generation.py)（追加并发用例）

这是核心改造。代码量较大，但语义在 spec §3.6 的伪代码里已经全部展开。

- [ ] **Step 1: 写失败测试**

在 `tests/test_tts_concurrent_generation.py` 末尾追加：

```python
import os
import subprocess
from concurrent.futures import ThreadPoolExecutor


@pytest.fixture
def fake_segment_audio(monkeypatch):
    """让 generate_segment_audio 不真打 ElevenLabs，只 sleep 一小段写一个 mp3 文件。"""
    import time as _time

    def _fake(text, voice_id, output_path, **kwargs):
        # 模拟单段耗时（让并发性可观察）
        _time.sleep(0.05)
        os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
        with open(output_path, "wb") as f:
            f.write(b"fake-mp3")
        return output_path

    monkeypatch.setattr(tts, "generate_segment_audio", _fake)
    return _fake


@pytest.fixture
def fake_audio_duration(monkeypatch):
    monkeypatch.setattr(tts, "_get_audio_duration", lambda path: 1.5)


@pytest.fixture
def fake_ffmpeg(monkeypatch):
    """跳过真 ffmpeg concat。"""
    class _R:
        returncode = 0
        stderr = ""
    monkeypatch.setattr(tts.subprocess, "run", lambda *a, **kw: _R())


def test_generate_full_audio_emits_submitted_started_completed(
    monkeypatch, tmp_path, fake_segment_audio, fake_audio_duration, fake_ffmpeg,
):
    """on_progress 应按 submitted → started* → completed* 顺序触发。"""
    monkeypatch.setattr("appcore.settings.get_setting", lambda key: "4")
    tts._TTS_POOL = None  # 用新 max_workers=4 重建 pool

    snapshots = []
    def collect(snap):
        snapshots.append(dict(snap))

    segments = [{"tts_text": f"line {i}"} for i in range(6)]
    out = tts.generate_full_audio(
        segments, voice_id="v1", output_dir=str(tmp_path),
        on_progress=collect,
    )

    states = [s["state"] for s in snapshots]
    # 第 1 个事件必须是 submitted
    assert states[0] == "submitted"
    # 至少有 6 个 started 和 6 个 completed
    assert states.count("started") == 6
    assert states.count("completed") == 6
    # 完结后状态：done=6, active=0, queued=0
    last = snapshots[-1]
    assert last["done"] == 6
    assert last["active"] == 0
    assert last["queued"] == 0
    assert last["total"] == 6
    # 输出 segments 顺序按 i（concat 顺序）
    assert [s["tts_path"] for s in out["segments"]] == [
        os.path.join(str(tmp_path), "tts_segments", f"seg_{i:04d}.mp3")
        for i in range(6)
    ]


def test_generate_full_audio_active_never_exceeds_max_workers(
    monkeypatch, tmp_path, fake_segment_audio, fake_audio_duration, fake_ffmpeg,
):
    """同时活跃的段数不可能超过 pool max_workers。"""
    monkeypatch.setattr("appcore.settings.get_setting", lambda key: "3")
    tts._TTS_POOL = None

    peak_active = {"v": 0}
    def watch(snap):
        if snap["active"] > peak_active["v"]:
            peak_active["v"] = snap["active"]

    segments = [{"tts_text": f"line {i}"} for i in range(10)]
    tts.generate_full_audio(segments, voice_id="v1", output_dir=str(tmp_path),
                             on_progress=watch)
    assert peak_active["v"] <= 3


def test_generate_full_audio_concat_order_by_index(
    monkeypatch, tmp_path, fake_audio_duration, fake_ffmpeg,
):
    """乱序完成，但 concat.txt 必须按 i 升序写入（音轨时序）。"""
    import time as _time

    def slow_for_index_zero(text, voice_id, output_path, **kwargs):
        # 让 i=0 比其他段慢一倍，确保完成顺序乱
        if "0000" in output_path:
            _time.sleep(0.15)
        else:
            _time.sleep(0.02)
        os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
        with open(output_path, "wb") as f:
            f.write(b"fake")
        return output_path

    monkeypatch.setattr(tts, "generate_segment_audio", slow_for_index_zero)
    monkeypatch.setattr("appcore.settings.get_setting", lambda key: "4")
    tts._TTS_POOL = None

    segments = [{"tts_text": f"line {i}"} for i in range(5)]
    tts.generate_full_audio(segments, voice_id="v1", output_dir=str(tmp_path),
                             on_progress=None)

    concat_path = tmp_path / "tts_segments" / "concat.txt"
    lines = concat_path.read_text().splitlines()
    expected_basenames = [f"seg_{i:04d}.mp3" for i in range(5)]
    actual_basenames = [os.path.basename(line.split("'")[1]) for line in lines]
    assert actual_basenames == expected_basenames


def test_generate_full_audio_first_failure_propagates(
    monkeypatch, tmp_path, fake_audio_duration, fake_ffmpeg,
):
    """任一段抛错 → cancel 其余 + 抛 RuntimeError。"""
    def fail_index_2(text, voice_id, output_path, **kwargs):
        if "0002" in output_path:
            raise ValueError("synthetic failure")
        os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
        with open(output_path, "wb") as f:
            f.write(b"fake")
        return output_path

    monkeypatch.setattr(tts, "generate_segment_audio", fail_index_2)
    monkeypatch.setattr("appcore.settings.get_setting", lambda key: "4")
    tts._TTS_POOL = None

    segments = [{"tts_text": f"line {i}"} for i in range(5)]
    with pytest.raises(RuntimeError, match="TTS segment generation failed at index 2"):
        tts.generate_full_audio(segments, voice_id="v1", output_dir=str(tmp_path))


def test_generate_full_audio_legacy_on_segment_done_still_called(
    monkeypatch, tmp_path, fake_segment_audio, fake_audio_duration, fake_ffmpeg,
):
    """旧 on_segment_done 接口要继续兼容（向下不破）。"""
    monkeypatch.setattr("appcore.settings.get_setting", lambda key: "4")
    tts._TTS_POOL = None

    legacy_calls = []
    def legacy_cb(done, total, info):
        legacy_calls.append((done, total, info.get("segment_index")))

    segments = [{"tts_text": f"line {i}"} for i in range(4)]
    tts.generate_full_audio(segments, voice_id="v1", output_dir=str(tmp_path),
                             on_segment_done=legacy_cb)
    assert len(legacy_calls) == 4
    # done 单调递增，最后一次 done == total
    dones = [c[0] for c in legacy_calls]
    assert dones == sorted(dones)
    assert dones[-1] == 4
    assert all(c[1] == 4 for c in legacy_calls)


def test_generate_full_audio_progress_callback_exception_does_not_break(
    monkeypatch, tmp_path, fake_segment_audio, fake_audio_duration, fake_ffmpeg,
):
    """on_progress 回调抛错应被吞掉，不影响主流程。"""
    monkeypatch.setattr("appcore.settings.get_setting", lambda key: "2")
    tts._TTS_POOL = None

    def angry(snap):
        raise RuntimeError("callback explodes")

    segments = [{"tts_text": f"line {i}"} for i in range(3)]
    out = tts.generate_full_audio(segments, voice_id="v1", output_dir=str(tmp_path),
                                    on_progress=angry)
    assert len(out["segments"]) == 3
```

- [ ] **Step 2: 跑测试确认 fail**

```bash
python -m pytest tests/test_tts_concurrent_generation.py -v -k "generate_full_audio" 2>&1 | tail -25
```

Expected: 6 个新测试 FAIL（旧串行版本不调 on_progress、不接 `on_progress` kwarg、错误处理不一样等等）。

- [ ] **Step 3: 实现**

把 [pipeline/tts.py:166-230](../../../pipeline/tts.py) 的整个 `generate_full_audio` 函数替换为：

```python
def generate_full_audio(
    segments: List[Dict],
    voice_id: str,
    output_dir: str,
    *,
    variant: str | None = None,
    elevenlabs_api_key: str | None = None,
    model_id: str = "eleven_turbo_v2_5",
    language_code: str | None = None,
    on_progress: Callable[[dict], None] | None = None,
    on_segment_done: Optional[Callable[[int, int, dict], None]] = None,
) -> Dict:
    """
    为所有翻译段落生成音频并拼接成完整音轨（并发提交到全局 TTS 线程池）。

    Args:
        on_progress: 新接口。每次状态变化触发 (state ∈ submitted/started/completed)，
            snapshot = {state, total, done, active, queued, info}。回调抛出的异常会
            被吞掉。
        on_segment_done: 兼容旧接口。每段完成后调用 (done, total, info)。回调抛出的
            异常会被吞掉。两者可同时传，会都被调用。

    Returns:
        {"full_audio_path": str, "segments": [...]}  # 每段新增 tts_path, tts_duration
    """
    seg_dir = (
        os.path.join(output_dir, "tts_segments", variant)
        if variant else os.path.join(output_dir, "tts_segments")
    )
    os.makedirs(seg_dir, exist_ok=True)

    total = len(segments)
    pool = _get_tts_pool()

    state = {"total": total, "active": 0, "queued": total, "done": 0}
    state_lock = threading.Lock()

    def _emit_progress(reason: str, info: dict | None = None) -> None:
        if on_progress is None:
            return
        with state_lock:
            snapshot = {
                "state": reason,
                "total": state["total"],
                "active": state["active"],
                "queued": state["queued"],
                "done": state["done"],
                "info": info or {},
            }
        try:
            on_progress(snapshot)
        except Exception:
            log.exception("on_progress callback raised; ignoring")

    def _segment_wrapper(text: str, seg_path: str) -> tuple[str, float]:
        with state_lock:
            state["active"] += 1
            state["queued"] -= 1
        _emit_progress("started", {"text_preview": (text or "")[:60]})
        try:
            generate_segment_audio(
                text, voice_id, seg_path,
                elevenlabs_api_key=elevenlabs_api_key,
                model_id=model_id, language_code=language_code,
            )
            duration = _get_audio_duration(seg_path)
            return seg_path, duration
        finally:
            with state_lock:
                state["active"] -= 1

    # 1. 提交全部 segment 到全局 pool（受 max_workers 限流）
    tasks: list[tuple[int, dict, str, str, Future]] = []
    for i, seg in enumerate(segments):
        text = seg.get("tts_text") or seg.get("translated") or seg.get("text", "")
        seg_path = os.path.join(seg_dir, f"seg_{i:04d}.mp3")
        future = pool.submit(_segment_wrapper, text, seg_path)
        tasks.append((i, seg, text, seg_path, future))

    # 2. submit 完毕：emit submitted。此时 active=0、queued=total、done=0 → 前端"排队中"
    _emit_progress("submitted")

    # 3. as_completed 收回（按完成时间，不按 i 顺序）
    seg_results: dict[int, dict] = {}
    failures: list[tuple[int, BaseException]] = []
    future_to_meta = {t[4]: t for t in tasks}
    for fut in as_completed([t[4] for t in tasks]):
        i, seg, text, seg_path, _ = future_to_meta[fut]
        try:
            _, duration = fut.result()
        except BaseException as exc:
            failures.append((i, exc))
            continue
        seg_copy = dict(seg)
        seg_copy["tts_path"] = seg_path
        seg_copy["tts_duration"] = duration
        seg_results[i] = seg_copy

        with state_lock:
            state["done"] += 1
            done_now = state["done"]
        info = {
            "segment_index": i,
            "tts_duration": duration,
            "tts_text_preview": (text or "")[:60],
        }
        _emit_progress("completed", info)
        if on_segment_done is not None:
            try:
                on_segment_done(done_now, total, info)
            except Exception:
                log.exception("on_segment_done callback raised; ignoring")

    if failures:
        for _, _, _, _, f in tasks:
            f.cancel()
        first_idx, first_exc = failures[0]
        raise RuntimeError(
            f"TTS segment generation failed at index {first_idx} "
            f"({len(failures)}/{total} failed): {first_exc}"
        ) from first_exc

    # 4. 按 i 顺序拼 concat 列表（保持音轨时序）
    updated_segments = [seg_results[i] for i in range(total)]
    concat_list_path = os.path.join(seg_dir, "concat.txt")
    with open(concat_list_path, "w", encoding="utf-8") as concat_f:
        for seg_copy in updated_segments:
            concat_f.write(f"file '{os.path.abspath(seg_copy['tts_path'])}'\n")

    # 5. ffmpeg concat（不变）
    full_audio_name = f"tts_full.{variant}.mp3" if variant else "tts_full.mp3"
    full_audio_path = os.path.join(output_dir, full_audio_name)
    result = subprocess.run(
        ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", concat_list_path,
         "-c", "copy", full_audio_path],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        raise RuntimeError(f"音频拼接失败: {result.stderr}")

    return {"full_audio_path": full_audio_path, "segments": updated_segments}
```

- [ ] **Step 4: 跑测试确认 pass**

```bash
python -m pytest tests/test_tts_concurrent_generation.py -v 2>&1 | tail -25
```

Expected: 全部 17 个测试 PASS。

跑现有 TTS 相关测试确保没破坏：

```bash
python -m pytest tests/test_tts_pipeline.py tests/test_tts_duration_loop.py -v 2>&1 | tail -25
```

Expected: 现有测试全绿。如果 `tests/test_tts_duration_loop.py` 因为 mock 没 `on_progress` kwarg 而 fail，就把 fake `generate_full_audio` mock 加上 `**kwargs` 接受即可。

- [ ] **Step 5: Commit**

```bash
git add pipeline/tts.py tests/test_tts_concurrent_generation.py
git -c gc.auto=0 commit -m "$(cat <<'EOF'
feat(tts): rewrite generate_full_audio for concurrent submission via global pool

- 把每个 segment submit 到 _get_tts_pool() 单例（max_workers=12，硬上限 15）
- 维护 active/queued/done 计数 + state_lock，线程安全
- 新接口 on_progress(snapshot) 推 submitted/started/completed 状态机
- 旧 on_segment_done(done, total, info) 接口保留兼容
- concat.txt 按 i 顺序写（不按完成顺序），保证音轨时序
- 任一段失败立即 cancel 其余 + 抛 RuntimeError，行为与原顺序版一致

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: make_tts_progress_emitter 公共 helper

**Files:**
- Modify: [appcore/runtime/_helpers.py](../../../appcore/runtime/_helpers.py)
- Test: `tests/test_tts_progress_emitter.py`（新建）

- [ ] **Step 1: 写失败测试**

新建 `tests/test_tts_progress_emitter.py`：

```python
"""make_tts_progress_emitter helper 单元测试。"""
from __future__ import annotations

from unittest.mock import MagicMock

from appcore.runtime._helpers import make_tts_progress_emitter


def _snap(state, total, active, queued, done, info=None):
    return {
        "state": state, "total": total, "active": active,
        "queued": queued, "done": done, "info": info or {},
    }


def test_emitter_says_queued_when_no_active_no_done():
    runner = MagicMock()
    emitter = make_tts_progress_emitter(runner, "task-1", lang_label="西班牙语")
    emitter(_snap("submitted", total=70, active=0, queued=70, done=0))
    runner._emit_substep_msg.assert_called_once()
    args = runner._emit_substep_msg.call_args[0]
    assert args[0] == "task-1"
    assert args[1] == "tts"
    assert "西班牙语配音" in args[2]
    assert "排队中" in args[2]
    assert "70 段待派发" in args[2]


def test_emitter_says_progress_after_first_started():
    runner = MagicMock()
    emitter = make_tts_progress_emitter(runner, "task-1", lang_label="西班牙语")
    emitter(_snap("started", total=70, active=1, queued=69, done=0))
    msg = runner._emit_substep_msg.call_args[0][2]
    assert "西班牙语配音" in msg
    assert "0/70" in msg
    assert "活跃 1 路" in msg
    assert "排队中" not in msg


def test_emitter_says_progress_during_completion():
    runner = MagicMock()
    emitter = make_tts_progress_emitter(runner, "task-1", lang_label="德语")
    emitter(_snap("completed", total=70, active=11, queued=47, done=12))
    msg = runner._emit_substep_msg.call_args[0][2]
    assert "12/70" in msg
    assert "活跃 11 路" in msg


def test_emitter_uses_round_label():
    runner = MagicMock()
    emitter = make_tts_progress_emitter(
        runner, "task-1", lang_label="法语", round_label="第 2 轮",
    )
    emitter(_snap("started", total=10, active=1, queued=9, done=0))
    msg = runner._emit_substep_msg.call_args[0][2]
    assert "法语配音" in msg
    assert "第 2 轮" in msg


def test_emitter_calls_extra_state_update():
    runner = MagicMock()
    extra_calls = []
    emitter = make_tts_progress_emitter(
        runner, "task-1", lang_label="西班牙语",
        extra_state_update=lambda snap: extra_calls.append(snap["done"]),
    )
    emitter(_snap("completed", total=10, active=5, queued=4, done=1))
    emitter(_snap("completed", total=10, active=4, queued=3, done=2))
    assert extra_calls == [1, 2]


def test_emitter_swallows_extra_update_exception():
    runner = MagicMock()
    def boom(snap): raise RuntimeError("kaboom")
    emitter = make_tts_progress_emitter(
        runner, "task-1", lang_label="西班牙语",
        extra_state_update=boom,
    )
    # 不抛错就 OK
    emitter(_snap("completed", total=10, active=5, queued=4, done=1))
```

- [ ] **Step 2: 跑测试确认 fail**

```bash
python -m pytest tests/test_tts_progress_emitter.py -v 2>&1 | tail -10
```

Expected: 6 个测试全部 FAIL（`make_tts_progress_emitter` 还没实现）。

- [ ] **Step 3: 实现**

打开 [appcore/runtime/_helpers.py](../../../appcore/runtime/_helpers.py)，在文件末尾追加（不要替换现有内容）：

```python
import logging as _logging
from typing import Callable as _Callable

_progress_log = _logging.getLogger(__name__)


def make_tts_progress_emitter(
    runner,
    task_id: str,
    *,
    lang_label: str,
    round_label: str = "",
    extra_state_update: _Callable[[dict], None] | None = None,
) -> _Callable[[dict], None]:
    """生成 generate_full_audio(on_progress=...) 用的标准回调，把 snapshot
    转成统一中文 substep 文案推到前端。

    Args:
        runner: 任何提供 _emit_substep_msg(task_id, step, msg) 的 runtime 实例。
        task_id: 任务 ID，用于 substep 路由。
        lang_label: 语言显示名（例如 "西班牙语"），拼进文案前缀。
        round_label: 可选轮次标签（例如 "第 2 轮"），拼进文案前缀。
        extra_state_update: 可选回调，每次 emit 时同步给一份 snapshot
            （用于 _pipeline_runner 同步更新 round_record["audio_segments_done"]）。
            抛出的异常会被吞掉。
    """
    def _emit(snapshot: dict) -> None:
        active = snapshot.get("active", 0)
        done = snapshot.get("done", 0)
        total = snapshot.get("total", 0)
        queued = snapshot.get("queued", 0)

        prefix = f"正在生成{lang_label}配音"
        if round_label:
            prefix = f"{prefix} · {round_label}"

        if active == 0 and done == 0 and total > 0:
            msg = f"{prefix} · 排队中等待 ElevenLabs 并发槽位（{queued} 段待派发）"
        else:
            msg = f"{prefix} · {done}/{total}（活跃 {active} 路）"

        runner._emit_substep_msg(task_id, "tts", msg)
        if extra_state_update is not None:
            try:
                extra_state_update(snapshot)
            except Exception:
                _progress_log.exception(
                    "extra_state_update raised in tts progress emitter; ignoring"
                )

    return _emit
```

- [ ] **Step 4: 跑测试确认 pass**

```bash
python -m pytest tests/test_tts_progress_emitter.py -v 2>&1 | tail -15
```

Expected: 6 个测试全部 PASS。

- [ ] **Step 5: Commit**

```bash
git add appcore/runtime/_helpers.py tests/test_tts_progress_emitter.py
git -c gc.auto=0 commit -m "$(cat <<'EOF'
feat(runtime): add make_tts_progress_emitter helper for unified TTS substep messages

5 个 TTS 调用方共用同一个 helper：
- 排队中："正在生成X配音 · 排队中等待 ElevenLabs 并发槽位（N 段待派发）"
- 进度："正在生成X配音 · done/total（活跃 N 路）"
- 可选 round_label / extra_state_update（多语言视频翻译用它同步 round_record）

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: 接入多语言视频翻译 runtime（_pipeline_runner.py）

**Files:**
- Modify: [appcore/runtime/_pipeline_runner.py:534-555](../../../appcore/runtime/_pipeline_runner.py)
- Test: [tests/test_pipeline_runner.py:494-496](../../../tests/test_pipeline_runner.py)（fake_generate_full_audio 兼容 on_progress kwarg）

- [ ] **Step 1: 检查现有测试 mock 是否需要适配**

```bash
python -m pytest tests/test_pipeline_runner.py -v 2>&1 | tail -15
```

如果有测试 fail（因为 fake `generate_full_audio` 不接受 `on_progress`），打开 [tests/test_pipeline_runner.py:494](../../../tests/test_pipeline_runner.py)，把：

```python
def fake_generate_full_audio(tts_segments, voice_id, output_dir, variant="normal", **kwargs):
```

确认它已经有 `**kwargs`（应该已经有）。如果没有，加上 `**kwargs` 让它吞掉新增的 `on_progress`。

- [ ] **Step 2: 修改 _pipeline_runner.py**

打开 [appcore/runtime/_pipeline_runner.py:534-555](../../../appcore/runtime/_pipeline_runner.py)，把：

```python
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
```

替换为：

```python
            tts_segments = loc_mod.build_tts_segments(tts_script, script_segments)
            round_record["audio_segments_total"] = len(tts_segments)
            round_record["audio_segments_done"] = 0
            _substep(f"生成 ElevenLabs 音频 0/{len(tts_segments)}")
            self._emit_duration_round(task_id, round_index, "audio_gen", round_record)

            from appcore.runtime._helpers import make_tts_progress_emitter

            def _sync_round_record(snap):
                round_record["audio_segments_done"] = snap["done"]
                round_record["audio_segments_total"] = snap["total"]
                self._emit_duration_round(task_id, round_index, "audio_gen", round_record)

            on_progress = make_tts_progress_emitter(
                self, task_id,
                lang_label=_lang_display(target_language_label),
                round_label=f"第 {round_index} 轮",
                extra_state_update=_sync_round_record,
            )

            result = generate_full_audio(
                tts_segments, voice["elevenlabs_voice_id"], task_dir,
                variant=f"round_{round_index}",
                elevenlabs_api_key=elevenlabs_api_key,
                model_id=tts_model_id,
                language_code=tts_language_code,
                on_progress=on_progress,
            )
```

- [ ] **Step 3: 跑测试确认通过**

```bash
python -m pytest tests/test_pipeline_runner.py tests/test_tts_duration_loop.py -v 2>&1 | tail -25
```

Expected: 全部 PASS。如果有 mock 不接受 `on_progress` 报错，加 `**kwargs` 兼容。

- [ ] **Step 4: Commit**

```bash
git add appcore/runtime/_pipeline_runner.py tests/test_pipeline_runner.py tests/test_tts_duration_loop.py
git -c gc.auto=0 commit -m "$(cat <<'EOF'
refactor(multi-translate): wire TTS progress through unified emitter helper

把 _on_seg_done 替换为 make_tts_progress_emitter + extra_state_update
同步 round_record["audio_segments_done"]。文案逻辑（含排队中状态）由 helper 统一产出。

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 7: 接入全能翻译 runtime（runtime/__init__.py）

**Files:**
- Modify: [appcore/runtime/__init__.py:300-309](../../../appcore/runtime/__init__.py)

- [ ] **Step 1: 修改代码**

打开 [appcore/runtime/__init__.py:300-309](../../../appcore/runtime/__init__.py)，把：

```python
        current_step = "tts"
        runner._set_step(task_id, "tts", "running", f"正在生成{target_language_name}配音...")
        tts_input_segments = _build_av_tts_segments(av_sentences)
        tts_output = generate_full_audio(
            tts_input_segments,
            tts_voice_id,
            task_dir,
            variant=variant,
            language_code=target_language,
        )
```

替换为：

```python
        current_step = "tts"
        runner._set_step(task_id, "tts", "running", f"正在生成{target_language_name}配音...")
        tts_input_segments = _build_av_tts_segments(av_sentences)
        from appcore.runtime._helpers import make_tts_progress_emitter
        on_progress = make_tts_progress_emitter(
            runner, task_id, lang_label=target_language_name,
        )
        tts_output = generate_full_audio(
            tts_input_segments,
            tts_voice_id,
            task_dir,
            variant=variant,
            language_code=target_language,
            on_progress=on_progress,
        )
```

- [ ] **Step 2: 跑现有测试确保不破坏**

```bash
python -m pytest tests/test_appcore_runtime.py -v 2>&1 | tail -15
```

Expected: 全绿（如果有 mock 报 unexpected kwarg `on_progress` 就加 `**kwargs`）。

- [ ] **Step 3: Commit**

```bash
git add appcore/runtime/__init__.py tests/
git -c gc.auto=0 commit -m "$(cat <<'EOF'
refactor(omni-translate): emit TTS queue/progress substep messages

全能翻译现在也会显示"排队中等待 ElevenLabs 并发槽位"以及实时进度，
和多语言视频翻译保持一致。

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 8: 接入视频翻译音画同步 runtime（runtime_sentence_translate.py）

**Files:**
- Modify: [appcore/runtime_sentence_translate.py:265-273](../../../appcore/runtime_sentence_translate.py)

- [ ] **Step 1: 修改代码**

打开 [appcore/runtime_sentence_translate.py:265-273](../../../appcore/runtime_sentence_translate.py)，把：

```python
            self._set_step(task_id, "tts", "running", f"正在生成{target_language_name}首轮配音...")
            tts_input_segments = _build_av_tts_segments(av_sentences)
            tts_output = generate_full_audio(
                tts_input_segments,
                tts_voice_id,
                task_dir,
                variant="av",
                language_code=target_language,
            )
```

替换为：

```python
            self._set_step(task_id, "tts", "running", f"正在生成{target_language_name}首轮配音...")
            tts_input_segments = _build_av_tts_segments(av_sentences)
            from appcore.runtime._helpers import make_tts_progress_emitter
            on_progress = make_tts_progress_emitter(
                self, task_id,
                lang_label=target_language_name,
                round_label="首轮",
            )
            tts_output = generate_full_audio(
                tts_input_segments,
                tts_voice_id,
                task_dir,
                variant="av",
                language_code=target_language,
                on_progress=on_progress,
            )
```

- [ ] **Step 2: 跑现有测试确保不破坏**

```bash
python -m pytest tests/test_sentence_translate_runtime.py tests/test_runtime_multi_translate.py -v 2>&1 | tail -15
```

Expected: 全绿（mock 报错的话加 `**kwargs`）。

- [ ] **Step 3: Commit**

```bash
git add appcore/runtime_sentence_translate.py tests/
git -c gc.auto=0 commit -m "$(cat <<'EOF'
refactor(av-sync): emit TTS queue/progress substep messages

视频翻译音画同步首轮配音也接入统一进度 emitter，前端能看到排队中和实时进度。

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 9: 接入日语 runtime（runtime_ja.py）

**Files:**
- Modify: [appcore/runtime_ja.py:292-309](../../../appcore/runtime_ja.py)

- [ ] **Step 1: 修改代码**

打开 [appcore/runtime_ja.py:292-309](../../../appcore/runtime_ja.py)，把：

```python
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

替换为：

```python
            from appcore.runtime._helpers import make_tts_progress_emitter
            on_progress = make_tts_progress_emitter(
                self, task_id,
                lang_label="日语",
                round_label=f"第 {round_index} 轮",
            )
            tts_output = generate_full_audio(
                tts_segments,
                voice_id=voice_id,
                output_dir=task_dir,
                variant=round_variant,
                elevenlabs_api_key=elevenlabs_api_key,
                model_id=self.tts_model_id,
                language_code=self.tts_language_code,
                on_progress=on_progress,
            )
```

- [ ] **Step 2: 跑现有测试确保不破坏**

```bash
python -m pytest tests/test_runtime_ja_shared_shell.py -v 2>&1 | tail -15
```

Expected: 全绿。

- [ ] **Step 3: Commit**

```bash
git add appcore/runtime_ja.py tests/
git -c gc.auto=0 commit -m "$(cat <<'EOF'
refactor(ja-translate): migrate TTS progress to unified emitter helper

日语翻译之前自己写一份 _on_seg_done，现在改用 make_tts_progress_emitter
保持文案一致并自动获得排队中状态。

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 10: 接入文案配音 runtime（copywriting_runtime.py）

**Files:**
- Modify: [appcore/copywriting_runtime.py:201-207](../../../appcore/copywriting_runtime.py)

- [ ] **Step 1: 检查 lang_label 来源**

文案配音任务的语言由 `task.get("language")` 或类似字段提供。先 grep 一下：

```bash
python -m pytest tests/test_appcore_runtime.py -v 2>&1 | tail -5  # 先看下相关测试
```

打开 [appcore/copywriting_runtime.py](../../../appcore/copywriting_runtime.py) 上下文，找到 `_step_tts` 函数定义里能拿到的语言变量。如果没有显式语种，用 `voice` 字典里的语言信息或 fallback 到 `"配音"`（不带语种）：

- [ ] **Step 2: 修改代码**

打开 [appcore/copywriting_runtime.py:201-207](../../../appcore/copywriting_runtime.py)，把：

```python
        result = generate_full_audio(
            segments=tts_segments,
            voice_id=voice["elevenlabs_voice_id"],
            output_dir=task_dir,
            variant="copywriting",
            elevenlabs_api_key=elevenlabs_key,
        )
```

替换为：

```python
        from appcore.runtime._helpers import make_tts_progress_emitter
        # 文案配音没有显式语种字段，用 voice 名称兜底；前端文案会显示"正在生成X配音..."
        lang_label = (voice.get("language_label") or voice.get("language") or "")
        if not lang_label:
            lang_label = ""  # 兜底：文案变成"正在生成配音 · ..."
        on_progress = make_tts_progress_emitter(
            self, task_id, lang_label=lang_label,
        )
        result = generate_full_audio(
            segments=tts_segments,
            voice_id=voice["elevenlabs_voice_id"],
            output_dir=task_dir,
            variant="copywriting",
            elevenlabs_api_key=elevenlabs_key,
            on_progress=on_progress,
        )
```

- [ ] **Step 3: 跑现有测试**

```bash
python -m pytest tests/test_appcore_runtime.py -v -k "copywriting" 2>&1 | tail -15
```

Expected: 全绿（mock 报 unexpected kwarg `on_progress` 就加 `**kwargs`）。

- [ ] **Step 4: Commit**

```bash
git add appcore/copywriting_runtime.py tests/
git -c gc.auto=0 commit -m "$(cat <<'EOF'
refactor(copywriting): emit TTS queue/progress substep messages

文案配音也接入统一 emitter，跨任务并发时前端能看到排队中状态。

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 11: admin /settings 加 tts_max_concurrency 表单字段

**Files:**
- Modify: `web/templates/settings.html`（找现有 system settings 输入区追加一行）
- Modify: `web/routes/admin.py`（POST 处理 `tts_max_concurrency`）
- Test: 手动 + 现有 admin 路由测试

- [ ] **Step 1: 找到现有 system settings 编辑区**

```bash
grep -n "set_setting\|get_setting" web/routes/admin.py | head -20
grep -n "system_settings\|retention\|rmb_per_usd" web/templates/settings.html | head -20
```

看现有的 retention / RMB 配置长什么样，照葫芦画瓢加一行。

- [ ] **Step 2: 在 settings.html 加表单字段**

在现有 system settings 区域追加（HTML 风格参照同区其他字段，遵循 Ocean Blue Design System：`--accent` 海洋蓝、`--radius-md` 圆角、`--font-mono` 数字输入）：

```html
<div class="form-row">
  <label for="tts_max_concurrency">TTS 并发上限</label>
  <input
    type="number"
    id="tts_max_concurrency"
    name="tts_max_concurrency"
    min="1"
    max="15"
    value="{{ tts_max_concurrency or 12 }}"
    style="font-family: var(--font-mono); width: 96px;"
  />
  <p class="form-hint" style="color: var(--fg-muted); font-size: var(--text-xs);">
    ElevenLabs Business 套餐硬上限 15。改后需 systemctl restart autovideosrt 生效。
  </p>
</div>
```

- [ ] **Step 3: 在 admin.py 的 GET / POST settings 路由加处理**

参照现有 retention 字段的处理（`get_setting('retention_default_hours')` / `set_setting(...)`），加：

```python
# GET 路由（render settings.html）
context["tts_max_concurrency"] = get_setting("tts_max_concurrency") or "12"

# POST 路由（处理表单提交）
tts_concurrency_raw = request.form.get("tts_max_concurrency", "12").strip()
try:
    tts_concurrency_n = int(tts_concurrency_raw)
except ValueError:
    tts_concurrency_n = 12
tts_concurrency_n = max(1, min(tts_concurrency_n, 15))
set_setting("tts_max_concurrency", str(tts_concurrency_n))
```

- [ ] **Step 4: 启动 dev server 手动验证**

```bash
python main.py 2>&1 | tee /tmp/dev_server.log &
# 或者用项目的 dev 启动方式
```

打开浏览器（Ocean Blue 风格的 admin 后台），登录 admin/709709@（[testuser.md](../../../testuser.md)），访问 `/settings`，确认：
- 新表单字段出现
- 默认值 12
- 改成 8 + 提交 → 再访问 `/settings` 显示 8
- 改成 100 → 提交后被 clamp 到 15
- 改成 0 → 提交后被 clamp 到 1

- [ ] **Step 5: Commit**

```bash
git add web/routes/admin.py web/templates/settings.html
git -c gc.auto=0 commit -m "$(cat <<'EOF'
feat(admin): add TTS concurrency setting in /settings

新增 system setting tts_max_concurrency（默认 12，硬上限 15）。改后
restart autovideosrt 才生效（ThreadPoolExecutor 不支持运行时 resize）。

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 12: 端到端验证 + 部署准备

**Files:** 无代码改动，仅验证与 release。

- [ ] **Step 1: 跑全部相关测试**

```bash
cd .worktrees/tts-concurrent-generation
python -m pytest tests/test_tts_concurrent_generation.py tests/test_tts_progress_emitter.py tests/test_tts_pipeline.py tests/test_tts_duration_loop.py tests/test_pipeline_runner.py tests/test_appcore_runtime.py tests/test_runtime_ja_shared_shell.py tests/test_sentence_translate_runtime.py tests/test_runtime_multi_translate.py -v 2>&1 | tail -40
```

Expected: 全绿。如果有挂的，回到对应 Task 修复，**不要**用 `pytest.skip` 或 `xfail` 兜底。

- [ ] **Step 2: 跑架构边界检查**

```bash
python -m pytest tests/test_architecture_boundaries.py -v 2>&1 | tail -10
```

Expected: 全绿（确认没引入禁止的循环依赖）。

- [ ] **Step 3: 起 dev server 跑端到端验证**

启动 dev server 在空闲端口（5090）：

```bash
PORT=5090 python main.py 2>&1 | tee /tmp/dev_5090.log &
sleep 3
curl -s -o /dev/null -w "GET /: HTTP %{http_code}\n" http://127.0.0.1:5090/
```

Expected: HTTP 302（未登录跳 login）。

用 testuser admin/709709@ 登录后：
- 跑一个 70 段左右的多语言视频翻译任务
- 观察任务详情页 substep 显示"正在生成 XX 配音 · 第 1 轮 · X/70（活跃 12 路）"
- 同时启动第二个翻译任务，观察该任务初期是否显示"排队中等待 ElevenLabs 并发槽位（70 段待派发）"
- 观察 `journalctl -u autovideosrt` 或 dev_5090.log 中没有 429 错误

如果用户没空亲自跑，至少跑一个任务看 substep 文案是否切换成功。

- [ ] **Step 4: Rebase 到最新 master**

```bash
cd .worktrees/tts-concurrent-generation
git fetch origin master
# 如果有未提交改动先 stash
git stash push -u -m "pre-rebase" 2>/dev/null
git rebase origin/master
git stash pop 2>/dev/null || true
```

如果有冲突，停下来报告用户。

- [ ] **Step 5: 给用户一键发布 sudo 命令**

按 [CLAUDE.md "本机部署到线上的标准流程"](../../../CLAUDE.md) 模板生成。本机就是生产服务器，用 sudo 不走 publish.sh：

```bash
sudo bash -c '
set -e
WORKTREE=/g/Code/AutoVideoSrtLocal/.worktrees/tts-concurrent-generation
git config --global --add safe.directory /opt/autovideosrt
git config --global --add safe.directory $WORKTREE

cd /opt/autovideosrt
git fetch origin master
git reset --hard origin/master
BRANCH=$(git -C $WORKTREE rev-parse --abbrev-ref HEAD)
git fetch $WORKTREE $BRANCH:_deploy_incoming
git merge --ff-only _deploy_incoming
git branch -d _deploy_incoming
git push origin master

systemctl restart autovideosrt
sleep 3
systemctl is-active autovideosrt
curl -s -o /dev/null -w "/: HTTP %{http_code}\n" http://127.0.0.1/
'
```

**注意路径**：本机环境是 Windows，但 worktree 路径在生产服务器视角下是 Linux 路径——上面命令是给生产 Linux 用户跑的（本机 = 生产），如果实际机器是 Windows 开发机就调用 `bash deploy/publish.sh`，agent 自己判断。

⚠️ **不要主动跑这条命令**——CLAUDE.md 全局规则明确"未经许可禁止重启服务"。等用户说"发布"或类似明确字眼后再贴给用户执行。

---

## Self-Review 检查清单

实施完成后做最后一轮 self-review：

- [ ] **Spec 全覆盖**：spec §3.1～§3.7 每节都有对应 Task 实现？
- [ ] **5 个 TTS 调用方都改了**：multi-translate / omni / av-sync / ja / copywriting？
- [ ] **`on_progress` 接口与 `on_segment_done` 兼容**：没有改动 caller 都能继续工作？
- [ ] **排队中文案在 dev server 实测可见**？
- [ ] **没有 placeholder / TBD / 跳过的 step**？
- [ ] **新建测试文件**：`test_tts_concurrent_generation.py`、`test_tts_progress_emitter.py`？
- [ ] **commit 颗粒度合适**：每个 Task 一次 commit，不要把所有 runtime 改动塞一个 commit？
