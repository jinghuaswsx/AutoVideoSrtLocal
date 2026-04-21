# 音画同步(v2)流程调试可视化 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` 或 `superpowers:executing-plans` 按 Phase 顺序实施。Steps 用 `- [ ]` 追踪。
>
> **本 plan 与 spec 配套**:详细设计在 `docs/superpowers/specs/2026-04-21-av-sync-debug-visualization-design.md`。plan 只列操作步骤 + 代码骨架 + 验证命令,细节以 spec 为准。
>
> **由 Codex 在 worktree `.worktrees/av-sync-debug-view`(分支 `feature/av-sync-debug-view`)内执行**。每个 Phase 末尾 commit 并跑通验证才可进入下一 Phase。

## ⚠️ Codex 边界约束(硬性)

执行本 plan 期间**严禁**以下行为:

1. **严禁下载任何外部二进制**(MySQL / Redis / Node runtime / 任何 exe/zip 到本地),`.codex_tmp` 这种目录不准出现
2. **严禁启动任何后台服务进程**(mysqld / redis-server / nginx / docker / gunicorn 除了本地 `python main.py` 手测除外)
3. **严禁修改 `.env` 或往里写任何东西** — 即便为了跑测试
4. **严禁自行执行 git merge / push / 发布** — 即便 commit 作者身份是用户,也要等用户手动操作
5. **严禁动 master 分支** — 所有 commit 落在 `feature/av-sync-debug-view`
6. **严禁修改 v2 业务逻辑**(`pipeline/shot_notes.py` / `av_translate.py` / `duration_reconcile.py` 只允许在决策点加 `av_debug.log_decision(...)` 调用,不准改控制流、schema、返回值)

违反任何一条 → 立刻停下汇报,不要掩盖。

---

**Goal:** 在任务详情页加一个"流程调试"折叠区,通过 SSE 实时展示 v2 每一阶段的中间产物 + LLM prompt/response + 决策日志 + 重写历史。非侵入式挂在 `llm_client` 和阶段入口,业务零改动,可 env 开关全局关闭。

**Architecture:** `av_debug` 模块管 contextvar + 内存 queue + capture;`llm_client` 末尾加透明 hook 记录每次 LLM 调用;`runtime.run_av_localize` 每阶段前后 emit SSE event;前端 EventSource 订阅 + 阶段卡渲染。全量数据落 `state_json.av_debug`,零新 DB 表。

**Tech Stack:** Python / Flask SSE(`stream_with_context`) / pytest / contextvar / 内存 queue / vanilla JS + EventSource / Ocean Blue CSS token

---

## Phase 0: 准备(无 commit)

- [ ] **Step 0.1:进入 worktree,确认状态**
```bash
cd .worktrees/av-sync-debug-view
git status                                    # clean, on feature/av-sync-debug-view
git log --oneline -3                          # 最新提交是 a3d1b16 docs(spec)
```

- [ ] **Step 0.2:读 spec 全文**
```bash
cat docs/superpowers/specs/2026-04-21-av-sync-debug-visualization-design.md
```

- [ ] **Step 0.3:定位任务详情页模板**
```bash
grep -rln "project_detail\|任务详情" web/templates/ | head -5
grep -rln "projects\b" web/routes/ | head -5
```
记录找到的文件路径,Phase 6 会用到。

- [ ] **Step 0.4:确认 Flask 是单 worker(SSE 前提)**
```bash
grep -n "worker\|gunicorn" deploy/ README.md Procfile 2>/dev/null | head
```
应看到 `-w 1` 或等价。**若发现多 worker → 停下告警,spec 不成立**。

---

## Phase 1: av_debug 基础设施

**Files:**
- Create: `appcore/av_debug.py`
- Create: `tests/test_av_debug.py`
- Modify: `config.py`(加两个 env 开关)

- [ ] **Step 1.1:在 `config.py` 追加**
```python
AV_DEBUG_CAPTURE = _env("AV_DEBUG_CAPTURE", "1") == "1"
AV_DEBUG_UI = _env("AV_DEBUG_UI", "1") == "1"
```

- [ ] **Step 1.2:创建 `appcore/av_debug.py`**

核心 API(按 spec "SSE 推流" 和 "LLM 调用拦截" 节):
```python
import contextvars, queue, threading, time, logging
from typing import Any

logger = logging.getLogger(__name__)

_CURRENT_TASK_ID: contextvars.ContextVar[int | None] = contextvars.ContextVar("av_debug_task_id", default=None)
_CURRENT_STAGE: contextvars.ContextVar[str | None] = contextvars.ContextVar("av_debug_stage", default=None)

_task_queues: dict[int, queue.Queue] = {}
_queue_meta: dict[int, float] = {}      # task_id → last_activity_ts
_lock = threading.Lock()

MAX_PROMPT_SIZE = 100 * 1024


def set_task_context(task_id: int): _CURRENT_TASK_ID.set(task_id)
def get_task_context() -> int | None: return _CURRENT_TASK_ID.get()
def set_stage_context(stage: str | None): _CURRENT_STAGE.set(stage)
def get_stage_context() -> str | None: return _CURRENT_STAGE.get()


def _enabled() -> bool:
    from config import AV_DEBUG_CAPTURE
    return AV_DEBUG_CAPTURE


def get_or_create_queue(task_id: int) -> queue.Queue:
    with _lock:
        q = _task_queues.get(task_id)
        if q is None:
            q = queue.Queue()
            _task_queues[task_id] = q
        _queue_meta[task_id] = time.time()
        return q


def emit(event_type: str, data: dict, task_id: int | None = None) -> None:
    if not _enabled(): return
    tid = task_id if task_id is not None else get_task_context()
    if tid is None: return
    try:
        get_or_create_queue(tid).put_nowait({"event": event_type, "data": data, "ts": time.time()})
    except Exception as e:
        logger.warning("av_debug emit failed: %s", e)


def emit_stage_start(stage: str): set_stage_context(stage); emit("stage_start", {"stage": stage})
def emit_stage_progress(stage: str, note: str): emit("stage_progress", {"stage": stage, "note": note})
def emit_stage_done(stage: str, elapsed_ms: int, output_ref: str | None = None):
    emit("stage_done", {"stage": stage, "elapsed_ms": elapsed_ms, "output_ref": output_ref})
    set_stage_context(None)
def emit_stage_error(stage: str, error_msg: str, stack_tail: str = ""):
    emit("stage_error", {"stage": stage, "error_msg": error_msg, "stack_tail": stack_tail[-2000:]})
    set_stage_context(None)
def emit_task_done(task_id: int | None = None): emit("task_done", {}, task_id=task_id)


def log_decision(stage: str | None, message: str) -> None:
    if not _enabled(): return
    tid = get_task_context()
    if tid is None: return
    st = stage or get_stage_context() or "unknown"
    try:
        from appcore import task_state
        task_state.append_av_debug_decision(tid, st, message)   # Phase 3 会实现
        emit("decision", {"stage": st, "message": message})
    except Exception as e:
        logger.warning("log_decision failed: %s", e)


def capture_llm_call(use_case: str, *, messages: list | None, prompt: str | None,
                     system: str | None, media_refs: list[str] | None,
                     response_raw: Any, tokens_in: int | None, tokens_out: int | None,
                     elapsed_ms: int, status: str, error: str | None = None) -> None:
    if not _enabled(): return
    tid = get_task_context()
    if tid is None: return
    stage = get_stage_context() or "unknown"
    try:
        def _trim(s):
            if isinstance(s, str) and len(s) > MAX_PROMPT_SIZE:
                return f"<truncated; full_len={len(s)}>{s[:MAX_PROMPT_SIZE]}"
            return s
        record = {
            "attempt": 1, "use_case": use_case,
            "messages": _trim(str(messages)) if messages else None,
            "prompt": _trim(prompt), "system": _trim(system),
            "media_refs": media_refs,
            "response_raw": _trim(str(response_raw)) if not isinstance(response_raw, (dict, list)) else response_raw,
            "tokens_in": tokens_in, "tokens_out": tokens_out,
            "elapsed_ms": elapsed_ms, "status": status, "error": error,
        }
        from appcore import task_state
        task_state.append_av_debug_llm_call(tid, stage, record)   # Phase 3 实现
        emit("llm_call", {
            "stage": stage, "use_case": use_case, "tokens_in": tokens_in,
            "tokens_out": tokens_out, "elapsed_ms": elapsed_ms, "status": status,
        })
    except Exception as e:
        logger.warning("capture_llm_call failed: %s", e)


def sweep_stale_queues(max_age_sec: int = 3600) -> int:
    """删除 max_age_sec 内无活动的 queue。返回清理数。"""
    now = time.time()
    removed = 0
    with _lock:
        for tid, last in list(_queue_meta.items()):
            if now - last > max_age_sec:
                _task_queues.pop(tid, None)
                _queue_meta.pop(tid, None)
                removed += 1
    return removed
```

- [ ] **Step 1.3:写 `tests/test_av_debug.py`**

```python
def test_set_and_get_task_context():
    from appcore import av_debug
    av_debug.set_task_context(42)
    assert av_debug.get_task_context() == 42

def test_emit_skipped_when_disabled(monkeypatch):
    monkeypatch.setattr("config.AV_DEBUG_CAPTURE", False)
    from appcore import av_debug
    av_debug.set_task_context(1)
    av_debug.emit("stage_start", {"stage": "s"})
    # queue 不应创建
    assert 1 not in av_debug._task_queues

def test_emit_without_task_context_is_noop(monkeypatch):
    monkeypatch.setattr("config.AV_DEBUG_CAPTURE", True)
    from appcore import av_debug
    av_debug._CURRENT_TASK_ID.set(None)
    av_debug.emit("x", {})

def test_emit_creates_queue_and_puts_event(monkeypatch):
    monkeypatch.setattr("config.AV_DEBUG_CAPTURE", True)
    from appcore import av_debug
    av_debug.set_task_context(99)
    av_debug.emit("stage_start", {"stage": "shot_notes"})
    q = av_debug._task_queues[99]
    item = q.get(timeout=1)
    assert item["event"] == "stage_start"

def test_capture_llm_call_truncates_oversized_prompt(monkeypatch):
    monkeypatch.setattr("config.AV_DEBUG_CAPTURE", True)
    from appcore import av_debug
    av_debug.set_task_context(7)
    av_debug.set_stage_context("shot_notes")
    big = "x" * (200 * 1024)
    # 需要 mock task_state.append_av_debug_llm_call 避免 DB 调用
    calls = []
    import appcore.task_state as ts
    monkeypatch.setattr(ts, "append_av_debug_llm_call", lambda *a, **kw: calls.append((a, kw)))
    av_debug.capture_llm_call("x.y", messages=None, prompt=big, system=None,
                               media_refs=None, response_raw={"r": 1},
                               tokens_in=1, tokens_out=1, elapsed_ms=100, status="success")
    assert calls, "should have called append"
    stored = calls[0][0][2]
    assert stored["prompt"].startswith("<truncated")

def test_capture_swallows_exception(monkeypatch):
    monkeypatch.setattr("config.AV_DEBUG_CAPTURE", True)
    from appcore import av_debug
    av_debug.set_task_context(8)
    av_debug.set_stage_context("x")
    import appcore.task_state as ts
    monkeypatch.setattr(ts, "append_av_debug_llm_call", lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("db dead")))
    # 不应抛
    av_debug.capture_llm_call("x.y", messages=None, prompt="p", system=None,
                               media_refs=None, response_raw={}, tokens_in=0, tokens_out=0,
                               elapsed_ms=1, status="success")

def test_sweep_stale_queues(monkeypatch):
    from appcore import av_debug
    av_debug._task_queues[100] = __import__("queue").Queue()
    av_debug._queue_meta[100] = 0   # 远古时间
    removed = av_debug.sweep_stale_queues(max_age_sec=1)
    assert removed == 1
    assert 100 not in av_debug._task_queues
```

- [ ] **Step 1.4:跑测试**
```bash
pytest tests/test_av_debug.py -q -v
```
Expected: 7 PASS(`append_av_debug_llm_call` 只 mock,真函数 Phase 3 实现)。

- [ ] **Step 1.5:Commit**
```bash
git add appcore/av_debug.py tests/test_av_debug.py config.py
git commit -m "feat(av-debug): contextvar + 内存 queue + emit + capture 骨架"
```

---

## Phase 2: llm_client 拦截层

**Files:**
- Modify: `appcore/llm_client.py`
- Modify: `tests/` 新增 `test_llm_client_av_debug.py`

- [ ] **Step 2.1:在 `appcore/llm_client.py` 的 `invoke_chat` 和 `invoke_generate` 末尾加 hook**

在每个函数的 try/except 里,成功路径末尾调 `av_debug.capture_llm_call(...)`,失败路径也调(status="error")。用 `time.perf_counter` 计 elapsed_ms。参考:

```python
def invoke_chat(use_case_code, *, messages, ...):
    ...
    binding = llm_bindings.resolve(use_case_code)
    provider = provider_override or binding["provider"]
    model = model_override or binding["model"]
    adapter = get_adapter(provider)
    t0 = time.perf_counter()
    try:
        result = adapter.chat(...)
    except Exception as e:
        elapsed = int((time.perf_counter() - t0) * 1000)
        _log_usage(..., success=False, ..., error=e)
        try:
            from appcore import av_debug
            av_debug.capture_llm_call(
                use_case_code, messages=messages, prompt=None, system=None,
                media_refs=None, response_raw=None, tokens_in=None, tokens_out=None,
                elapsed_ms=elapsed, status="error", error=str(e)[:500],
            )
        except Exception:
            pass
        raise
    elapsed = int((time.perf_counter() - t0) * 1000)
    _log_usage(..., success=True, ..., usage=result.get("usage"))
    try:
        from appcore import av_debug
        usage = result.get("usage") or {}
        av_debug.capture_llm_call(
            use_case_code, messages=messages, prompt=None, system=None,
            media_refs=None, response_raw=result,
            tokens_in=usage.get("input_tokens"), tokens_out=usage.get("output_tokens"),
            elapsed_ms=elapsed, status="success",
        )
    except Exception:
        pass
    return result
```

`invoke_generate` 同理,`messages=None`,`prompt`、`system`、`media_refs` 从入参填。

- [ ] **Step 2.2:写 `tests/test_llm_client_av_debug.py`**

```python
def test_invoke_chat_captures_llm_call(monkeypatch):
    from appcore import llm_client, av_debug, task_state
    av_debug.set_task_context(1)
    av_debug.set_stage_context("shot_notes")
    captured = []
    monkeypatch.setattr(task_state, "append_av_debug_llm_call",
                         lambda tid, st, rec: captured.append((tid, st, rec)))
    monkeypatch.setattr("config.AV_DEBUG_CAPTURE", True)

    class FakeAdapter:
        def chat(self, **kw): return {"text": "ok", "usage": {"input_tokens": 10, "output_tokens": 5}}
    monkeypatch.setattr("appcore.llm_providers.get_adapter", lambda p: FakeAdapter())
    monkeypatch.setattr("appcore.llm_bindings.resolve",
                         lambda code: {"provider": "openrouter", "model": "x"})

    llm_client.invoke_chat("test.x", messages=[{"role": "user", "content": "hi"}])
    assert captured and captured[0][1] == "shot_notes"
    assert captured[0][2]["status"] == "success"

def test_invoke_chat_captures_error(monkeypatch):
    # 同上,但 FakeAdapter.chat 抛 RuntimeError
    # 验 captured[0][2]["status"] == "error"
    ...

def test_capture_never_breaks_invoke(monkeypatch):
    # av_debug.capture_llm_call 抛异常,invoke_chat 仍返回正常
    ...
```

- [ ] **Step 2.3:跑测试**
```bash
pytest tests/test_llm_client_av_debug.py -q -v
pytest tests/ -k "llm_client" -q   # 老 llm_client 测试不退化
```
Expected: 全 PASS。

- [ ] **Step 2.4:Commit**
```bash
git add appcore/llm_client.py tests/test_llm_client_av_debug.py
git commit -m "feat(av-debug): llm_client 透明 capture hook"
```

---

## Phase 3: task_state 扩展 + runtime 集成

**Files:**
- Modify: `appcore/task_state.py`(新增 3 个 helper)
- Modify: `appcore/runtime.py`(`run_av_localize` 加 emit)
- Test: `tests/test_appcore_task_state.py`, `tests/test_appcore_runtime.py`

- [ ] **Step 3.1:`appcore/task_state.py` 追加 3 个 helper**

```python
def append_av_debug_decision(task_id: int, stage: str, message: str) -> None:
    """追加一条决策日志到 state_json.av_debug.stages[stage].decisions[]。"""
    state = load_task_state(task_id)
    state.setdefault("av_debug", {"enabled": True, "stages": {}})
    st = state["av_debug"]["stages"].setdefault(stage, {"status": "running", "decisions": [], "llm_calls": []})
    st["decisions"].append(message)
    save_task_state(task_id, state)

def append_av_debug_llm_call(task_id: int, stage: str, record: dict) -> None:
    state = load_task_state(task_id)
    state.setdefault("av_debug", {"enabled": True, "stages": {}})
    st = state["av_debug"]["stages"].setdefault(stage, {"status": "running", "decisions": [], "llm_calls": []})
    st["llm_calls"].append(record)
    save_task_state(task_id, state)

def mark_av_debug_stage(task_id: int, stage: str, *,
                        status: str, started_at: float | None = None,
                        ended_at: float | None = None, elapsed_ms: int | None = None,
                        output_ref: str | None = None, error: str | None = None) -> None:
    state = load_task_state(task_id)
    state.setdefault("av_debug", {"enabled": True, "stages": {}})
    st = state["av_debug"]["stages"].setdefault(stage, {"decisions": [], "llm_calls": []})
    st["status"] = status
    if started_at: st["started_at"] = started_at
    if ended_at: st["ended_at"] = ended_at
    if elapsed_ms is not None: st["elapsed_ms"] = elapsed_ms
    if output_ref: st["output_ref"] = output_ref
    if error: st["error"] = error
    save_task_state(task_id, state)
```

(如果 `load_task_state`/`save_task_state` 命名不同,Codex 用实际命名。)

- [ ] **Step 3.2:修改 `appcore/runtime.py` 的 `run_av_localize`**

在函数最开始 set contextvar,每个阶段(shot_notes / av_translate / tts / duration_reconcile / subtitle)前后包 `av_debug.emit_stage_start` + `mark_av_debug_stage(running/done/error)`。示意:

```python
def run_av_localize(task_id, ...):
    from appcore import av_debug
    from appcore.task_state import mark_av_debug_stage
    import time, traceback

    av_debug.set_task_context(task_id)

    # script_segments(从 ASR / alignment 结果读)
    mark_av_debug_stage(task_id, "script_segments", status="done",
                         output_ref="script_segments")
    av_debug.log_decision("script_segments",
                           f"ASR 识别 {len(script_segments)} 句,最短 {min_dur:.1f}s / 最长 {max_dur:.1f}s")

    # shot_notes
    t0 = time.perf_counter()
    av_debug.emit_stage_start("shot_notes")
    mark_av_debug_stage(task_id, "shot_notes", status="running", started_at=time.time())
    try:
        shot_notes = generate_shot_notes(...)
    except Exception as e:
        elapsed = int((time.perf_counter() - t0) * 1000)
        av_debug.emit_stage_error("shot_notes", str(e), traceback.format_exc())
        mark_av_debug_stage(task_id, "shot_notes", status="error",
                             elapsed_ms=elapsed, error=str(e)[:500])
        raise
    elapsed = int((time.perf_counter() - t0) * 1000)
    mark_av_debug_stage(task_id, "shot_notes", status="done",
                         ended_at=time.time(), elapsed_ms=elapsed,
                         output_ref="shot_notes")
    av_debug.emit_stage_done("shot_notes", elapsed, output_ref="shot_notes")

    # ... av_translate / tts / duration_reconcile / subtitle 同理
    av_debug.emit_task_done(task_id)
```

- [ ] **Step 3.3:在 `pipeline/shot_notes.py` / `av_translate.py` / `duration_reconcile.py` 关键决策点加 `av_debug.log_decision(...)`**

仅在**决策点**插入(如:sentences 数校验后、target_chars 计算后、每个句子分类后),**不改控制流 / 返回值 / schema**。示例:

```python
# pipeline/duration_reconcile.py 的分类分支里
from appcore import av_debug
status, speed = classify_overshoot(td, tts_dur)
av_debug.log_decision("duration_reconcile",
    f"#{idx} target={td:.1f}s tts={tts_dur:.1f}s overshoot={ratio*100:+.0f}% → status={status}")
```

- [ ] **Step 3.4:`rewrite_history` 捕获**

在 `duration_reconcile.py` 的 rewrite 循环里,每轮 append 一条到 `sentences[i].rewrite_history`(直接改 av_output 的 dict,下游 save_task_state 带走即可):

```python
sentence.setdefault("rewrite_history", []).append({
    "round": round_num,
    "prev_text": prev_text,
    "prev_tts_duration": prev_tts_dur,
    "overshoot_sec": prev_tts_dur - target_duration,
    "new_target_chars": list(new_range),
    "rewrite_prompt": rewrite_prompt,     # 从 av_translate.rewrite_one 返回时暴露
    "rewrite_response": rewrite_response,
    "new_text": new_text,
    "new_tts_duration": new_tts_dur,
    "result_status": new_status,
})
av_debug.emit("rewrite_round", {"asr_index": idx, "round": round_num, "status": new_status})
```

`av_translate.rewrite_one` 需要改签名返回 `(new_text, prompt_used, response_raw)` 三元组,或者把这些存到一个传入的 dict。Codex 选清爽的方式,但**不破坏已有调用点**。

- [ ] **Step 3.5:写集成测试 `tests/test_appcore_runtime.py` 追加**

```python
def test_run_av_localize_emits_all_stage_events(monkeypatch):
    # mock shot_notes/av_translate/tts/reconcile 全部返回合法值
    # 断言 av_debug.emit 被调用序列: stage_start/done 各 5-6 次
    ...

def test_stage_error_emits_error_event(monkeypatch):
    # mock shot_notes 抛异常
    # 断言 emit_stage_error 被调用,task.status 更新为 failed
    ...
```

- [ ] **Step 3.6:跑测试**
```bash
pytest tests/test_appcore_task_state.py tests/test_appcore_runtime.py tests/test_av_debug.py -q -v
```

- [ ] **Step 3.7:Commit**
```bash
git add appcore/task_state.py appcore/runtime.py pipeline/shot_notes.py pipeline/av_translate.py pipeline/duration_reconcile.py tests/test_appcore_task_state.py tests/test_appcore_runtime.py
git commit -m "feat(av-debug): runtime emit + task_state helpers + pipeline decision log"
```

---

## Phase 4: SSE 路由

**Files:**
- Create: `web/routes/av_debug.py`
- Modify: `web/__init__.py`(或实际注册蓝图的地方)
- Test: `tests/test_routes_av_debug_sse.py`

- [ ] **Step 4.1:创建 `web/routes/av_debug.py`**

```python
from flask import Blueprint, Response, stream_with_context, jsonify, request, abort
from flask import session   # 或实际鉴权方式
from appcore import av_debug
import json, time

bp = Blueprint("av_debug", __name__, url_prefix="/api/tasks")


def _assert_can_read_task(task_id: int):
    # TODO: 参考任务详情页鉴权,不通过则 abort(403)
    pass


@bp.get("/<int:task_id>/av_debug/stream")
def stream(task_id: int):
    _assert_can_read_task(task_id)

    def gen():
        yield f'event: connected\ndata: {{"ts": {time.time()}}}\n\n'
        q = av_debug.get_or_create_queue(task_id)
        # 客户端重连时通过 Last-Event-ID 不在 MVP 范围
        while True:
            try:
                item = q.get(timeout=25)    # 25s 超时发 keepalive
            except Exception:
                yield ": keepalive\n\n"
                continue
            payload = json.dumps(item["data"])
            yield f'event: {item["event"]}\ndata: {payload}\n\n'
            if item["event"] == "task_done":
                break

    return Response(stream_with_context(gen()),
                     mimetype="text/event-stream",
                     headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no",
                              "Connection": "keep-alive"})


@bp.get("/<int:task_id>/av_debug/snapshot")
def snapshot(task_id: int):
    """返回 task.state_json.av_debug 当前快照,给前端刷新用。"""
    _assert_can_read_task(task_id)
    from appcore import task_state
    state = task_state.load_task_state(task_id)
    return jsonify(state.get("av_debug") or {})
```

- [ ] **Step 4.2:注册蓝图**

找到现有蓝图注册位置(`web/__init__.py` 或 `main.py`),追加:
```python
from web.routes.av_debug import bp as av_debug_bp
app.register_blueprint(av_debug_bp)
```

- [ ] **Step 4.3:写 `tests/test_routes_av_debug_sse.py`**

```python
def test_stream_endpoint_rejects_unauthed(client): ...
def test_snapshot_returns_state_json_slice(client, monkeypatch):
    # mock task_state.load_task_state 返回带 av_debug 的 state
    resp = client.get("/api/tasks/1/av_debug/snapshot")
    assert resp.json == {...}

def test_stream_emits_connected_event(client, monkeypatch):
    # 订阅 stream,断言第一行是 "event: connected"
    ...
```

- [ ] **Step 4.4:跑测试**
```bash
pytest tests/test_routes_av_debug_sse.py -q -v
```

- [ ] **Step 4.5:Commit**
```bash
git add web/routes/av_debug.py web/__init__.py tests/test_routes_av_debug_sse.py
git commit -m "feat(av-debug): SSE 路由 stream + snapshot"
```

---

## Phase 5: 前端折叠区

**Files:**
- Create: `web/templates/av_debug_panel.html`
- Create: `web/static/av_debug.js`
- Create: `web/static/av_debug.css`
- Modify: 任务详情页模板(Phase 0.3 已定位)include 折叠区

- [ ] **Step 5.1:模板 `web/templates/av_debug_panel.html`**

条件渲染:`{% if av_debug_ui_enabled %}`(从后端 g 传入,来自 `config.AV_DEBUG_UI`)。骨架:

```html
<section class="av-debug-panel" data-task-id="{{ task_id }}" id="avDebugPanel" aria-live="polite" hidden-empty>
  <header class="av-debug-head">
    <button class="av-debug-toggle" type="button" aria-expanded="false">
      <svg class="icon-chevron">...</svg>
      <span>流程调试</span>
      <span class="av-debug-version">v2 音画同步</span>
    </button>
    <span class="av-debug-global-status" data-status="idle">未开始</span>
  </header>
  <div class="av-debug-body" hidden>
    <div class="av-debug-grid">
      <!-- 6 个卡片 -->
      <article class="av-debug-card" data-stage="script_segments">
        <div class="av-debug-card-head">
          <span class="status-dot" data-status="idle"></span>
          <h3>ASR 对齐</h3>
          <span class="elapsed"></span>
        </div>
        <div class="av-debug-card-body"></div>
      </article>
      <!-- shot_notes / av_translate / tts / duration_reconcile / subtitle -->
    </div>
  </div>
</section>
```

- [ ] **Step 5.2:CSS `web/static/av_debug.css`**

遵循 Ocean Blue token:
```css
.av-debug-panel { margin-top: var(--space-6); border: 1px solid var(--border); border-radius: var(--radius-lg); background: white; }
.av-debug-head { display: flex; align-items: center; justify-content: space-between; padding: var(--space-4) var(--space-6); cursor: pointer; }
.av-debug-body { padding: var(--space-6); border-top: 1px solid var(--border); background: var(--bg-subtle); }
.av-debug-grid { display: grid; grid-template-columns: repeat(2, 1fr); gap: var(--space-4); }
@media (max-width: 768px) { .av-debug-grid { grid-template-columns: 1fr; } }
.av-debug-card { border: 1px solid var(--border); border-radius: var(--radius-md); background: white; padding: var(--space-4); }
.status-dot { width: 10px; height: 10px; border-radius: var(--radius-full); display: inline-block; }
.status-dot[data-status="idle"] { background: var(--fg-subtle); }
.status-dot[data-status="running"] { background: var(--cyan); animation: pulse 1.2s ease-in-out infinite; }
.status-dot[data-status="done"] { background: var(--success); }
.status-dot[data-status="error"] { background: var(--danger); }
@keyframes pulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.3; } }
pre.av-debug-raw { max-height: 360px; overflow: auto; font-family: var(--font-mono); font-size: var(--text-xs); background: var(--bg-muted); padding: var(--space-3); border-radius: var(--radius); }
/* 禁紫色 / 禁 emoji - 见项目 CLAUDE.md */
```

- [ ] **Step 5.3:JS `web/static/av_debug.js`**

```javascript
(function() {
  const panel = document.getElementById("avDebugPanel");
  if (!panel) return;
  const taskId = panel.dataset.taskId;
  const toggle = panel.querySelector(".av-debug-toggle");
  const body = panel.querySelector(".av-debug-body");
  let expanded = false;
  toggle.addEventListener("click", () => {
    expanded = !expanded;
    body.hidden = !expanded;
    toggle.setAttribute("aria-expanded", expanded);
    if (expanded) refreshSnapshot();
  });

  async function refreshSnapshot() {
    const resp = await fetch(`/api/tasks/${taskId}/av_debug/snapshot`);
    if (!resp.ok) return;
    const data = await resp.json();
    render(data);
  }

  function render(avDebug) {
    const stages = (avDebug.stages || {});
    Object.entries(stages).forEach(([stageName, stage]) => {
      const card = panel.querySelector(`.av-debug-card[data-stage="${stageName}"]`);
      if (!card) return;
      card.querySelector(".status-dot").dataset.status = stage.status || "idle";
      card.querySelector(".elapsed").textContent = stage.elapsed_ms ? `${(stage.elapsed_ms/1000).toFixed(1)}s` : "";
      card.querySelector(".av-debug-card-body").innerHTML = renderStageBody(stageName, stage);
    });
  }

  function renderStageBody(name, stage) {
    // 不同阶段不同视图,详见 spec "单卡结构" 节
    // 决策日志 + LLM 调用折叠 + 数据预览
    // 各阶段 handler 单独实现:
    if (name === "shot_notes") return renderShotNotes(stage);
    if (name === "av_translate") return renderAvTranslate(stage);
    if (name === "tts") return renderTts(stage);
    if (name === "duration_reconcile") return renderReconcile(stage);
    return renderGeneric(stage);
  }
  // 4-6 个具体渲染函数实现 spec 中描述的视图

  // SSE
  const es = new EventSource(`/api/tasks/${taskId}/av_debug/stream`);
  ["stage_start", "stage_progress", "stage_done", "stage_error",
   "llm_call", "decision", "rewrite_round", "task_done"].forEach(evt => {
    es.addEventListener(evt, (e) => {
      if (expanded) refreshSnapshot();   // 面板打开时才去拿
      updateGlobalStatus(evt, JSON.parse(e.data));
      if (evt === "task_done") es.close();
    });
  });

  function updateGlobalStatus(evt, data) { /* 顶部全局 status 灯 */ }
})();
```

- [ ] **Step 5.4:任务详情页 include**

在 Phase 0.3 定位的模板(比如 `web/templates/project_detail.html`)合适位置:
```html
{% include "av_debug_panel.html" with context %}
<link rel="stylesheet" href="{{ url_for('static', filename='av_debug.css') }}">
<script src="{{ url_for('static', filename='av_debug.js') }}" defer></script>
```

Flask 视图函数把 `av_debug_ui_enabled=AV_DEBUG_UI`、`task_id=<id>` 塞进 template context。

- [ ] **Step 5.5:手工冒烟**

```bash
# 启动服务(不走 gunicorn,直接 python main.py)
python main.py
```

浏览器打开已有 v2 任务详情页:
- 折叠区默认收起,点击展开后看到 6 个卡片
- 已完成任务:snapshot 数据正确,决策日志、LLM 调用能展开
- 新跑任务:status dot 实时切换(灰 → 蓝脉冲 → 绿)

- [ ] **Step 5.6:Commit**
```bash
git add web/templates/av_debug_panel.html web/static/av_debug.js web/static/av_debug.css web/templates/<project_detail>.html
git commit -m "feat(av-debug): 任务详情页流程调试折叠区 + SSE 前端"
```

---

## Phase 6: 冒烟 + 收尾

- [ ] **Step 6.1:跑 v2 触及测试回归**
```bash
pytest tests/test_av_debug.py tests/test_llm_client_av_debug.py tests/test_appcore_task_state.py tests/test_appcore_runtime.py tests/test_routes_av_debug_sse.py tests/test_shot_notes.py tests/test_av_translate.py tests/test_duration_reconcile.py -q
```
Expected: 全绿。

- [ ] **Step 6.2:开关降级验证**
```bash
# 关采集
AV_DEBUG_CAPTURE=0 python main.py
# 验证:v2 任务仍正常跑,snapshot 返回 {},折叠区显示"采集已关闭"
# 关 UI
AV_DEBUG_UI=0 python main.py
# 验证:任务页无折叠区;state_json 仍在采集
```

- [ ] **Step 6.3:最终汇报发用户**

产出:
- 全部 commit hash 列表
- Phase 6 冒烟结果简报(能跑,断言 SSE 事件序列到前端)
- 已知限制(单 worker 前提 / prompt 100KB 截断)
- 一句话:"调试可视化第一版就绪"

完成后停下,**不发 PR / 不 rebase master / 不 merge**,等用户指令。
