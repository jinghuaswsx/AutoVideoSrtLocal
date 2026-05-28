-- Split material AI evaluation by country and pin default binding to Gemini 3 Flash.
-- Docs-anchor: docs/superpowers/specs/2026-05-28-material-evaluation-country-split-design.md

INSERT INTO llm_use_case_bindings (
  use_case_code,
  provider_code,
  model_id,
  extra_config,
  enabled,
  updated_by
) VALUES (
  'material_evaluation.evaluate',
  'openrouter',
  'google/gemini-3-flash-preview',
  NULL,
  1,
  NULL
)
ON DUPLICATE KEY UPDATE
  provider_code = VALUES(provider_code),
  model_id = VALUES(model_id),
  enabled = VALUES(enabled);
