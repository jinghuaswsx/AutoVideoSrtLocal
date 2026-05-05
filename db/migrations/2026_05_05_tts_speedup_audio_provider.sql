-- TTS speedup quality review sends audio media.
-- Keep existing audio-capable Gemini overrides, but move stale OpenRouter rows
-- away from a provider that cannot accept audio/mpeg.

UPDATE llm_use_case_bindings
SET provider_code = 'gemini_vertex',
    model_id = 'gemini-3-flash-preview',
    extra_config = NULL,
    enabled = 1
WHERE use_case_code = 'video_translate.tts_speedup_quality_review'
  AND provider_code = 'openrouter';

INSERT INTO llm_use_case_bindings
    (use_case_code, provider_code, model_id, extra_config, enabled, updated_by)
SELECT
    'video_translate.tts_speedup_quality_review',
    'gemini_vertex',
    'gemini-3-flash-preview',
    NULL,
    1,
    NULL
WHERE NOT EXISTS (
    SELECT 1
    FROM llm_use_case_bindings
    WHERE use_case_code = 'video_translate.tts_speedup_quality_review'
);
