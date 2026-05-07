-- Doubao Seed 2.0 Lite 精确价格。
-- 官方价：input 0.6 元/百万 tokens，output 3.6 元/百万 tokens。
-- ai_model_prices 按 CNY/token 存储。

INSERT INTO ai_model_prices (provider, model, units_type, unit_input_cny, unit_output_cny, unit_flat_cny, note)
VALUES (
  'doubao',
  'doubao-seed-2-0-lite-260215',
  'tokens',
  0.00000060,
  0.00000360,
  NULL,
  'Doubao Seed 2.0 Lite：官方价 input 0.6 元/百万 tokens、output 3.6 元/百万 tokens'
)
ON DUPLICATE KEY UPDATE
  units_type=VALUES(units_type),
  unit_input_cny=VALUES(unit_input_cny),
  unit_output_cny=VALUES(unit_output_cny),
  unit_flat_cny=VALUES(unit_flat_cny),
  note=VALUES(note),
  updated_at=CURRENT_TIMESTAMP;
