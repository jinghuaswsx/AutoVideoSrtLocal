-- Fix OpenRouter Gemini 1.5 Flash model slug after live smoke test.
-- OpenRouter slug: google/gemini-flash-1.5
-- Docs-anchor: docs/superpowers/specs/2026-06-14-tabcut-video-translation-task-design.md

UPDATE llm_use_case_bindings
SET model_id = 'google/gemini-flash-1.5'
WHERE use_case_code = 'tabcut.translate_video_info'
  AND provider_code = 'openrouter';

INSERT INTO ai_model_prices (
  provider,
  model,
  units_type,
  unit_input_cny,
  unit_output_cny,
  unit_flat_cny,
  note
)
VALUES (
  'openrouter',
  'google/gemini-flash-1.5',
  'tokens',
  0.00000051,
  0.00000204,
  NULL,
  'OpenRouter Gemini 1.5 Flash Base: 0.075/0.30 USD/M x6.8'
)
ON DUPLICATE KEY UPDATE
  units_type = VALUES(units_type),
  unit_input_cny = VALUES(unit_input_cny),
  unit_output_cny = VALUES(unit_output_cny),
  unit_flat_cny = VALUES(unit_flat_cny),
  note = VALUES(note);
