-- 补齐 OpenRouter Claude Sonnet 4.6 与 ElevenLabs Scribe v2 ASR 的定价兜底。
-- 单位：unit_input_cny / unit_output_cny 为 CNY/每token；unit_flat_cny 为平铺单位价
-- （seconds=CNY/秒）。USD_TO_CNY 取 6.8（与 config.py 一致）。
-- 全部使用 INSERT ... ON DUPLICATE KEY UPDATE，可重复执行。

-- 1) OpenRouter Claude Sonnet 4.6：响应不在 usage.cost 里返金额，pricebook 兜底
--    官方价：input $3/1M tokens、output $15/1M tokens
--    × 6.8 → input ¥0.00002040/token、output ¥0.00010200/token
INSERT INTO ai_model_prices (provider, model, units_type, unit_input_cny, unit_output_cny, note)
VALUES (
  'openrouter', 'anthropic/claude-sonnet-4.6', 'tokens',
  0.00002040, 0.00010200,
  'OpenRouter Claude Sonnet 4.6 兜底：input $3/1M、output $15/1M ×6.8；OpenRouter 不在 usage.cost 里返 anthropic 金额'
)
ON DUPLICATE KEY UPDATE
  unit_input_cny=VALUES(unit_input_cny),
  unit_output_cny=VALUES(unit_output_cny),
  units_type=VALUES(units_type),
  note=VALUES(note),
  updated_at=CURRENT_TIMESTAMP;

-- 2) ElevenLabs Scribe v2 ASR：按音频秒计费
--    Scale 套餐超额价 $0.33/小时
--    $0.33 ÷ 3600 × 6.8 ≈ ¥0.00062333/秒
--    历史命名分叉（elevenlabs_tts / elevenlabs_scribe 都指向同一 ASR 服务），两条都补
INSERT INTO ai_model_prices (provider, model, units_type, unit_flat_cny, note) VALUES
  ('elevenlabs_tts',    'scribe_v2', 'seconds', 0.00062333,
   'ElevenLabs Scribe v2 ASR：Scale 套餐超额价 $0.33/小时 ×6.8 ÷3600'),
  ('elevenlabs_scribe', 'scribe_v2', 'seconds', 0.00062333,
   'ElevenLabs Scribe v2 ASR：Scale 套餐超额价 $0.33/小时 ×6.8 ÷3600；与 elevenlabs_tts 同价')
ON DUPLICATE KEY UPDATE
  unit_flat_cny=VALUES(unit_flat_cny),
  units_type=VALUES(units_type),
  note=VALUES(note),
  updated_at=CURRENT_TIMESTAMP;
