-- Docs-anchor: docs/superpowers/specs/2026-05-19-image-translate-local-image2-low-cost-default.md
-- Keep image translation on the fixed low-cost local Image 2 path.

INSERT INTO system_settings (`key`, `value`) VALUES
  ('image_translate.channel', 'local_image_2'),
  ('image_translate.default_model.local_image_2', 'gpt-image-2'),
  ('image_translate.openrouter_openai_image2_enabled', '0')
ON DUPLICATE KEY UPDATE
  `value` = VALUES(`value`),
  updated_at = CURRENT_TIMESTAMP;
