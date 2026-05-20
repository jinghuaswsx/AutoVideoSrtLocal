CREATE TABLE IF NOT EXISTS `voice_preview_archives` (
  `id` BIGINT AUTO_INCREMENT PRIMARY KEY,
  `voice_id` VARCHAR(64) NOT NULL,
  `language` VARCHAR(32) NOT NULL,
  `preview_url_hash` VARCHAR(64) NOT NULL,
  `preview_url` TEXT NOT NULL,
  `local_path` VARCHAR(1024) DEFAULT NULL,
  `duration_seconds` DECIMAL(10,3) DEFAULT NULL,
  `transcript_text` MEDIUMTEXT DEFAULT NULL,
  `utterances_json` JSON DEFAULT NULL,
  `asr_source` VARCHAR(64) DEFAULT NULL,
  `status` VARCHAR(32) NOT NULL DEFAULT 'ready',
  `error` TEXT DEFAULT NULL,
  `archived_at` DATETIME NOT NULL,
  `updated_at` DATETIME NOT NULL,
  UNIQUE KEY `uq_voice_preview_archive` (`voice_id`, `language`, `preview_url_hash`),
  KEY `idx_language` (`language`),
  KEY `idx_voice_language` (`voice_id`, `language`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
