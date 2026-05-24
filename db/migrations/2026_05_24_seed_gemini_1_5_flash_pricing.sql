-- db/migrations/2026_05_24_seed_gemini_1_5_flash_pricing.sql
-- Seed base pricing for Gemini 1.5 Flash model (prompts <= 128K tokens)
-- Base input: $0.075 per 1M tokens ($0.075 * 6.8 = 0.51 CNY / 1M tokens = 0.00000051 CNY/token)
-- Base output: $0.30 per 1M tokens ($0.30 * 6.8 = 2.04 CNY / 1M tokens = 0.00000204 CNY/token)

INSERT INTO ai_model_prices (
  provider,
  model,
  units_type,
  unit_input_cny,
  unit_output_cny,
  unit_flat_cny,
  note
)
VALUES
  ('gemini_aistudio', 'gemini-1.5-flash', 'tokens', 0.00000051, 0.00000204, NULL, 'Gemini 1.5 Flash Base: 0.075/0.30 USD/M ×6.8'),
  ('gemini_vertex', 'gemini-1.5-flash', 'tokens', 0.00000051, 0.00000204, NULL, 'Gemini 1.5 Flash Base: 0.075/0.30 USD/M ×6.8'),
  ('gemini_vertex_adc', 'gemini-1.5-flash', 'tokens', 0.00000051, 0.00000204, NULL, 'Gemini 1.5 Flash Base: 0.075/0.30 USD/M ×6.8'),
  ('openrouter', 'google/gemini-1.5-flash', 'tokens', 0.00000051, 0.00000204, NULL, 'OpenRouter Gemini 1.5 Flash Base: 0.075/0.30 USD/M ×6.8')
ON DUPLICATE KEY UPDATE
  units_type = VALUES(units_type),
  unit_input_cny = VALUES(unit_input_cny),
  unit_output_cny = VALUES(unit_output_cny),
  unit_flat_cny = VALUES(unit_flat_cny),
  note = VALUES(note);
