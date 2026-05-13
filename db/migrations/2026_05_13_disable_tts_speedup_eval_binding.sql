-- TTS speedup AI evaluation sidecar was removed from runtime/web on 2026-05-13.
-- Keep historical rows, but disable the old binding so it no longer appears as
-- an active production LLM use case after older migrations insert it.
UPDATE llm_use_case_bindings
SET enabled = 0
WHERE use_case_code = 'video_translate.tts_speedup_quality_review';
