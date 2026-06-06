-- Docs-anchor: docs/superpowers/specs/2026-05-22-image-translate-apimart-image2-parallel-default.md
-- Force the current persisted image translation default back to APIMART Image 2.

UPDATE system_settings
SET value = 'apimart',
    updated_at = CURRENT_TIMESTAMP
WHERE `key` = 'image_translate.channel';

INSERT INTO system_settings (`key`, `value`) VALUES
  ('image_translate.channel', 'apimart'),
  ('image_translate.default_model.apimart', 'gpt-image-2')
ON DUPLICATE KEY UPDATE
  `value` = VALUES(`value`),
  updated_at = CURRENT_TIMESTAMP;
