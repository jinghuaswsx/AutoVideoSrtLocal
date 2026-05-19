-- Docs-anchor: docs/superpowers/specs/2026-04-16-image-translate-design.md
-- 图片翻译默认通道切回 Google Vertex AI (ADC)，默认模型使用 Nano Banana 2。

INSERT INTO system_settings (`key`, `value`) VALUES
  ('image_translate.channel', 'cloud_adc'),
  ('image_translate.default_model.cloud_adc', 'gemini-3.1-flash-image-preview')
ON DUPLICATE KEY UPDATE
  `value` = VALUES(`value`),
  updated_at = CURRENT_TIMESTAMP;
