CREATE DATABASE IF NOT EXISTS auto_video CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
USE auto_video;

CREATE TABLE IF NOT EXISTS users (
    id           INT AUTO_INCREMENT PRIMARY KEY,
    username     VARCHAR(64) NOT NULL UNIQUE,
    password_hash VARCHAR(255) NOT NULL,
    role         ENUM('admin','user') NOT NULL DEFAULT 'user',
    is_active    TINYINT(1) NOT NULL DEFAULT 1,
    created_at   DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS api_keys (
    id           INT AUTO_INCREMENT PRIMARY KEY,
    user_id      INT NOT NULL,
    service      VARCHAR(32) NOT NULL,
    key_value    VARCHAR(512) NOT NULL,
    extra_config JSON,
    updated_at   DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uq_user_service (user_id, service),
    FOREIGN KEY (user_id) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS projects (
    id               VARCHAR(36) PRIMARY KEY,
    user_id          INT NOT NULL,
    type             ENUM('translation','de_translate','fr_translate','copywriting','video_creation','video_review','text_translate','subtitle_removal','translate_lab','image_translate','multi_translate','bulk_translate','copywriting_translate','link_check','ja_translate') NOT NULL DEFAULT 'translation',
    original_filename VARCHAR(255),
    display_name     VARCHAR(255),
    thumbnail_path   VARCHAR(512),
    status           VARCHAR(32) NOT NULL DEFAULT 'uploaded',
    task_dir         VARCHAR(512),
    state_json       LONGTEXT,
    created_at       DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    expires_at       DATETIME,
    deleted_at       DATETIME,
    FOREIGN KEY (user_id) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS system_settings (
    `key`      VARCHAR(100) PRIMARY KEY,
    `value`    TEXT NOT NULL,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS usage_logs (
    id                     BIGINT AUTO_INCREMENT PRIMARY KEY,
    user_id                INT NOT NULL,
    project_id             VARCHAR(36),
    service                VARCHAR(32) NOT NULL,
    use_case_code          VARCHAR(64) DEFAULT NULL,
    module                 VARCHAR(32) DEFAULT NULL,
    provider               VARCHAR(32) DEFAULT NULL,
    model_name             VARCHAR(128),
    called_at              DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    success                TINYINT(1) NOT NULL DEFAULT 1,
    input_tokens           INT,
    output_tokens          INT,
    audio_duration_seconds FLOAT,
    request_units          INT,
    units_type             VARCHAR(16),
    cost_cny               DECIMAL(12,6),
    cost_source            ENUM('response','pricebook','unknown') NOT NULL DEFAULT 'unknown',
    extra_data             JSON,
    KEY idx_called_at (called_at),
    KEY idx_user_module (user_id, module, called_at),
    KEY idx_use_case (use_case_code, called_at),
    FOREIGN KEY (user_id) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS user_voices (
    id INT AUTO_INCREMENT PRIMARY KEY,
    user_id INT NOT NULL,
    name VARCHAR(100) NOT NULL,
    gender ENUM('male','female') NOT NULL,
    elevenlabs_voice_id VARCHAR(50) NOT NULL,
    language VARCHAR(10) NOT NULL DEFAULT 'en',
    description TEXT,
    style_tags JSON DEFAULT NULL,
    preview_url VARCHAR(500) DEFAULT '',
    source VARCHAR(50) DEFAULT 'manual',
    labels JSON DEFAULT NULL,
    is_default BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uq_user_voice (user_id, elevenlabs_voice_id, language)
);

CREATE TABLE IF NOT EXISTS user_prompts (
    id INT AUTO_INCREMENT PRIMARY KEY,
    user_id INT NOT NULL,
    type VARCHAR(20) NOT NULL DEFAULT 'translation',
    name VARCHAR(100) NOT NULL,
    prompt_text TEXT NOT NULL,
    prompt_text_zh TEXT,
    is_default BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS copywriting_inputs (
    id INT AUTO_INCREMENT PRIMARY KEY,
    project_id VARCHAR(36) NOT NULL,
    product_title VARCHAR(255) DEFAULT '',
    product_image_url TEXT,
    price VARCHAR(50) DEFAULT '',
    selling_points TEXT,
    target_audience VARCHAR(255) DEFAULT '',
    extra_info TEXT,
    language VARCHAR(10) NOT NULL DEFAULT 'en',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

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

CREATE TABLE IF NOT EXISTS ai_model_prices (
    id              INT AUTO_INCREMENT PRIMARY KEY,
    provider        VARCHAR(32) NOT NULL,
    model           VARCHAR(128) NOT NULL,
    units_type      VARCHAR(16) NOT NULL,
    unit_input_cny  DECIMAL(14,8) DEFAULT NULL,
    unit_output_cny DECIMAL(14,8) DEFAULT NULL,
    unit_flat_cny   DECIMAL(14,8) DEFAULT NULL,
    note            VARCHAR(255) DEFAULT NULL,
    updated_at      DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uniq_provider_model (provider, model)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
