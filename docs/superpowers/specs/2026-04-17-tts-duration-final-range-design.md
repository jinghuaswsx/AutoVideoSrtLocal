# TTS Duration Final Range Design

## Goal

Fix DE/FR/EN TTS duration control so rewrite rounds do not stop early in the old
`[0.9v, 1.1v]` staging window. The loop should only stop early when the audio
lands in the final target window `[video_duration - 3, video_duration]`.

## Confirmed Behavior

1. Run at most 5 rewrite rounds.
2. Stop early only when `final_target_lo <= audio_duration <= final_target_hi`.
3. If all 5 rounds miss the final target window, choose the round whose audio
   is closest to the final window, not merely closest to `video_duration`.
4. If the selected audio is longer than the video, truncate the final audio to
   `video_duration`.
5. If the selected audio is shorter than the video, keep it as-is.

## Implementation Notes

- Keep the existing rewrite framework and target-word computation.
- Remove the old success condition tied to `[0.9v, 1.1v]`.
- Add an explicit distance-to-range helper for best-round selection.
- Replace tail-block deletion as the final over-length handling path with direct
  audio truncation.
- Keep downstream timeline generation consistent with the truncated final audio
  by updating final TTS segment durations to match the final audio length.

## Risks

- If final audio truncation does not update downstream timing metadata, soft
  compose may freeze video longer than the audio.
- Subtitle alignment must read the final audio path after truncation.

## Test Plan

- Round 2 may not stop merely because it lands in the old 10 percent window.
- Best-round fallback must use distance to `[video-3, video]`.
- Over-long final audio must be truncated.
- Short final audio must be reused without extra processing.
