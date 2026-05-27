-- Move all Meta hot-post LLM use cases to OpenRouter Gemini model IDs.
-- Docs-anchor: AGENTS.md Meta hot-post multi-account and LLM use-case guidance.

INSERT INTO llm_use_case_bindings (
  use_case_code,
  provider_code,
  model_id,
  extra_config,
  enabled,
  updated_by
) VALUES
(
  'meta_hot_posts.categorize',
  'openrouter',
  'google/gemini-3.1-flash-lite',
  NULL,
  1,
  NULL
),
(
  'meta_hot_posts.translate_message',
  'openrouter',
  'google/gemini-3-flash-preview',
  NULL,
  1,
  NULL
),
(
  'meta_hot_posts.translate_product_title',
  'openrouter',
  'google/gemini-3.1-flash-lite',
  NULL,
  1,
  NULL
),
(
  'meta_hot_posts.europe_fit',
  'openrouter',
  'google/gemini-3-flash-preview',
  NULL,
  1,
  NULL
),
(
  'meta_hot_posts.europe_fit_translate',
  'openrouter',
  'google/gemini-3.1-flash-lite',
  NULL,
  1,
  NULL
),
(
  'meta_hot_posts.video_copyability',
  'openrouter',
  'google/gemini-3-flash-preview',
  NULL,
  1,
  NULL
),
(
  'meta_hot_posts.video_copyability_translate',
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
