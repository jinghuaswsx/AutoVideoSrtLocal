# Omni Video Size Adjustment Design

- Date: 2026-06-08
- Module: Omni translate and Omni V2 final video output
- Anchors:
  - `AGENTS.md`: document-driven code and focused pytest rules.
  - `docs/superpowers/specs/2026-05-14-omni-final-fallback-compose-summary-design.md`: final output diagnostics must remain visible on the detail page.
  - `web/templates/CLAUDE.md`: translation detail additions must stay inside the shared task workbench step card system.

## Problem

Omni translation can produce a final hard-subtitle MP4 larger than the delivery limit. Operators need the final downloadable video to stay within 100 MB. When the composed output is too large, the system must calculate a lower bitrate, re-encode the hard-subtitle video, and show exactly which bitrate changed.

The user wording mentions "100 Mbps (100 兆)", but the examples and goal are file-size based: a 200 MB video must be reduced to a final file under 100 MB. This design therefore treats the hard requirement as a 100 MB file-size limit and displays bitrate separately in kbps / Mbps.

## Goal

1. After `compose` finishes, Omni and Omni V2 run a `video_size_adjustment` step before `export`.
2. The step checks the final hard-subtitle MP4 size.
3. If the file is already at or below 100 MB, keep it unchanged and record a "skipped" adjustment summary.
4. If the file is larger than 100 MB, compute a target total bitrate from duration and file-size budget, subtract the planned audio bitrate to get a target video bitrate, and re-encode with ffmpeg.
5. The final `result.hard_video` and `preview_files.hard_video` must point to the adjusted file when re-encoding occurs.
6. The CapCut export package must use the same adjusted final hard-video file only when `video_size_adjustment` actually re-encoded the video. If no re-encode was necessary, it must keep the editable source timeline with separate TTS audio, subtitle, and accompaniment tracks.
7. The detail page always shows a visible step card named "视频大小调整" once the step exists in the pipeline, regardless of whether re-encoding was required. The card must state the outcome, original size, final size, original bitrate, target bitrate, audio bitrate, and video bitrate before/after.

## Non-Goals

1. Do not change non-Omni multi-language, DE/FR/JA, dialogue, copywriting, or subtitle removal flows.
2. Do not add a database table. Persist the summary in task state and artifacts.
3. Do not run full pytest for this local change unless the focused selector indicates broad coverage is necessary.

## Bitrate Formula

Definitions:

- `limit_bytes = 100 * 1024 * 1024`
- `safety_ratio = 0.96`
- `budget_bits = floor(limit_bytes * safety_ratio * 8)`
- `target_total_bitrate_bps = floor(budget_bits / duration_seconds)`
- `target_audio_bitrate_bps = min(original_audio_bitrate_bps, 128000)` with a fallback of `128000`
- `raw_target_video_bitrate_bps = target_total_bitrate_bps - target_audio_bitrate_bps`
- `target_video_bitrate_bps = floor(raw_target_video_bitrate_bps / 1000000) * 1000000` when the budget allows at least one 1000 kbps step.
- `target_total_bitrate_bps = target_video_bitrate_bps + target_audio_bitrate_bps` after video bitrate snapping.

Rules:

1. If `duration_seconds <= 0`, fail the size adjustment step. Without duration there is no safe bitrate budget.
2. Clamp `target_audio_bitrate_bps` to at least 64 kbps and at most 128 kbps.
3. The target video bitrate must use whole 1000 kbps steps whenever the size budget can fit that step. Examples: `4000k`, `5000k`, `24000k`. This is intentionally rounded down, not up, so the automatic adjustment stays under 100 MB while preserving the highest safe bitrate.
4. For very long videos whose budget is below the first 1000 kbps video step, use the minimum emergency video bitrate of 300 kbps, still encoded as an integer `k` value, and rely on the final size verification.
5. Run ffmpeg with `-c:v libx264 -preset slow -b:v <target> -maxrate <target> -bufsize <2*target> -c:a aac -b:a <audio> -movflags +faststart`.
6. After encoding, stat the output file. If it is still above 100 MB, retry once with the observed overshoot ratio applied to the total budget, then snap the video bitrate down again. Audio keeps the clamp.
7. If the second output is still above 100 MB, fail the step instead of silently delivering an oversized file.

## Data Shape

Persist the summary in `task.video_size_adjustment`, `variants[variant].video_size_adjustment`, and `artifacts.video_size_adjustment`:

```json
{
  "status": "skipped | adjusted | failed",
  "limit_bytes": 104857600,
  "safety_ratio": 0.96,
  "input_path": "/path/to/input.mp4",
  "output_path": "/path/to/final.mp4",
  "input_size_bytes": 209715200,
  "output_size_bytes": 99614720,
  "duration_seconds": 32.5,
  "original_total_bitrate_bps": 51622240,
  "original_video_bitrate_bps": 51200000,
  "original_audio_bitrate_bps": 192000,
  "target_total_bitrate_bps": 24128000,
  "target_video_bitrate_bps": 24000000,
  "target_audio_bitrate_bps": 128000,
  "attempts": [
    {
      "attempt": 1,
      "target_total_bitrate_bps": 24128000,
      "target_video_bitrate_bps": 24000000,
      "target_audio_bitrate_bps": 128000,
      "output_size_bytes": 99614720
    }
  ],
  "message": "视频大小 200.0 MB，已按总码率 51.20 Mbps (51200 kbps) -> 24.13 Mbps (24128 kbps)、视频码率 51.20 Mbps (51200 kbps) -> 24.00 Mbps (24000 kbps) 重编码，最终 95.0 MB"
}
```

## UI

The shared workbench renders `#step-video_size_adjustment` between "视频合成" and "CapCut 导出".

Card title: `视频大小调整`.

Card contents:

- Status badge: `已检查，无需调整`, `已调整`, or `失败`.
- Size metrics: original size, final size, limit.
- Bitrate metrics: original total bitrate, target total bitrate, original video bitrate, target video bitrate, original audio bitrate, target audio bitrate.
- Attempt list when retry occurs.
- A note that MB is file size and Mbps/kbps are bitrate units.

The renderer must not depend only on `artifacts.video_size_adjustment`. If the artifact is missing but `task.video_size_adjustment` or `variants[variant].video_size_adjustment` exists, the frontend synthesizes the same `video_size_adjustment` artifact so completed tasks still show the card. This is required for resumptions, older in-flight tasks, and any edge case where the summary persisted but the preview artifact did not.

## CapCut Export

`export` runs after `video_size_adjustment`. When a `video_size_adjustment` summary exists with `status` of `adjusted`, CapCut export must use `summary.output_path` / `result.hard_video` as its video input. This keeps the video resource inside the CapCut archive aligned with the final downloadable video and under the 100 MB limit.

Because the adjusted input is already the final hard-subtitle video, CapCut export uses a final-video mode for this case:

- copy the adjusted final video into `Resources/auto_generated`;
- create one video segment that plays the final video from start to finish;
- do not rebuild the old source-video timeline from `timeline_manifest`;
- do not add duplicate TTS audio or subtitle tracks over the hard-subtitle final video;
- record `final_video_mode: true` and `video_source: "video_size_adjustment"` in the CapCut export manifest.

If the adjustment step has not run, or if its summary status is `skipped`, keep the existing CapCut timeline rebuild behavior. The `skipped` case still records the size-check artifact, but the CapCut project must remain editable with separate video, TTS audio, subtitle, and accompaniment resources.

## Resume and Restart

Restart clears the top-level and variant `video_size_adjustment` fields.

Resume from `compose` or earlier clears stale `video_size_adjustment`, its artifact, and the adjusted hard-video preview. Resume from `video_size_adjustment` clears only that step and downstream export state.

## Verification

Focused tests:

- `tests/test_compose.py`: bitrate calculation and ffmpeg command construction.
- `tests/test_pipeline_runner.py`: `video_size_adjustment` rewrites the hard-video result and records artifacts.
- `tests/test_pipeline_runner.py`: CapCut export uses the adjusted final hard video only after `video_size_adjustment.status == "adjusted"` and keeps editable tracks when the status is `skipped`.
- `tests/test_runtime_omni_dispatch.py`: Omni step order inserts `video_size_adjustment` after `compose` and before `export`.
- `tests/test_omni_translate_routes.py`: restart/resume clears stale adjustment state.
- `tests/test_translate_detail_shell_templates.py`: shared workbench includes the new card and renderer.

Run:

```bash
python3 scripts/pytest_related.py --base origin/master --run
```

Do not run full `pytest -q` unless this focused selector or the user explicitly requests it.
