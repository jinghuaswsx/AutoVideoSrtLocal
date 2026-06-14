-- Switch Tabcut video translation to the user requested OpenRouter Gemini 3.1 Flash Lite model.
-- Docs-anchor: docs/superpowers/specs/2026-06-14-tabcut-video-translation-task-design.md

INSERT INTO llm_use_case_bindings (
  use_case_code,
  provider_code,
  model_id,
  extra_config,
  enabled,
  updated_by
) VALUES (
  'tabcut.translate_video_info',
  'openrouter',
  'google/gemini-3.1-flash-lite',
  NULL,
  1,
  NULL
)
ON DUPLICATE KEY UPDATE
  provider_code = VALUES(provider_code),
  model_id = VALUES(model_id),
  enabled = VALUES(enabled);

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
  'google/gemini-3.1-flash-lite',
  'tokens',
  0.00000170,
  0.00001020,
  NULL,
  'OpenRouter Gemini 3.1 Flash Lite: 0.25/1.50 USD/M x6.8'
)
ON DUPLICATE KEY UPDATE
  units_type = VALUES(units_type),
  unit_input_cny = VALUES(unit_input_cny),
  unit_output_cny = VALUES(unit_output_cny),
  unit_flat_cny = VALUES(unit_flat_cny),
  note = VALUES(note);
