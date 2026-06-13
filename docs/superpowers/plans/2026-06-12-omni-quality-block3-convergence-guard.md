# Block 3 — 收敛循环质量守门 + 压缩重译兜底 实施计划

> **For agentic workers:** 本块改动 multi/omni 共享的核心循环 `appcore/runtime/_pipeline_runner.py`，是全系列风险最高的一块。**先通读** Spec（[specs/2026-06-12-omni-quality-block3-convergence-guard-design.md](../specs/2026-06-12-omni-quality-block3-convergence-guard-design.md)）的「非目标 / 红线」与总览红线，再动手。每个 Task 内 TDD，小步提交。

**Goal:** 字数落窗候选过质量守门（忠实度+hook+ending）；5 轮未收敛时追加压缩重译终轮；物理截断升级为带句子预览的任务级告警。

**Architecture:** 新模块 `pipeline/rewrite_quality_guard.py`（独立、可单测）+ `_run_tts_duration_loop` 三个最小侵入插入点（守门调用、扩轮、target 旁路）+ 截断函数返回值扩展 + 详情页警示条。

**分支**: 从 `origin/audit/video-translate-quality` 切出 `fix/omni-quality-block3-guard`（若 Block 1 已合 master，则从 master 切并确认 base_rewrite 含 PROTECTION 段）。

---

### Task 1: 注册 use case + config 开关

**Files:**
- Modify: `appcore/llm_use_cases.py`、`config.py`
- Test: `tests/test_llm_use_cases_registry.py`（追加）

- [ ] **Step 1**: `tests/test_llm_use_cases_registry.py` 追加：

```python
def test_rewrite_guard_use_case_registered():
    from appcore.llm_use_cases import get_use_case
    uc = get_use_case("video_translate.rewrite_guard")
    assert uc["default_model"] == "gemini-3.1-flash-lite"
```

跑失败 → 在 `llm_use_cases.py` 的 video_translate 区块仿照 `video_translate.tts_language_check` 注册：

```python
    "video_translate.rewrite_guard": _uc(
        "video_translate.rewrite_guard",
        "video_translate",
        "字数收敛重写守门",
        "对落入字数窗口的 rewrite 候选做忠实度 + 首句钩子 + 尾句收尾三项快评",
        "gemini_vertex",
        "gemini-3.1-flash-lite",
        "gemini_vertex",
        "tokens",
    ),
```

- [ ] **Step 2**: `config.py` 追加（带注释）：

```python
# Block3: rewrite 质量守门 + 压缩重译兜底（docs/superpowers/specs/2026-06-12-omni-quality-block3-convergence-guard-design.md）
OMNI_REWRITE_GUARD_ENABLED = True
OMNI_REWRITE_GUARD_MIN_FIDELITY = 75
OMNI_REWRITE_GUARD_MAX_CALLS_PER_ROUND = 3
OMNI_COMPRESS_RETRANSLATE_ENABLED = True
```

- [ ] **Step 3**: 跑测试 PASS → `git commit -am "feat(block3): register rewrite_guard use case + config switches"`

### Task 2: 守门模块（独立、可单测）

**Files:**
- Create: `pipeline/rewrite_quality_guard.py`
- Create: `tests/test_rewrite_quality_guard.py`

- [ ] **Step 1: 写失败测试**（mock `appcore.llm_client.invoke_chat`）：

```python
import json
from unittest.mock import patch
from pipeline.rewrite_quality_guard import assess_rewrite_candidate

KW = dict(source_full_text="src", reference_translation_text="ref",
          candidate_text="cand", target_lang="en", task_id="t1", user_id=1)


def _resp(payload):
    return {"text": json.dumps(payload), "usage": {}}


def test_pass_when_all_good():
    with patch("pipeline.rewrite_quality_guard.llm_client") as m:
        m.invoke_chat.return_value = _resp(
            {"fidelity": 90, "hook_ok": True, "ending_ok": True, "issues": []})
        r = assess_rewrite_candidate(**KW)
    assert r["passed"] is True and r["guard_error"] is False


def test_fail_on_low_fidelity():
    with patch("pipeline.rewrite_quality_guard.llm_client") as m:
        m.invoke_chat.return_value = _resp(
            {"fidelity": 60, "hook_ok": True, "ending_ok": True, "issues": ["漏卖点"]})
        r = assess_rewrite_candidate(**KW)
    assert r["passed"] is False and r["issues"] == ["漏卖点"]


def test_fail_on_broken_ending():
    with patch("pipeline.rewrite_quality_guard.llm_client") as m:
        m.invoke_chat.return_value = _resp(
            {"fidelity": 95, "hook_ok": True, "ending_ok": False, "issues": ["结尾CTA丢失"]})
        r = assess_rewrite_candidate(**KW)
    assert r["passed"] is False


def test_fail_open_on_llm_error():
    with patch("pipeline.rewrite_quality_guard.llm_client") as m:
        m.invoke_chat.side_effect = RuntimeError("boom")
        r = assess_rewrite_candidate(**KW)
    assert r["passed"] is True and r["guard_error"] is True
```

- [ ] **Step 2: 实现模块**（参照 `pipeline/asr_clean.py` 的 `_call` 模式：llm_client.invoke_chat + json_schema strict + prompt_file_payload debug）：

```python
"""Rewrite 候选质量守门：忠实度 + 首句钩子 + 尾句收尾三项快评。
Spec: docs/superpowers/specs/2026-06-12-omni-quality-block3-convergence-guard-design.md
"""
from __future__ import annotations
import json
import logging

import config
from appcore import llm_client
from appcore.llm_debug_payloads import build_chat_request_payload, prompt_file_payload

log = logging.getLogger(__name__)
_USE_CASE = "video_translate.rewrite_guard"

_SYSTEM = """You are a translation quality gatekeeper for short-form commerce video scripts.
Compare CANDIDATE (a length-adjusted rewrite) against REFERENCE (the approved initial translation) and SOURCE (the original video transcript).
Return strict JSON only: {"fidelity": 0-100, "hook_ok": true/false, "ending_ok": true/false, "issues": ["..."]}
- fidelity: does CANDIDATE preserve the meaning of REFERENCE/SOURCE? No invented claims, no dropped key selling points. 100 = fully faithful.
- hook_ok: does CANDIDATE's FIRST sentence still work as a strong 3-second hook (clear outcome / benefit / curiosity / contrast)? It does not need to match REFERENCE word-for-word.
- ending_ok: does CANDIDATE's FINAL sentence preserve the closing / CTA intent of REFERENCE's ending? If REFERENCE ends with a wrap-up or CTA and CANDIDATE drops it, this is false.
- issues: up to 3 short Simplified-Chinese phrases describing concrete problems."""

_RESPONSE_FORMAT = {
    "type": "json_schema",
    "json_schema": {
        "name": "rewrite_guard",
        "strict": True,
        "schema": {
            "type": "object", "additionalProperties": False,
            "properties": {
                "fidelity": {"type": "integer", "minimum": 0, "maximum": 100},
                "hook_ok": {"type": "boolean"},
                "ending_ok": {"type": "boolean"},
                "issues": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["fidelity", "hook_ok", "ending_ok", "issues"],
        },
    },
}


def assess_rewrite_candidate(*, source_full_text: str, reference_translation_text: str,
                             candidate_text: str, target_lang: str,
                             task_id: str, user_id: int | None) -> dict:
    user_content = (
        f"TARGET LANGUAGE: {target_lang}\n\n"
        f"SOURCE (original transcript):\n{source_full_text}\n\n"
        f"REFERENCE (approved initial translation):\n{reference_translation_text}\n\n"
        f"CANDIDATE (length-adjusted rewrite to judge):\n{candidate_text}"
    )
    messages = [{"role": "system", "content": _SYSTEM},
                {"role": "user", "content": user_content}]
    debug_call = prompt_file_payload(
        phase="rewrite_guard", label="重写质量守门", use_case_code=_USE_CASE,
        provider=None, model=None, messages=messages,
        request_payload=build_chat_request_payload(
            use_case_code=_USE_CASE, provider=None, model=None,
            messages=messages, response_format=_RESPONSE_FORMAT,
            temperature=0.0, max_tokens=1000,
        ),
    )
    min_fidelity = int(getattr(config, "OMNI_REWRITE_GUARD_MIN_FIDELITY", 75))
    try:
        result = llm_client.invoke_chat(
            _USE_CASE, messages=messages, response_format=_RESPONSE_FORMAT,
            temperature=0.0, max_tokens=1000, user_id=user_id, project_id=task_id,
        )
        payload = result.get("json") or json.loads((result.get("text") or "").strip())
        fidelity = int(payload["fidelity"])
        hook_ok = bool(payload["hook_ok"])
        ending_ok = bool(payload["ending_ok"])
        issues = [str(x) for x in (payload.get("issues") or [])][:3]
    except Exception as exc:
        log.warning("[rewrite_guard] task=%s fail-open: %s", task_id, exc, exc_info=True)
        debug_call["error"] = str(exc)
        return {"fidelity": -1, "hook_ok": True, "ending_ok": True, "issues": [],
                "passed": True, "guard_error": True, "_llm_debug_call": debug_call}
    debug_call["response_preview"] = json.dumps(payload, ensure_ascii=False)[:2000]
    passed = fidelity >= min_fidelity and hook_ok and ending_ok
    return {"fidelity": fidelity, "hook_ok": hook_ok, "ending_ok": ending_ok,
            "issues": issues, "passed": passed, "guard_error": False,
            "_llm_debug_call": debug_call}
```

- [ ] **Step 3**: 跑测试 PASS → `git commit -am "feat(block3): rewrite_quality_guard module"`

### Task 3: 守门接入内循环

**Files:**
- Modify: `appcore/runtime/_pipeline_runner.py`（内循环 `if diff <= tolerance_abs:` 一带，约 1099 行）
- Create: `tests/test_duration_loop_guard_logic.py`

接入逻辑（伪代码级别，落地时贴合现场变量名）：

```python
# 循环前（每轮重置）：guard_calls_this_round = 0; in_window_rejected: list[tuple[int, dict, dict]] = []
guard_enabled = bool(getattr(config, "OMNI_REWRITE_GUARD_ENABLED", True))
guard_max_calls = int(getattr(config, "OMNI_REWRITE_GUARD_MAX_CALLS_PER_ROUND", 3))

if diff <= tolerance_abs:
    guard_result = None
    if guard_enabled and guard_calls_this_round < guard_max_calls:
        guard_calls_this_round += 1
        guard_result = assess_rewrite_candidate(
            source_full_text=source_full_text,
            reference_translation_text=(initial_localized_translation or {}).get("full_text", ""),
            candidate_text=candidate.get("full_text", ""),
            target_lang=str(target_language_label or ""),
            task_id=task_id, user_id=self.user_id,
        )
        attempts_list[-1]["guard"] = {k: guard_result[k] for k in
                                      ("fidelity", "hook_ok", "ending_ok", "issues", "passed", "guard_error")}
        # debug 落盘：_save_llm_prompt_debug 同 rewrite attempt 模式，filename rewrite_guard.round_N.attempt_M.json
    if guard_result is not None and not guard_result["passed"]:
        in_window_rejected.append((attempt, candidate, guard_result))
        prior_word_counts.append(cand_words)  # 既有行为保留
        # feedback 注入（下一 attempt 的 feedback_notes 拼接 QUALITY GATE FEEDBACK 段）
        guard_feedback = ("QUALITY GATE FEEDBACK: the previous in-window candidate was REJECTED: "
                          + "; ".join(guard_result["issues"] or ["quality below threshold"])
                          + ". Fix these while staying inside the word window. Keep sentence 1 as the hook "
                          + "and keep the final sentence's closing/CTA intent.")
        continue  # 不采纳，继续 attempt
    # guard 通过 / 关闭 / 超额（guard_skipped 标记）→ 现状采纳路径
    localized_translation = candidate
    ...
# attempt 耗尽后（localized_translation is None 分支之前）：
if localized_translation is None and in_window_rejected:
    best = max(in_window_rejected, key=lambda t: t[2]["fidelity"])
    localized_translation = best[1]
    chosen_attempt_idx = best[0] - 1
    round_record["guard_degraded"] = True
```

- [ ] **Step 1: 写测试**。直接对循环做单测太重——把上述决策提炼为纯函数 `appcore/runtime/_helpers.py::resolve_guarded_candidate(in_window: list[tuple[int, dict, dict|None]], ...) -> tuple[chosen_idx|None, degraded: bool]`（输入：每个落窗候选的 (attempt, candidate, guard_result)；输出：选用谁），循环里调用它。测试：

```python
from appcore.runtime._helpers import resolve_guarded_candidate


def test_first_passed_candidate_wins():
    rows = [(1, {"full_text": "a"}, {"passed": False, "fidelity": 60}),
            (2, {"full_text": "b"}, {"passed": True, "fidelity": 90})]
    idx, degraded = resolve_guarded_candidate(rows)
    assert idx == 2 and degraded is False


def test_all_rejected_falls_back_to_highest_fidelity():
    rows = [(1, {"full_text": "a"}, {"passed": False, "fidelity": 60}),
            (3, {"full_text": "c"}, {"passed": False, "fidelity": 72})]
    idx, degraded = resolve_guarded_candidate(rows)
    assert idx == 3 and degraded is True


def test_no_guard_means_first_in_window():
    rows = [(2, {"full_text": "b"}, None)]
    idx, degraded = resolve_guarded_candidate(rows)
    assert idx == 2 and degraded is False
```

- [ ] **Step 2**: 实现 helper + 循环接线（保持现有"guard 通过即 break"的流式行为：循环内 pass 直接 break，是 helper 的特例；helper 主要服务"耗尽降级"决策。两处语义必须一致）。
- [ ] **Step 3**: phase 事件：候选被守门拒绝时 `self._emit_duration_round(task_id, round_index, "quality_gate_rejected", round_record)`；前端 `web/static` 中 Duration 面板的 phase 文案表（grep `translate_rewrite` 在 JS 里的文案映射）追加 `quality_gate_rejected: "质量守门拒绝"`、`compress_round: "压缩重译终轮"` 两条中文文案；确认未知 phase 不致 JS 报错。
- [ ] **Step 4**: 跑相关测试 + `python3 scripts/pytest_related.py --base origin/master --run` → PASS → `git commit -am "feat(block3): wire quality gate into rewrite inner loop"`

### Task 4: 压缩重译终轮

**Files:**
- Modify: `appcore/runtime/_pipeline_runner.py`（while 循环尾部，仿 `EXTRA_STAGE1_SPEEDUP_FALLBACK_ROUNDS` 先例）
- Create: `tests/test_compress_round_target.py`

- [ ] **Step 1: 提炼 target 计算纯函数**（`appcore/runtime/_helpers.py`）：

```python
def compute_compress_round_target(last_audio_duration: float, wps: float,
                                  video_duration: float) -> tuple[float, int, str]:
    """压缩重译终轮：瞄准 final 窗中部 video−0.5s（窗为 [video−1, video]）。"""
    target_duration = max(0.5, video_duration - 0.5)
    target_words = max(3, round(target_duration * wps))
    direction = "shrink" if last_audio_duration > video_duration else "expand"
    return target_duration, target_words, direction
```

测试：

```python
from appcore.runtime._helpers import compute_compress_round_target


def test_compress_targets_half_second_under_video():
    d, w, direction = compute_compress_round_target(35.0, 2.5, 30.0)
    assert d == 29.5 and w == 74 and direction == "shrink"


def test_expand_when_audio_too_short():
    _, _, direction = compute_compress_round_target(25.0, 2.5, 30.0)
    assert direction == "expand"
```

- [ ] **Step 2: 扩轮接线**。在 while 循环体末尾（`round_index += 1` 前，最后一轮未收敛即将退出处）加：

```python
            if (
                round_index >= max_rounds_allowed
                and not compress_round_used
                and bool(getattr(config, "OMNI_COMPRESS_RETRANSLATE_ENABLED", True))
            ):
                compress_round_used = True
                max_rounds_allowed += 1
                compress_round_pending = True  # 下一轮按 compress 模式跑
```

循环顶部 Phase 1 的 target 计算处：`if compress_round_pending: target_duration, target_words, direction = compute_compress_round_target(...); round_record["compress_round"] = True; compress_round_pending = False`，并在该轮 feedback_notes 前置拼接 `FINAL LENGTH-CRITICAL REWRITE: this is the last chance before hard audio truncation. You MUST keep sentence 1 as the hook and keep the final sentence's closing/CTA intent; cut or expand only in the middle.`。变量初始化（`compress_round_used = False`、`compress_round_pending = False`）放在 `MAX_ROUNDS = 5` 一带。注意与 stage1 fallback 扩轮叠加：总上限自然为 `MAX_ROUNDS + EXTRA + 1`，无需额外处理（两个标志位各自只触发一次）。
- [ ] **Step 3**: `message` 文案：compress 轮的 `round_record["message"]` 写 `第 N 轮（压缩重译终轮）：…`。
- [ ] **Step 4**: 跑测试 + 相关 pytest → PASS → `git commit -am "feat(block3): compress-retranslate final round before bestpick"`

### Task 5: 截断告警升级

**Files:**
- Modify: `appcore/runtime/_pipeline_runner.py`（`_truncate_audio_to_duration` 及 `_run_default_tts_loop` 截断分支）
- Modify: 任务详情模板（grep `final_compose_summary` / 现有警示条所在模板，预期 `web/templates/_translate_detail_shell.html` 体系 + 对应 JS）
- Create: `tests/test_truncate_warning.py`

- [ ] **Step 1**: `_truncate_audio_to_duration` 返回 dict 增加 `removed_texts`（从被移除的 tts_segments 取 `tts_text` or `translated` or `text`）。
- [ ] **Step 2**: `_run_default_tts_loop` 截断采纳分支（`if not trim_result.get("skipped")`）追加：

```python
                    removed_texts = trim_result.get("removed_texts") or []
                    if trim_result.get("removed_count"):
                        warnings = list((task_state.get(task_id) or {}).get("quality_warnings") or [])
                        warnings.append({
                            "type": "tail_truncated",
                            "removed_count": trim_result["removed_count"],
                            "removed_texts": removed_texts,
                            "message": (
                                f"配音尾部被截断 {trim_result['removed_count']} 句，"
                                "可能丢失收尾/CTA 完整性"
                            ),
                        })
                        task_state.update(task_id, quality_warnings=warnings)
                        trimmed_record["removed_texts_preview"] = removed_texts[:3]
```

- [ ] **Step 3**: 详情页警示条：调研模板中任务状态卡渲染处（`grep -rn "quality_warnings\|compose_summary" web/templates web/static` 找现有提示条模式），新增：task JSON 带 `quality_warnings` 时渲染黄色警示条 `⚠️ {message}：{removed_texts 前 1-2 句}`。后端详情 API 确认透出该字段（task_state 全量字段一般自动透出，验证即可）。
- [ ] **Step 4**: 测试：mock `_truncate_audio_to_duration` 输入 segments 断言 `removed_texts`；task_state mock 断言 warnings append。跑 PASS → `git commit -am "feat(block3): tail-truncation quality warning with removed sentence preview"`

### Task 6: 收尾验证（红线自查必做）

- [ ] `python3 scripts/pytest_related.py --base origin/master --run` 全 PASS。
- [ ] 红线 diff 自查：`git diff origin/master -- appcore/runtime/_pipeline_runner.py | grep -E "^[-+].*(_tts_final_target_range|video_cap_|stage1_lo|stage1_hi|speedup_window)"` → 只允许出现新增读引用，不允许修改既有定义；`_compute_next_target` 函数体零改动。
- [ ] 测试环境人工冒烟（按 Spec 验收标准 3 跑三种任务），把 Duration 面板截图与 guard 调用次数写进汇报。
- [ ] push `fix/omni-quality-block3-guard`，停下等人工验收。
