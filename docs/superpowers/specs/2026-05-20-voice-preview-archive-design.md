# Voice Preview Archive

Date: 2026-05-20

## Goal

Localize ElevenLabs voice preview audio for every synced language and voice, and
archive metadata that operators need for review: local audio path, audio
duration, ASR transcript text, ASR utterances, source, status, and the current
preview URL hash.

The archive is keyed by `(voice_id, language, preview_url_hash)` so a changed
remote preview URL creates a new archive target instead of reusing stale audio
or transcript data.

## Behavior

- Voice-library sync/backfill downloads preview audio into
  `uploads/voice_preview_archive/<language>/`.
- Each archived item records:
  `voice_id`, `language`, `preview_url`, `preview_url_hash`, `local_path`,
  `duration_seconds`, `transcript_text`, `utterances_json`, `asr_source`,
  `status`, and `error`.
- ASR uses the same provider rule as preview-rate measurement:
  English uses Doubao ASR; other languages use ElevenLabs Scribe.
- Preview-rate rows may be updated from the archived ASR result, but the archive
  remains the durable source for transcript and local audio metadata.
- Voice-library API payloads expose `preview_local_url` only when the current
  preview URL hash has a ready archive and the local file exists.
- Browser playback in the voice library and shared TTS voice selector uses
  `preview_local_url` first. If it is missing, playback falls back to the
  existing remote `preview_url`.
- The local preview endpoint is login protected and serves only resolved archive
  files under `UPLOAD_DIR`.

## Non-Goals

- Do not replace actual TTS generation audio or measured `voice_speech_rate`.
- Do not require every voice to be archived before the UI is usable.
- Do not connect to Windows local MySQL for validation.

## Verification

- Unit tests cover local-preview URL annotation and missing-file fallback.
- Unit tests cover archive upsert fields, duration, transcript, and preview-rate
  update from ASR utterances.
- Static frontend tests prove local preview URLs are preferred while remote
  preview URLs remain the fallback.
- Route tests prove the preview endpoint returns 404 when no ready local archive
  exists.
