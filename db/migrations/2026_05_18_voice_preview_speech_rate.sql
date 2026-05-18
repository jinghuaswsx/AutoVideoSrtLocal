CREATE TABLE IF NOT EXISTS voice_preview_speech_rate (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  voice_id VARCHAR(64) NOT NULL,
  language VARCHAR(32) NOT NULL,
  preview_url_hash VARCHAR(64) NOT NULL,
  words_per_second DECIMAL(8,4) DEFAULT NULL,
  chars_per_second DECIMAL(8,4) DEFAULT NULL,
  sample_duration DECIMAL(10,3) DEFAULT NULL,
  source VARCHAR(32) NOT NULL DEFAULT 'preview',
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  UNIQUE KEY uq_voice_preview_rate (voice_id, language, preview_url_hash),
  KEY idx_voice_preview_rate_lang_voice (language, voice_id)
);
