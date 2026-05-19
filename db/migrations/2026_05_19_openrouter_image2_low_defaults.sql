-- Docs-anchor: docs/superpowers/specs/2026-04-24-openrouter-openai-image2-image-translate-design.md
-- Make OpenRouter OpenAI Image 2 Low the default, cheapest image translation path.

INSERT INTO system_settings (`key`, `value`) VALUES
  ('image_translate.channel', 'openrouter'),
  ('image_translate.openrouter_openai_image2_enabled', '1'),
  ('image_translate.openrouter_openai_image2_default_quality', 'low'),
  ('image_translate.default_model.openrouter', 'openai/gpt-5.4-image-2:low')
ON DUPLICATE KEY UPDATE
  `value` = VALUES(`value`),
  updated_at = CURRENT_TIMESTAMP;

INSERT INTO system_settings (`key`, `value`) VALUES (
  'video_cover_model_defaults',
  '{"cover_generation":{"provider":"openrouter","model_id":"openai/gpt-5.4-image-2:low","execution_mode":"parallel"}}'
)
ON DUPLICATE KEY UPDATE
  `value` = JSON_SET(
    IF(JSON_VALID(`value`), `value`, '{}'),
    '$.cover_generation.provider', 'openrouter',
    '$.cover_generation.model_id', 'openai/gpt-5.4-image-2:low',
    '$.cover_generation.execution_mode', 'parallel'
  ),
  updated_at = CURRENT_TIMESTAMP;
