# LLM 视频上传优化 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reduce LLM video/media request size across OpenRouter, Gemini, and Doubao multimodal paths without changing model bindings, prompts, or business outputs.

**Architecture:** Add one shared `appcore.llm_media_optimizer` module that prepares temporary LLM-only media paths and records metadata. Each business module chooses a named policy before calling `llm_client`; failures fall back to the original media or existing keyframe/text fallback. Debug payloads record both original and actual LLM media paths.

**Tech Stack:** Python 3.12, pytest, ffmpeg subprocess calls, existing `appcore.llm_client`, OpenRouter/Doubao/Gemini adapters.

---

## Docs Anchor

- Primary spec: `docs/superpowers/specs/2026-05-14-llm-video-upload-optimization-design.md`
- Existing shot-decompose reference: `docs/superpowers/specs/2026-05-14-omni-shot-decompose-480p-preprocess-design.md`

## File Structure

- Create `appcore/llm_media_optimizer.py`: shared policies, ffmpeg command construction, metadata, cleanup.
- Modify `pipeline/shot_decompose.py`: replace local optimizer with shared helper.
- Modify `pipeline/shot_notes.py`: prepare silent visual video before `invoke_generate`, update debug payload.
- Modify `appcore/material_evaluation.py`: optimize 15s eval clip before LLM call and debug payload.
- Modify `appcore/new_product_review.py`: benefits through shared material eval clip helper.
- Modify `appcore/video_ai_review.py` and `pipeline/video_ai_review.py`: prepare task/media-item videos for Vertex inline while preserving audio and debug details.
- Modify `pipeline/copywriting.py`: optimize video input before OpenRouter base64/Doubao URL payload; fall back to keyframes/text if base64 remains too large.
- Modify `appcore/push_quality_checks.py`: optimize 5s clip before LLM call.
- Modify `pipeline/omni_av_sync_audit.py`: optimize Doubao understand video before URL upload, update debug.
- Modify `pipeline/video_score.py`, `pipeline/video_csk.py`, `pipeline/video_review.py`: optimize Gemini Files API inputs while preserving audio.
- Tests: add/update focused pytest files listed in the spec.

## Task 1: Shared Optimizer

**Files:**
- Create: `appcore/llm_media_optimizer.py`
- Create: `tests/test_llm_media_optimizer.py`

- [ ] **Step 1: Write failing tests**

Add tests covering command shape, missing source fallback, ffmpeg failure fallback, cleanup, and dynamic inline bitrate:

```python
def test_prepare_video_for_llm_visual_policy_drops_audio(tmp_path, monkeypatch):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"source")
    calls = []
    monkeypatch.setattr("appcore.llm_media_optimizer.probe_media_info", lambda path: {"height": 1080, "duration": 10.0})
    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        Path(cmd[-1]).write_bytes(b"small")
    monkeypatch.setattr("appcore.llm_media_optimizer.subprocess.run", fake_run)
    media = prepare_video_for_llm(source, VISUAL_480P_SILENT, output_dir=tmp_path)
    assert media.optimized is True
    assert media.llm_path != str(source)
    assert "-an" in calls[0]
    assert "scale=-2:min(480\\,ih),fps=15" in calls[0]
```

- [ ] **Step 2: Verify RED**

Run:

```bash
pytest tests/test_llm_media_optimizer.py -q
```

Expected: fails because `appcore.llm_media_optimizer` does not exist.

- [ ] **Step 3: Implement minimal optimizer**

Implement dataclasses `VideoOptimizationPolicy` and `OptimizedMedia`, named policies `VISUAL_480P_SILENT`, `REVIEW_480P_AUDIO`, `SHORT_CLIP_AUDIO`, `VERTEX_INLINE_AUDIO`, `prepare_video_for_llm()`, `cleanup_optimized_media()`, and `media_debug_snapshot()`.

- [ ] **Step 4: Verify GREEN**

Run:

```bash
pytest tests/test_llm_media_optimizer.py -q
```

Expected: all tests pass.

## Task 2: Shot Decompose Migration

**Files:**
- Modify: `pipeline/shot_decompose.py`
- Test: `tests/test_shot_decompose.py`
- Test: `tests/test_runtime_omni_dispatch.py`

- [ ] **Step 1: Write failing migration assertion**

Update existing tests to assert `prepare_shot_decompose_media()` delegates to `appcore.llm_media_optimizer.prepare_video_for_llm()` and preserves current public `ShotDecomposeMedia` fields.

- [ ] **Step 2: Verify RED**

Run:

```bash
pytest tests/test_shot_decompose.py::test_decompose_shots_preprocesses_existing_video_before_llm -q
```

Expected: fails because shot_decompose still owns local ffmpeg logic.

- [ ] **Step 3: Replace local ffmpeg with shared helper**

Keep `ShotDecomposeMedia` compatibility or alias it to optimizer metadata. Preserve existing `decompose_shots(video_path, preprocess_video=False)` behavior used by Omni runtime debug.

- [ ] **Step 4: Verify feature**

Run:

```bash
pytest tests/test_shot_decompose.py tests/test_runtime_omni_dispatch.py::test_shot_decompose_debug_payload_uses_preprocessed_llm_video -q
```

Expected: pass.

## Task 3: Shot Notes P0

**Files:**
- Modify: `pipeline/shot_notes.py`
- Test: `tests/test_shot_notes.py`

- [ ] **Step 1: Write failing tests**

Add tests:

```python
def test_shot_notes_uses_optimized_visual_video_and_debug_snapshot(monkeypatch, tmp_path):
    original = tmp_path / "source.mp4"
    optimized = tmp_path / "source_480p.mp4"
    original.write_bytes(b"source")
    optimized.write_bytes(b"small")
    monkeypatch.setattr(
        shot_notes,
        "prepare_video_for_llm",
        lambda path, policy, output_dir=None: OptimizedMedia(
            original_path=str(original),
            llm_path=str(optimized),
            optimized=True,
            cleanup_path=str(optimized),
            original_bytes=6,
            llm_bytes=5,
            command=["ffmpeg", "-y", "-i", str(original), str(optimized)],
            error=None,
            policy_name="visual_480p_silent",
        ),
    )
    result = shot_notes.generate_shot_notes(video_path=original, script_segments=SCRIPT_SEGMENTS, target_language="en", target_market="US")
    assert captured["kwargs"]["media"] == [str(optimized)]
    assert result["_llm_debug_calls"][0]["request_payload"]["media"] == [str(optimized)]
    assert result["_llm_debug_calls"][0]["input_snapshot"][0]["original_video_path"] == str(original)
```

Also add failure fallback test where optimizer returns `error="ffmpeg failed"` and original path is used.

- [ ] **Step 2: Verify RED**

Run:

```bash
pytest tests/test_shot_notes.py::test_shot_notes_uses_optimized_visual_video_and_debug_snapshot -q
```

Expected: fails because current code passes original `video_path`.

- [ ] **Step 3: Implement shot notes optimization**

Use `VISUAL_480P_SILENT`; call cleanup in `finally`; build debug after optimizer selection so request payload records actual LLM path and snapshot records both paths.

- [ ] **Step 4: Verify feature**

Run:

```bash
pytest tests/test_shot_notes.py -q
```

Expected: pass.

## Task 4: Material Evaluation and New Product Review P0

**Files:**
- Modify: `appcore/material_evaluation.py`
- Test: `tests/test_material_evaluation.py`

- [ ] **Step 1: Write failing tests**

Add tests asserting `_make_eval_clip_15s()` still cuts 15s, then `evaluate_product_if_ready()` sends an optimized 15s path to `llm_client.invoke_generate()`. Add fallback test where optimizer fails and original 15s clip is sent.

- [ ] **Step 2: Verify RED**

Run:

```bash
pytest tests/test_material_evaluation.py::test_evaluate_ready_product_sends_optimized_15s_clip_to_llm -q
```

Expected: fails because current media path is the raw copy-cut 15s clip.

- [ ] **Step 3: Implement optimization**

After `_materialize_required_eval_video()` returns the clip, call `prepare_video_for_llm(video_path, SHORT_CLIP_AUDIO)`. Clean up optimized temporary file after `invoke_generate`. Store optimization metadata in `ai_evaluation_detail`.

- [ ] **Step 4: Verify feature**

Run:

```bash
pytest tests/test_material_evaluation.py -q
```

Expected: pass.

## Task 5: Video AI Review P0

**Files:**
- Modify: `appcore/video_ai_review.py`
- Modify: `pipeline/video_ai_review.py`
- Test: add `tests/test_video_ai_review_pipeline.py`

- [ ] **Step 1: Write failing tests**

Test task-state source/target videos are optimized before `pipeline.video_ai_review.assess()` calls `llm_client.invoke_generate()`. Test media-item path still keeps audio policy and debug uses actual LLM path.

- [ ] **Step 2: Verify RED**

Run:

```bash
pytest tests/test_video_ai_review_pipeline.py -q
```

Expected: fails because task videos are only size-warned and sent directly.

- [ ] **Step 3: Implement optimization**

Move existing `_compress_for_inline()` behavior into shared `VERTEX_INLINE_AUDIO` policy or wrap it through the shared helper. In `pipeline.video_ai_review.assess()`, optimize `source_video_path` and `target_video_path`, clean up after invoke, and include optimizer metadata in `_llm_debug_call`.

- [ ] **Step 4: Verify feature**

Run:

```bash
pytest tests/test_video_ai_review_pipeline.py tests/test_task_video_ai_review_service.py tests/test_media_item_video_ai_review_service.py -q
```

Expected: pass.

## Task 6: Copywriting P0

**Files:**
- Modify: `pipeline/copywriting.py`
- Test: `tests/test_copywriting_pipeline.py`
- Test: `tests/test_pipeline_robustness.py`

- [ ] **Step 1: Write failing tests**

Add tests that OpenRouter video content uses optimized path before `_video_to_base64_url()`, Doubao uploads optimized path, and oversized base64 after optimizer failure falls back to keyframes/text rather than raising.

- [ ] **Step 2: Verify RED**

Run:

```bash
pytest tests/test_copywriting_pipeline.py::test_generate_copy_uses_optimized_video_for_openrouter -q
```

Expected: fails because current code base64-encodes `video_path` directly.

- [ ] **Step 3: Implement optimization and fallback**

Before `use_video` content construction, prepare `llm_video_path` with `VISUAL_480P_SILENT`. Use the optimized path for base64 or Doubao URL upload. If OpenRouter video conversion still raises size error, continue with keyframes/product image/text and record fallback in `_debug`.

- [ ] **Step 4: Verify feature**

Run:

```bash
pytest tests/test_copywriting_pipeline.py tests/test_pipeline_robustness.py tests/test_copywriting_runtime.py -q
```

Expected: pass.

## Task 7: P1 Video Functions

**Files:**
- Modify: `appcore/push_quality_checks.py`
- Modify: `pipeline/omni_av_sync_audit.py`
- Modify: `pipeline/video_score.py`
- Modify: `pipeline/video_csk.py`
- Modify: `pipeline/video_review.py`
- Test: `tests/test_push_quality_checks.py`
- Test: `tests/test_omni_av_sync_audit.py`
- Test: `tests/test_video_csk.py`
- Test: add `tests/test_video_score.py`
- Test: add `tests/test_video_review_pipeline.py` if not already created

- [ ] **Step 1: Write failing tests per feature**

For each feature, assert `invoke_generate()` or URL upload receives optimized video path and failure falls back to original clip. Keep tests independent and run each test before implementing that feature.

- [ ] **Step 2: Implement `push_quality.check`**

Optimize the 5s clip with `SHORT_CLIP_AUDIO`; fallback to the 5s copy clip.

- [ ] **Step 3: Verify push quality**

Run:

```bash
pytest tests/test_push_quality_checks.py -q
```

Expected: pass.

- [ ] **Step 4: Implement `omni_av_sync.understand`**

Optimize with `REVIEW_480P_AUDIO` before Doubao URL upload; debug payload uses actual path and snapshot records original.

- [ ] **Step 5: Verify Omni AV sync**

Run:

```bash
pytest tests/test_omni_av_sync_audit.py -q
```

Expected: pass.

- [ ] **Step 6: Implement score/CSK/review**

Optimize with `REVIEW_480P_AUDIO`; cleanup after `invoke_generate`; keep model overrides unchanged.

- [ ] **Step 7: Verify video analysis**

Run:

```bash
pytest tests/test_video_csk.py tests/test_video_score.py tests/test_video_review_pipeline.py -q
```

Expected: pass.

## Task 8: Debug Payload and Integration Checks

**Files:**
- Modify: `appcore/llm_client.py` only if actual media estimate needs extra optimizer fields.
- Test: `tests/test_llm_client_invoke.py`
- Test: `tests/test_llm_providers_openrouter.py`
- Test: `tests/test_llm_providers_gemini_vertex.py`

- [ ] **Step 1: Write failing test only if needed**

If `request_payload.network_estimate` already reports actual optimized media path, do not modify `llm_client.py`. If optimizer metadata must be surfaced in billing payload, add a failing test first.

- [ ] **Step 2: Run adapter regression tests**

Run:

```bash
pytest tests/test_llm_client_invoke.py tests/test_llm_providers_openrouter.py tests/test_llm_providers_gemini_vertex.py -q
```

Expected: pass.

## Task 9: Full Verification and Publish

**Files:**
- No new source files unless verification exposes a bug.

- [ ] **Step 1: Run full targeted suite**

Run:

```bash
pytest tests/test_llm_media_optimizer.py \
  tests/test_shot_decompose.py \
  tests/test_shot_notes.py \
  tests/test_copywriting_pipeline.py \
  tests/test_pipeline_robustness.py \
  tests/test_copywriting_runtime.py \
  tests/test_material_evaluation.py \
  tests/test_push_quality_checks.py \
  tests/test_omni_av_sync_audit.py \
  tests/test_video_csk.py \
  tests/test_video_score.py \
  tests/test_video_review_pipeline.py \
  tests/test_llm_client_invoke.py \
  tests/test_llm_providers_openrouter.py \
  tests/test_llm_providers_gemini_vertex.py -q
```

Expected: pass.

- [ ] **Step 2: Inspect diff**

Run:

```bash
git diff --stat
git diff --check
```

Expected: no whitespace errors; changed files match this plan and spec.

- [ ] **Step 3: Publish because user explicitly requested上线**

Use the AGENTS publish sequence, not `deploy/publish.sh`, after tests pass:

```bash
git push origin HEAD:master
ssh -i C:/Users/admin/.ssh/CC.pem root@172.30.254.14 '
set -e
cd /opt/autovideosrt-test && git pull origin master --ff-only
systemctl restart autovideosrt-test && sleep 3
systemctl is-active autovideosrt-test
curl -s -o /dev/null -w "TEST HTTP %{http_code}\n" http://127.0.0.1:8080/
cd /opt/autovideosrt && git pull origin master --ff-only
if ! cmp -s deploy/autovideosrt.service /etc/systemd/system/autovideosrt.service; then
  cp deploy/autovideosrt.service /etc/systemd/system/ && systemctl daemon-reload
fi
systemctl restart autovideosrt && sleep 3
systemctl is-active autovideosrt
curl -s -o /dev/null -w "PROD HTTP %{http_code}\n" http://127.0.0.1/
'
```

Expected: both services active; HTTP is 200 or 302.

## Self-Review

- Spec coverage: P0, P1, P2 scan items are mapped to tasks; deployment gate is included.
- Self-review scan: no unresolved markers or open-ended implementation steps are required for execution.
- Type consistency: shared optimizer APIs are named consistently across all tasks.
