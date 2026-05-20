# Conditional ASR Gap Audio Window Design

Date: 2026-05-20

## Anchors

- `AGENTS.md`: video translation/TTS changes must be document-driven, scoped to an isolated worktree, and verified with focused tests.
- `docs/superpowers/specs/2026-05-20-omni-asr-window-audio-alignment-design.md`: ASR windows are the translated speech placement authority when source speech has real gaps.
- `docs/superpowers/specs/2026-05-07-omni-translate-merge-design.md`: Omni exposes both `five_round_rewrite` and `sentence_reconcile`; Multi remains the stable production path.
- `docs/superpowers/specs/2026-05-18-english-redub-speed-aware-voice-match-design.md`: English redub reuses Omni sentence-level `sentence_reconcile`.

## User Rule

The ASR-window behavior must apply to:

1. English redub.
2. Omni translate.
3. Multi translate.

For online Multi translate, normal tasks must keep the old stable logic. The new behavior is only enabled when ASR timing contains obvious no-speech windows, including leading, middle, or trailing gaps.

## Problem

`sentence_reconcile` now preserves large ASR no-speech windows. However, Multi and Omni tasks using `five_round_rewrite` still converge TTS toward the whole video duration. For videos with a long music lead-in or silent tail, that makes the generated narration try to fill windows where ASR detected no speech.

## Design

Add a shared ASR gap analysis helper:

```text
analyze_asr_window_gaps(segments, video_duration, preserve_gap_threshold=1.0, max_gap=0.25)
```

The helper reads source `start_time` / `end_time` windows and returns:

- whether any leading, middle, or trailing gap is at least `preserve_gap_threshold`;
- total preserved gap duration;
- an active speech budget equal to `video_duration - preserved_gap_total`;
- diagnostics for the task state and UI/debug JSON.

For `sentence_reconcile` paths, keep the existing ASR-window scheduler. It already preserves only large gaps and compacts micro-gaps.

For `five_round_rewrite` paths:

1. Before the duration loop, analyze `script_segments`.
2. If no large ASR gap is found, keep the existing behavior exactly:
   - target duration is the full video duration;
   - final audio is a normal concatenated TTS track;
   - `timeline_manifest` is built as before.
3. If a large ASR gap is found:
   - run the existing five-round rewrite loop against the active speech budget, not the full video duration;
   - after TTS convergence, schedule final TTS segments with `apply_asr_window_audio_schedule`;
   - rebuild `tts_full.<variant>.mp3` from segment files with silence through the original full video duration;
   - do not attach `timeline_manifest` for compose, so the original video timing is preserved instead of trimming video frames to the speech-only timeline;
   - store `audio_timeline_mode="asr_window_conditional"` and `asr_window_gap_analysis` diagnostics.

This keeps old Multi behavior for normal continuous-speech videos while fixing videos that have music/silence gaps.

CapCut exports must keep the same audio authority as hard-subtitle MP4 exports: TTS is on the generated audio track and preserved background/music is on the optional `ambience` track. The copied source video track is for picture only and must have its embedded audio muted in `draft_content.json`; otherwise the original source vocal can play on top of the translated TTS inside CapCut.

## Module Coverage

- English redub: already defaults to `sentence_reconcile`; add tests proving the route remains ASR-window scheduled.
- Omni translate: `sentence_reconcile` presets use the sentence scheduler; `five_round_rewrite` presets get conditional gap handling through the shared default TTS loop.
- Multi translate: keeps default `five_round_rewrite`; conditional handling triggers only when ASR gaps are detected.

## Non-Goals

- Do not force all Multi tasks onto sentence-level translation.
- Do not change ASR, voice matching, translation prompts, or language selection.
- Do not remove existing `five_round_rewrite` convergence, speedup, best-pick, or truncation behavior for continuous-speech tasks.
- Do not create TTS for no-ASR windows.

## Verification

Focused tests must prove:

1. Gap analysis returns disabled for continuous ASR windows.
2. Gap analysis detects leading, middle, and trailing gaps.
3. Multi `five_round_rewrite` uses the original full-video target when no large gap exists.
4. Multi `five_round_rewrite` uses the active speech budget and ASR-window rebuild when a large gap exists.
5. Sentence-level English redub and Omni paths continue to write ASR-window timeline metadata.
6. CapCut projects mute video-track embedded audio while leaving TTS and ambience audio tracks active.
