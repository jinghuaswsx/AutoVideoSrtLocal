-- Switch Tabcut video translation to a currently available OpenRouter Gemini Flash endpoint.
-- Live smoke result: google/gemini-1.5-flash is invalid and google/gemini-flash-1.5 has no endpoints.
-- Docs-anchor: docs/superpowers/specs/2026-06-14-tabcut-video-translation-task-design.md

UPDATE llm_use_case_bindings
SET model_id = 'google/gemini-2.5-flash'
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
  'google/gemini-2.5-flash',
  'tokens',
  0.00000204,
  0.000017,
  NULL,
  'OpenRouter Gemini 2.5 Flash: 0.30/2.50 USD/M x6.8'
)
ON DUPLICATE KEY UPDATE
  units_type = VALUES(units_type),
  unit_input_cny = VALUES(unit_input_cny),
  unit_output_cny = VALUES(unit_output_cny),
  unit_flat_cny = VALUES(unit_flat_cny),
  note = VALUES(note);
