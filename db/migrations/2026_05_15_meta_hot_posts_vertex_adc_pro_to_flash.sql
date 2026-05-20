-- Move the unified Meta hot-post video analysis queue from Vertex ADC Gemini 3.5 Flash to Gemini 3 Flash.
-- Docs-anchor: docs/superpowers/specs/2026-05-15-meta-hot-posts-unified-video-analysis-queue-design.md

INSERT INTO llm_use_case_bindings (
  use_case_code,
  provider_code,
  model_id,
  extra_config,
  enabled,
  updated_by
) VALUES
(
  'meta_hot_posts.europe_fit',
  'gemini_vertex_adc',
  'gemini-3-flash-preview',
  NULL,
  1,
  NULL
),
(
  'meta_hot_posts.video_copyability',
  'gemini_vertex_adc',
  'gemini-3-flash-preview',
  NULL,
  1,
  NULL
)
ON DUPLICATE KEY UPDATE
  provider_code = VALUES(provider_code),
  model_id = VALUES(model_id),
  enabled = VALUES(enabled);
