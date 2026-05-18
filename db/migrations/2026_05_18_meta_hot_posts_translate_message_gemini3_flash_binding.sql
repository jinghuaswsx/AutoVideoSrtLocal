-- Force Meta hot-post message translation to OpenRouter Gemini 3 Flash.
-- Docs-anchor: docs/superpowers/specs/2026-05-18-meta-hot-posts-translate-model-and-schedule-design.md

INSERT INTO llm_use_case_bindings (
  use_case_code,
  provider_code,
  model_id,
  extra_config,
  enabled,
  updated_by
) VALUES (
  'meta_hot_posts.translate_message',
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
