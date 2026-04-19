-- 新建 ElevenLabs 共享库远端总量统计表
CREATE TABLE IF NOT EXISTS `elevenlabs_voice_library_stats` (
  `language`        VARCHAR(32) NOT NULL PRIMARY KEY,
  `total_available` INT          NOT NULL DEFAULT 0,
  `last_counted_at` DATETIME     NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 给 elevenlabs_voices 加 use_case 独立列（之前 use_case 只在 labels_json 里，新 API 已不再提供 labels 嵌套字段）
ALTER TABLE `elevenlabs_voices`
  ADD COLUMN `use_case` VARCHAR(64) DEFAULT NULL AFTER `descriptive`,
  ADD INDEX `idx_use_case` (`use_case`);
