-- Register the GoogleWJ Vertex AI API-key channel.
-- Secrets are written later through llm_provider_configs / settings, not stored here.

INSERT IGNORE INTO llm_provider_configs (
  provider_code,
  display_name,
  group_code,
  model_id,
  extra_config
) VALUES
  ('google_wj_text',  'GoogleWJ Vertex AI (text)',  'text_llm', 'gemini-3.5-flash', JSON_OBJECT('location', 'global')),
  ('google_wj_image', 'GoogleWJ Vertex AI (image)', 'image',    'gemini-3.1-flash-image-preview', JSON_OBJECT('location', 'global'));

INSERT INTO ai_model_prices (
  provider,
  model,
  units_type,
  unit_input_cny,
  unit_output_cny,
  note
) VALUES
  ('google_wj', 'gemini-3.5-flash', 'tokens', 0.00001020, 0.00006120, 'GoogleWJ Vertex Gemini 3.5 Flash Standard: 1.5/9 USD/M x6.8')
ON DUPLICATE KEY UPDATE
  unit_input_cny = VALUES(unit_input_cny),
  unit_output_cny = VALUES(unit_output_cny),
  note = VALUES(note),
  updated_at = CURRENT_TIMESTAMP;
