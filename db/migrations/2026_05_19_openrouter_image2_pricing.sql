-- Docs-anchor: docs/superpowers/specs/2026-04-24-openrouter-openai-image2-image-translate-design.md
-- API 账单价目表：补齐 OpenRouter GPT-5.4 Image 2 的官方 token 价和三档图片兜底价。
-- OpenRouter 图像调用响应里的 usage.cost 优先；这些行只用于 response cost 缺失时的 pricebook fallback 和后台定价页展示。
-- 汇率沿用 config.USD_TO_CNY=6.8。

INSERT INTO ai_model_prices (
  provider, model, units_type,
  unit_input_cny, unit_output_cny, unit_flat_cny, note
) VALUES (
  'openrouter',
  'openai/gpt-5.4-image-2',
  'tokens',
  0.00005440,
  0.00010200,
  NULL,
  'OpenRouter GPT-5.4 Image 2：input $8/M、output $15/M；图像调用 response cost 优先'
)
ON DUPLICATE KEY UPDATE
  units_type = VALUES(units_type),
  unit_input_cny = VALUES(unit_input_cny),
  unit_output_cny = VALUES(unit_output_cny),
  unit_flat_cny = VALUES(unit_flat_cny),
  note = VALUES(note),
  updated_at = CURRENT_TIMESTAMP;

INSERT INTO ai_model_prices (
  provider, model, units_type,
  unit_input_cny, unit_output_cny, unit_flat_cny, note
) VALUES
  (
    'openrouter',
    'openai/gpt-5.4-image-2:low',
    'images',
    NULL,
    NULL,
    0.04080000,
    'OpenRouter Image 2 Low：1K fallback $0.006/image×6.8；2K/实价以 response cost 优先'
  ),
  (
    'openrouter',
    'openai/gpt-5.4-image-2:mid',
    'images',
    NULL,
    NULL,
    0.36040000,
    'OpenRouter Image 2 Medium：1K fallback $0.053/image×6.8；response cost 优先'
  ),
  (
    'openrouter',
    'openai/gpt-5.4-image-2:high',
    'images',
    NULL,
    NULL,
    1.43480000,
    'OpenRouter Image 2 High：1K fallback $0.211/image×6.8；response cost 优先'
  )
ON DUPLICATE KEY UPDATE
  units_type = VALUES(units_type),
  unit_input_cny = VALUES(unit_input_cny),
  unit_output_cny = VALUES(unit_output_cny),
  unit_flat_cny = VALUES(unit_flat_cny),
  note = VALUES(note),
  updated_at = CURRENT_TIMESTAMP;
