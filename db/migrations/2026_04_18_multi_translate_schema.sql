-- db/migrations/2026_04_18_multi_translate_schema.sql
-- 多语种视频翻译模块：新表 + projects.type 枚举扩展
-- 设计文档: docs/superpowers/specs/2026-04-18-multi-translate-design.md

-- ========== 1. projects.type 增加 'multi_translate' ==========
ALTER TABLE projects
  MODIFY COLUMN type ENUM(
    'translation','copywriting','video_creation','video_review',
    'text_translate','de_translate','fr_translate',
    'subtitle_removal','translate_lab','image_translate',
    'multi_translate'
  ) NOT NULL DEFAULT 'translation';

-- ========== 2. llm_prompt_configs 表 ==========
CREATE TABLE llm_prompt_configs (
  id              INT AUTO_INCREMENT PRIMARY KEY,
  slot            VARCHAR(64) NOT NULL COMMENT 'base_translation|base_tts_script|base_rewrite|ecommerce_plugin',
  lang            VARCHAR(8)  NULL     COMMENT 'de/fr/es/it/ja/pt；ecommerce_plugin 用 NULL 共享',
  model_provider  VARCHAR(32) NOT NULL COMMENT 'openrouter|doubao|openai|anthropic',
  model_name      VARCHAR(128) NOT NULL,
  content         MEDIUMTEXT NOT NULL,
  enabled         TINYINT DEFAULT 1,
  updated_at      DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  updated_by      INT NULL,
  UNIQUE KEY uk_slot_lang (slot, lang)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
