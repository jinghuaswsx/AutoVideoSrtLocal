# AV Sync V2 Sentence Convergence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rebuild the video-translate AV sync path around sentence-level localization, duration convergence, GPT-5.5 via OpenRouter, and visible per-step diagnostics.

**Architecture:** Keep the existing AV sync route, task state, TTS, subtitle, and artifact surfaces, but replace the AV sync core with a sentence-level loop. Persist per-sentence attempts and debug summaries under `variants.av`, then render those details in the existing task workbench.

**Tech Stack:** Flask, Jinja templates, vanilla JavaScript, pytest, OpenRouter through `appcore.llm_client`, ElevenLabs through `pipeline.tts`, ffmpeg/ffprobe.

---

## File Map

- `web/templates/_task_workbench.html`: fix AV sync entry page 500 and add the sentence convergence panel shell.
- `web/templates/_task_workbench_scripts.html`: render AV sync convergence rows, debug details, and manual rewrite refresh state.
- `web/templates/_task_workbench_styles.html`: add Ocean Blue styles for the sentence convergence panel.
- `appcore/llm_use_cases.py`: change AV sync localization and rewrite defaults to OpenRouter `openai/gpt-5.5`.
- `pipeline/av_translate.py`: replace AV sync prompt/schema with sentence-level localization and rewrite contracts.
- `pipeline/duration_reconcile.py`: enforce 95%-105% convergence and 0.95-1.05 speed bounds, record attempts.
- `appcore/runtime.py`: persist AV debug data and rebuild full audio from final sentence audio after convergence.
- `web/routes/task.py`: keep manual rewrite consistent with the same 95%-105% rules and full rebuild behavior.
- `tests/test_av_sync_menu_routes.py`: cover entry page 200.
- `tests/test_llm_use_cases_registry.py`: cover GPT-5.5 defaults.
- `tests/test_av_translate.py`: cover prompt/schema requirements.
- `tests/test_duration_reconcile.py`: cover convergence thresholds, speed bounds, and attempts.
- `tests/test_appcore_runtime.py`: cover AV runtime persistence.
- `tests/test_web_routes.py`: cover manual rewrite state and rebuild behavior.

## Task 1: Fix AV Sync Entry Page 500

**Files:**
- Modify: `web/templates/_task_workbench.html`
- Test: `tests/test_av_sync_menu_routes.py`

- [ ] **Step 1: Run the existing failing test**

Run:

```bash
pytest tests/test_av_sync_menu_routes.py::test_av_sync_menu_page_renders_shared_workbench -q
```

Expected: FAIL with `jinja2.exceptions.UndefinedError: 'project' is undefined`.

- [ ] **Step 2: Patch the template guard**

In `web/templates/_task_workbench.html`, replace the unsafe project access around the force-restart block with a project-defined guard:

```jinja
{% set _has_project_context = project is defined and project %}
{% if _has_project_context and not project.deleted_at and project.id and (request.path.startswith('/omni-translate') or request.path.startswith('/multi-translate')) %}
```

Keep the body of the block unchanged.

- [ ] **Step 3: Verify the entry route**

Run:

```bash
pytest tests/test_av_sync_menu_routes.py -q
```

Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add web/templates/_task_workbench.html tests/test_av_sync_menu_routes.py
git commit -m "fix(av-sync): guard workbench entry project context"
```

## Task 2: Switch AV Sync LLM Defaults to GPT-5.5

**Files:**
- Modify: `appcore/llm_use_cases.py`
- Test: `tests/test_llm_use_cases_registry.py`

- [ ] **Step 1: Add failing registry assertions**

Add this test near `test_video_translate_av_sync_defaults`:

```python
def test_video_translate_av_sync_uses_gpt55_openrouter():
    localize = USE_CASES["video_translate.av_localize"]
    rewrite = USE_CASES["video_translate.av_rewrite"]

    assert localize.default_provider == "openrouter"
    assert localize.default_model == "openai/gpt-5.5"
    assert rewrite.default_provider == "openrouter"
    assert rewrite.default_model == "openai/gpt-5.5"
```

- [ ] **Step 2: Run the failing test**

Run:

```bash
pytest tests/test_llm_use_cases_registry.py::test_video_translate_av_sync_uses_gpt55_openrouter -q
```

Expected: FAIL because the defaults still reference the current model.

- [ ] **Step 3: Update defaults**

In `appcore/llm_use_cases.py`, update:

```python
"video_translate.av_localize": _uc(
    "video_translate.av_localize",
    "video_translate",
    "音画同步翻译",
    "按句子级时间轴生成本土化口播译文",
    "openrouter",
    "openai/gpt-5.5",
    "openrouter",
    "tokens",
),
"video_translate.av_rewrite": _uc(
    "video_translate.av_rewrite",
    "video_translate",
    "音画同步重写",
    "音画同步流程中的单句时长收敛重写",
    "openrouter",
    "openai/gpt-5.5",
    "openrouter",
    "tokens",
),
```

- [ ] **Step 4: Verify**

Run:

```bash
pytest tests/test_llm_use_cases_registry.py::test_video_translate_av_sync_uses_gpt55_openrouter -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add appcore/llm_use_cases.py tests/test_llm_use_cases_registry.py
git commit -m "feat(av-sync): use gpt55 for sentence localization"
```

## Task 3: Rework Sentence Localization Prompt and Schema

**Files:**
- Modify: `pipeline/av_translate.py`
- Test: `tests/test_av_translate.py`

- [ ] **Step 1: Add prompt/schema tests**

Add tests that call `_build_translate_messages(...)` and assert the system prompt includes:

```python
required = [
    "one target-language sentence for every source sentence",
    "Do not merge, split, reorder, or skip sentences",
    "native short-video spoken line",
    "target_chars_range",
    "Do not invent facts",
    "ElevenLabs",
]
for phrase in required:
    assert phrase in messages[0]["content"]
```

Also assert the JSON schema requires:

```python
props = av_translate.AV_TRANSLATE_RESPONSE_FORMAT["json_schema"]["schema"]["properties"]["sentences"]["items"]["properties"]
assert "localization_note" in props
assert "duration_risk" in props
assert "source_intent" in props
```

- [ ] **Step 2: Run failing tests**

Run:

```bash
pytest tests/test_av_translate.py -q
```

Expected: FAIL because the prompt/schema is still the old AV sync contract.

- [ ] **Step 3: Update response schema**

In `pipeline/av_translate.py`, make each sentence item require:

```python
"asr_index": {"type": "integer"},
"text": {"type": "string"},
"est_chars": {"type": "integer"},
"source_intent": {"type": "string"},
"localization_note": {"type": "string"},
"duration_risk": {
    "type": "string",
    "enum": ["ok", "may_be_short", "may_be_long"],
},
```

Required keys:

```python
["asr_index", "text", "est_chars", "source_intent", "localization_note", "duration_risk"]
```

- [ ] **Step 4: Replace the AV system prompt**

Use an English system prompt so tests are stable and the model receives unambiguous constraints:

```python
SYSTEM_PROMPT_TEMPLATE = """You are a senior localization writer for {target_market} short-form commerce videos.

Your job is sentence-level AV-sync localization into {target_language}.

Hard rules:
1. Return exactly one target-language sentence for every source sentence.
2. Do not merge, split, reorder, or skip sentences.
3. Preserve each source sentence's sales intent, emotional function, and information points.
4. Make every line sound like a native short-video spoken line in the target market, not translated copy.
5. Preserve the sentence role when provided: hook, pain point, demo, proof, or CTA.
6. Do not invent facts, prices, materials, certifications, claims, discounts, or guarantees.
7. Respect target_chars_range as closely as possible. If the range is tight, remove decoration before removing meaning.
8. Write for ElevenLabs TTS: short clauses, clear rhythm, no dense subordinate clauses, no stacked adjectives.
9. Prefer natural local idioms only when they preserve the source meaning and fit the video frame.
10. Mark duration_risk as may_be_long or may_be_short when the line may be hard to fit.
"""
```

- [ ] **Step 5: Preserve new fields in merge**

In `_merge_output_sentences`, include:

```python
"source_intent": raw_item.get("source_intent", ""),
"localization_note": raw_item.get("localization_note", raw_item.get("notes", "")),
"duration_risk": raw_item.get("duration_risk", "ok"),
```

- [ ] **Step 6: Verify**

Run:

```bash
pytest tests/test_av_translate.py -q
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add pipeline/av_translate.py tests/test_av_translate.py
git commit -m "feat(av-sync): strengthen sentence localization prompt"
```

## Task 4: Rebuild Duration Reconcile Around 95%-105%

**Files:**
- Modify: `pipeline/duration_reconcile.py`
- Test: `tests/test_duration_reconcile.py`

- [ ] **Step 1: Update threshold tests**

Change parametrized cases to assert:

```python
(5.0, 5.25, "ok", (1.0, 1.0)),
(5.0, 5.26, "needs_rewrite", (1.0, 1.0)),
(5.0, 4.75, "ok", (1.0, 1.0)),
(5.0, 4.74, "needs_expand", (1.0, 1.0)),
```

Add a speed helper test:

```python
def test_speed_adjustment_clamped_to_five_percent():
    assert compute_speed_for_target(5.0, 5.2) == pytest.approx(1.04)
    assert compute_speed_for_target(5.0, 5.4) is None
    assert compute_speed_for_target(5.0, 4.8) == pytest.approx(0.96)
    assert compute_speed_for_target(5.0, 4.6) is None
```

- [ ] **Step 2: Run failing reconcile tests**

Run:

```bash
pytest tests/test_duration_reconcile.py -q
```

Expected: FAIL because old statuses and speed bounds are still used.

- [ ] **Step 3: Add constants and speed helper**

In `pipeline/duration_reconcile.py`:

```python
MIN_DURATION_RATIO = 0.95
MAX_DURATION_RATIO = 1.05
MIN_TTS_SPEED = 0.95
MAX_TTS_SPEED = 1.05


def duration_ratio(target_duration: float, tts_duration: float) -> float:
    if target_duration <= 0:
        return 1.0
    return tts_duration / target_duration


def compute_speed_for_target(target_duration: float, tts_duration: float) -> float | None:
    if target_duration <= 0 or tts_duration <= 0:
        return 1.0
    speed = tts_duration / target_duration
    if MIN_TTS_SPEED <= speed <= MAX_TTS_SPEED:
        return round(speed, 4)
    return None
```

- [ ] **Step 4: Replace classification**

Use:

```python
def classify_overshoot(target_duration: float, tts_duration: float) -> tuple[str, float]:
    ratio = duration_ratio(target_duration, tts_duration)
    if MIN_DURATION_RATIO <= ratio <= MAX_DURATION_RATIO:
        return ("ok", 1.0)
    if ratio > MAX_DURATION_RATIO:
        return ("needs_rewrite", 1.0)
    return ("needs_expand", 1.0)
```

- [ ] **Step 5: Record attempts during rewrite**

When a sentence is rewritten, append:

```python
current.setdefault("attempts", []).append({
    "round": rewrite_round,
    "action": "shorten" if current_duration > current["target_duration"] else "expand",
    "before_text": before_text,
    "after_text": new_text,
    "target_duration": current["target_duration"],
    "tts_duration": current_duration,
    "duration_ratio": duration_ratio(current["target_duration"], current_duration),
    "status": status,
    "reason": "too_long" if current_duration > current["target_duration"] else "too_short",
})
```

- [ ] **Step 6: Apply speed only inside bounds**

After a rewrite produces audio, call `compute_speed_for_target(...)`. If it returns a value and it is not `1.0`, regenerate that segment with `speed=speed` and mark `speed_adjusted`. If it returns `None`, continue rewriting until `max_rewrite_rounds` is reached.

- [ ] **Step 7: Verify**

Run:

```bash
pytest tests/test_duration_reconcile.py -q
```

Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add pipeline/duration_reconcile.py tests/test_duration_reconcile.py
git commit -m "feat(av-sync): converge sentence durations within five percent"
```

## Task 5: Persist AV Debug State in Runtime

**Files:**
- Modify: `appcore/runtime.py`
- Test: `tests/test_appcore_runtime.py`

- [ ] **Step 1: Add runtime persistence assertions**

In the existing AV runtime test, assert:

```python
av_state = saved["variants"]["av"]
assert av_state["av_debug"]["model"] == "openai/gpt-5.5"
assert av_state["av_debug"]["summary"]["total_sentences"] == len(av_state["sentences"])
assert "duration_ratio" in av_state["sentences"][0]
assert "attempts" in av_state["sentences"][0]
```

- [ ] **Step 2: Run failing test**

Run:

```bash
pytest tests/test_appcore_runtime.py -q
```

Expected: FAIL because `av_debug` is not persisted yet.

- [ ] **Step 3: Add AV debug builder**

Near AV helpers in `appcore/runtime.py`, add:

```python
def _build_av_debug_state(sentences: list[dict], *, model: str = "openai/gpt-5.5") -> dict:
    total = len(sentences or [])
    warnings = [
        item for item in sentences or []
        if str(item.get("status") or "") not in {"ok", "rewritten_ok", "speed_adjusted"}
    ]
    return {
        "model": model,
        "summary": {
            "total_sentences": total,
            "ok_sentences": total - len(warnings),
            "warning_sentences": len(warnings),
        },
        "steps": [
            {"code": "sentence_localize", "label": "GPT-5.5 句级本土化", "status": "done"},
            {"code": "tts_first_pass", "label": "ElevenLabs 首轮生成", "status": "done"},
            {"code": "duration_converge", "label": "句级时长收敛", "status": "done"},
            {"code": "rebuild_outputs", "label": "重建音频和字幕", "status": "done"},
        ],
    }
```

- [ ] **Step 4: Persist debug state**

After `final_sentences = reconcile_duration(...)`, set:

```python
av_debug = _build_av_debug_state(final_sentences)
variant_state["av_debug"] = av_debug
```

Also include `av_debug` in `task_state.update(...)` under `variants`.

- [ ] **Step 5: Verify**

Run:

```bash
pytest tests/test_appcore_runtime.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add appcore/runtime.py tests/test_appcore_runtime.py
git commit -m "feat(av-sync): persist sentence convergence debug state"
```

## Task 6: Align Manual Rewrite With Sentence Convergence Rules

**Files:**
- Modify: `web/routes/task.py`
- Test: `tests/test_web_routes.py`

- [ ] **Step 1: Add manual rewrite assertions**

Extend the existing AV rewrite route test to assert:

```python
sentence = payload["task"]["variants"]["av"]["sentences"][0]
assert sentence["duration_ratio"] == pytest.approx(sentence["tts_duration"] / sentence["target_duration"])
assert 0.95 <= sentence["speed"] <= 1.05
assert "attempts" in sentence
```

- [ ] **Step 2: Run failing route test**

Run the focused rewrite route test:

```bash
pytest tests/test_web_routes.py -q -k "av_rewrite"
```

Expected: FAIL because the route still uses old fallback speed behavior.

- [ ] **Step 3: Import new helpers**

In `web/routes/task.py`, update imports:

```python
from pipeline.duration_reconcile import (
    classify_overshoot,
    compute_speed_for_target,
    duration_ratio,
)
```

- [ ] **Step 4: Replace route speed fallback**

After generating the manual rewrite audio, set:

```python
updated_sentence["duration_ratio"] = duration_ratio(
    float(updated_sentence.get("target_duration", 0.0) or 0.0),
    tts_duration,
)
updated_sentence.setdefault("attempts", [])
speed = compute_speed_for_target(
    float(updated_sentence.get("target_duration", 0.0) or 0.0),
    tts_duration,
)
if speed is not None and abs(speed - 1.0) > 0.001:
    updated_sentence["speed"] = speed
    updated_sentence["status"] = "speed_adjusted"
    tts.generate_segment_audio(
        text=new_text,
        voice_id=elevenlabs_voice_id,
        output_path=segment_path,
        language_code=target_language,
        speed=speed,
    )
    updated_sentence["tts_duration"] = float(tts.get_audio_duration(segment_path) or 0.0)
    updated_sentence["duration_ratio"] = duration_ratio(
        float(updated_sentence.get("target_duration", 0.0) or 0.0),
        updated_sentence["tts_duration"],
    )
elif updated_sentence["duration_ratio"] < 0.95:
    updated_sentence["status"] = "warning_short"
    updated_sentence["speed"] = 1.0
elif updated_sentence["duration_ratio"] > 1.05:
    updated_sentence["status"] = "warning_long"
    updated_sentence["speed"] = 1.0
else:
    updated_sentence["status"] = "ok"
    updated_sentence["speed"] = 1.0
```

Remove the old `1.12` fallback.

- [ ] **Step 5: Verify**

Run:

```bash
pytest tests/test_web_routes.py -q -k "av_rewrite"
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add web/routes/task.py tests/test_web_routes.py
git commit -m "fix(av-sync): keep manual rewrite within convergence bounds"
```

## Task 7: Render Sentence Convergence Panel

**Files:**
- Modify: `web/templates/_task_workbench.html`
- Modify: `web/templates/_task_workbench_scripts.html`
- Modify: `web/templates/_task_workbench_styles.html`
- Test: `tests/test_web_routes.py`

- [ ] **Step 1: Add UI route test**

Add or extend a task detail test with a fake AV task state containing `variants.av.sentences` and assert the HTML contains:

```python
assert "句级收敛" in html
assert "目标时长" in html
assert "偏差" in html
assert "GPT-5.5" in html
```

- [ ] **Step 2: Run failing UI test**

Run:

```bash
pytest tests/test_web_routes.py -q -k "av_sync or av_convergence"
```

Expected: FAIL because the new panel does not exist.

- [ ] **Step 3: Add panel shell**

In `_task_workbench.html`, after `avInsightsPanel`, add:

```html
<div class="card hidden" id="avConvergencePanel">
  <div class="av-convergence-head">
    <div>
      <h3>句级收敛</h3>
      <p>查看 GPT-5.5 句级本土化、ElevenLabs 实测时长、重写轮次和 95%-105% 微调结果。</p>
    </div>
    <span class="av-convergence-model" id="avConvergenceModel">GPT-5.5</span>
  </div>
  <div class="av-convergence-summary" id="avConvergenceSummary"></div>
  <div class="av-convergence-table-wrap">
    <table class="av-convergence-table">
      <thead>
        <tr>
          <th>句号</th>
          <th>原句</th>
          <th>最终译文</th>
          <th>目标时长</th>
          <th>当前时长</th>
          <th>偏差</th>
          <th>speed</th>
          <th>状态</th>
          <th>轮次</th>
          <th>操作</th>
        </tr>
      </thead>
      <tbody id="avConvergenceRows"></tbody>
    </table>
  </div>
</div>
```

- [ ] **Step 4: Add renderer**

In `_task_workbench_scripts.html`, call `renderAvConvergence()` from `renderTaskState()`. Implement:

```javascript
function renderAvConvergence() {
  const panel = document.getElementById("avConvergencePanel");
  const rows = document.getElementById("avConvergenceRows");
  const summary = document.getElementById("avConvergenceSummary");
  const model = document.getElementById("avConvergenceModel");
  if (!panel || !rows || !summary || !model) return;
  const av = currentTask?.variants?.av || {};
  const sentences = Array.isArray(av.sentences) ? av.sentences : [];
  if (!sentences.length) {
    panel.classList.add("hidden");
    rows.innerHTML = "";
    summary.innerHTML = "";
    return;
  }
  const debug = av.av_debug || {};
  model.textContent = debug.model || "GPT-5.5";
  const okCount = sentences.filter(s => ["ok", "rewritten_ok", "speed_adjusted"].includes(s.status || "")).length;
  summary.innerHTML = `
    <span>共 ${sentences.length} 句</span>
    <span>已收敛 ${okCount} 句</span>
    <span>需校对 ${sentences.length - okCount} 句</span>
  `;
  rows.innerHTML = sentences.map(sentence => {
    const target = Number(sentence.target_duration || 0);
    const duration = Number(sentence.tts_duration || 0);
    const ratio = target > 0 && duration > 0 ? duration / target : Number(sentence.duration_ratio || 0);
    const pct = ratio ? `${((ratio - 1) * 100).toFixed(1)}%` : "--";
    const attempts = Array.isArray(sentence.attempts) ? sentence.attempts : [];
    return `
      <tr>
        <td>${escapeHtml(sentence.asr_index ?? "--")}</td>
        <td><div class="av-convergence-source">${escapeHtml(sentence.source_text || "")}</div></td>
        <td><div class="av-convergence-text">${escapeHtml(sentence.final_text || sentence.text || "")}</div></td>
        <td>${fmtTime(target)}</td>
        <td>${fmtTime(duration)}</td>
        <td>${escapeHtml(pct)}</td>
        <td>${escapeHtml(sentence.speed ?? 1)}</td>
        <td><span class="av-convergence-status">${escapeHtml(sentence.status || "--")}</span></td>
        <td>${attempts.length}</td>
        <td><button type="button" class="btn btn-ghost btn-sm" onclick="openAvRewriteModal(${Number(sentence.asr_index)})">重写</button></td>
      </tr>
      ${attempts.length ? `<tr class="av-convergence-attempts"><td colspan="10">${attempts.map(a => `第 ${escapeHtml(a.round)} 轮：${escapeHtml(a.action)} · ${escapeHtml(a.status)} · ${escapeHtml(a.reason)}`).join("<br>")}</td></tr>` : ""}
    `;
  }).join("");
  panel.classList.remove("hidden");
}
```

- [ ] **Step 5: Add styles**

In `_task_workbench_styles.html`, add styles using existing variables:

```css
.av-convergence-head { display:flex; justify-content:space-between; gap:var(--space-4); align-items:flex-start; }
.av-convergence-head h3 { margin:0; font-size:var(--text-lg); }
.av-convergence-head p { margin:var(--space-1) 0 0; color:var(--fg-muted); font-size:var(--text-sm); }
.av-convergence-model { border:1px solid var(--border); border-radius:var(--radius-md); padding:3px 8px; color:var(--accent); background:var(--accent-subtle); font-size:var(--text-xs); }
.av-convergence-summary { display:flex; gap:var(--space-3); flex-wrap:wrap; margin:var(--space-4) 0; color:var(--fg-muted); font-size:var(--text-sm); }
.av-convergence-table-wrap { overflow:auto; border:1px solid var(--border); border-radius:var(--radius-lg); }
.av-convergence-table { width:100%; border-collapse:collapse; font-size:var(--text-sm); }
.av-convergence-table th, .av-convergence-table td { padding:var(--space-3); border-bottom:1px solid var(--border); vertical-align:top; text-align:left; }
.av-convergence-table th { color:var(--fg-muted); background:var(--bg-subtle); font-weight:600; white-space:nowrap; }
.av-convergence-source, .av-convergence-text { max-width:260px; white-space:normal; line-height:var(--leading); }
.av-convergence-status { display:inline-flex; border-radius:var(--radius-md); padding:2px 8px; background:var(--bg-muted); color:var(--fg); font-size:var(--text-xs); }
.av-convergence-attempts td { background:var(--bg-subtle); color:var(--fg-muted); font-family:var(--font-mono); font-size:var(--text-xs); }
```

- [ ] **Step 6: Verify**

Run:

```bash
pytest tests/test_web_routes.py -q -k "av_sync or av_convergence"
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add web/templates/_task_workbench.html web/templates/_task_workbench_scripts.html web/templates/_task_workbench_styles.html tests/test_web_routes.py
git commit -m "feat(av-sync): show sentence convergence panel"
```

## Task 8: Final Focused Regression

**Files:**
- No code changes unless verification exposes a regression.

- [ ] **Step 1: Run focused AV sync suite**

Run:

```bash
pytest tests/test_av_translate.py tests/test_duration_reconcile.py tests/test_appcore_runtime.py tests/test_av_sync_menu_routes.py -q
```

Expected: PASS.

- [ ] **Step 2: Run related route suite**

Run:

```bash
pytest tests/test_web_routes.py -q -k "av or rewrite or start_route_persists_av_translate_inputs"
```

Expected: PASS.

- [ ] **Step 3: Check git diff**

Run:

```bash
git diff --check
git status --short
```

Expected: `git diff --check` has no output; status only shows intended files if there are uncommitted changes.

- [ ] **Step 4: Commit remaining verification fixes**

If verification required a small fix:

```bash
git add <changed-files>
git commit -m "test(av-sync): cover sentence convergence regression"
```

If no fix was required, do not create an empty commit.

## Self-Review

- Spec coverage: tasks cover entry bug, GPT-5.5 defaults, sentence prompt/schema, duration convergence, debug state, manual rewrite, UI visualization, and regression verification.
- Completeness scan: no unresolved marker or unspecified test/fix steps remain.
- Type consistency: `duration_ratio`, `attempts`, `av_debug`, `final_text`, and status names are consistently used across runtime, route, and UI tasks.
