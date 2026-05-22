-- Docs-anchor: docs/superpowers/specs/2026-05-22-image-translate-apimart-image2-parallel-default.md
-- New image translation tasks default to APIMART Image 2. Parallel mode is a code-level task creation default.

INSERT INTO system_settings (`key`, `value`) VALUES
  ('image_translate.channel', 'apimart'),
  ('image_translate.default_model.apimart', 'gpt-image-2')
ON DUPLICATE KEY UPDATE
  `value` = VALUES(`value`),
  updated_at = CURRENT_TIMESTAMP;
