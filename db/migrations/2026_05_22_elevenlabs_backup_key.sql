-- ElevenLabs standby API key row.
--
-- The primary row remains elevenlabs_tts. Operators can store a masked backup
-- secret in elevenlabs_tts_backup and switch the active slot through
-- elevenlabs_tts.extra_config.active_key_slot.

INSERT IGNORE INTO llm_provider_configs (provider_code, display_name, group_code, base_url)
VALUES ('elevenlabs_tts_backup', 'ElevenLabs 配音（备用 Key）', 'tts', 'https://api.elevenlabs.io/v1');

UPDATE llm_provider_configs
SET
  display_name = 'ElevenLabs 配音（备用 Key）',
  group_code = 'tts',
  base_url = COALESCE(NULLIF(base_url, ''), 'https://api.elevenlabs.io/v1')
WHERE provider_code = 'elevenlabs_tts_backup';
