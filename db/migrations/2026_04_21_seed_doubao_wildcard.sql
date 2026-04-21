-- db/migrations/2026_04_21_seed_doubao_wildcard.sql
-- 给 doubao / gemini_aistudio / gemini_vertex 加通配兜底价，避免未入表的模型落成 unknown。
-- 单价用"待复核"占位，由管理员在 /settings?tab=pricing 里复核。

INSERT INTO ai_model_prices (provider, model, units_type, unit_input_cny, unit_output_cny, unit_flat_cny, note)
VALUES
  ('doubao', '*', 'tokens', 0.00000600, 0.00001200, NULL, '待复核：参考 doubao-1-5-pro 单价，所有未精确入表的豆包模型走此行'),
  ('gemini_aistudio', '*', 'tokens', 0.00000204, 0.00000816, NULL, '待复核：默认参考 gemini-2.5-flash 单价'),
  ('gemini_vertex', '*', 'tokens', 0.00000816, 0.00003264, NULL, '待复核：默认参考 gemini-3.1-flash-lite 单价')
ON DUPLICATE KEY UPDATE
  units_type = VALUES(units_type),
  unit_input_cny = VALUES(unit_input_cny),
  unit_output_cny = VALUES(unit_output_cny),
  unit_flat_cny = VALUES(unit_flat_cny),
  note = VALUES(note);
