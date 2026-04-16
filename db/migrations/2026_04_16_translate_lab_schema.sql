-- 扩展 projects.type ENUM
ALTER TABLE `projects` MODIFY COLUMN `type` ENUM(
  'translation','copywriting','video_creation','video_review',
  'text_translate','de_translate','fr_translate','subtitle_removal',
  'translate_lab'
) NOT NULL DEFAULT 'translation';

-- ElevenLabs 全量音色库
CREATE TABLE IF NOT EXISTS `elevenlabs_voices` (
  `voice_id` VARCHAR(64) NOT NULL PRIMARY KEY,
  `name` VARCHAR(255) NOT NULL,
  `gender` VARCHAR(32) DEFAULT NULL,
  `age` VARCHAR(32) DEFAULT NULL,
  `language` VARCHAR(32) DEFAULT NULL,
  `accent` VARCHAR(64) DEFAULT NULL,
  `category` VARCHAR(64) DEFAULT NULL,
  `descriptive` VARCHAR(255) DEFAULT NULL,
  `preview_url` TEXT DEFAULT NULL,
  `audio_embedding` MEDIUMBLOB DEFAULT NULL,
  `labels_json` JSON DEFAULT NULL,
  `public_owner_id` VARCHAR(128) DEFAULT NULL,
  `synced_at` DATETIME NOT NULL,
  `updated_at` DATETIME NOT NULL ON UPDATE CURRENT_TIMESTAMP,
  KEY `idx_language` (`language`),
  KEY `idx_gender_language` (`gender`, `language`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 语速模型
CREATE TABLE IF NOT EXISTS `voice_speech_rate` (
  `id` BIGINT AUTO_INCREMENT PRIMARY KEY,
  `voice_id` VARCHAR(64) NOT NULL,
  `language` VARCHAR(32) NOT NULL,
  `chars_per_second` DECIMAL(6,3) NOT NULL,
  `sample_count` INT NOT NULL DEFAULT 1,
  `updated_at` DATETIME NOT NULL ON UPDATE CURRENT_TIMESTAMP,
  UNIQUE KEY `uniq_voice_lang` (`voice_id`, `language`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
