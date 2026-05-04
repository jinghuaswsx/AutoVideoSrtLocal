CREATE TABLE IF NOT EXISTS users (
    id           INT AUTO_INCREMENT PRIMARY KEY,
    username     VARCHAR(64) NOT NULL UNIQUE,
    password_hash VARCHAR(255) NOT NULL,
    role         VARCHAR(16) NOT NULL DEFAULT 'user',
    permissions  JSON DEFAULT NULL,
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

-- ElevenLabs target-language variants (same voice_id can support many languages)
CREATE TABLE IF NOT EXISTS `elevenlabs_voice_variants` (
  `id` BIGINT AUTO_INCREMENT PRIMARY KEY,
  `voice_id` VARCHAR(64) NOT NULL,
  `name` VARCHAR(255) NOT NULL,
  `gender` VARCHAR(32) DEFAULT NULL,
  `age` VARCHAR(32) DEFAULT NULL,
  `language` VARCHAR(32) NOT NULL,
  `accent` VARCHAR(64) DEFAULT NULL,
  `category` VARCHAR(64) DEFAULT NULL,
  `descriptive` VARCHAR(255) DEFAULT NULL,
  `use_case` VARCHAR(128) DEFAULT NULL,
  `preview_url` TEXT DEFAULT NULL,
  `audio_embedding` MEDIUMBLOB DEFAULT NULL,
  `labels_json` JSON DEFAULT NULL,
  `public_owner_id` VARCHAR(128) DEFAULT NULL,
  `synced_at` DATETIME NOT NULL,
  `updated_at` DATETIME NOT NULL,
  UNIQUE KEY `uq_voice_language` (`voice_id`, `language`),
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

-- LLM/API 供应商配置（2026-04-25 引入，取代 .env / api_keys 供应商凭据）
-- 每个功能入口一条独立 provider_code，管理员在 /settings 维护，全局生效。
CREATE TABLE IF NOT EXISTS llm_provider_configs (
    provider_code VARCHAR(64)  NOT NULL,
    display_name  VARCHAR(128) NOT NULL,
    group_code    VARCHAR(32)  NOT NULL DEFAULT 'llm',
    api_key       TEXT         NULL,
    base_url      VARCHAR(512) NULL,
    model_id      VARCHAR(160) NULL,
    extra_config  JSON         NULL,
    enabled       TINYINT(1)   NOT NULL DEFAULT 1,
    updated_by    BIGINT       NULL,
    created_at    TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at    TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (provider_code),
    KEY idx_llm_provider_group_code (group_code),
    KEY idx_llm_provider_enabled (enabled)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS runtime_active_tasks (
  task_key VARCHAR(191) NOT NULL PRIMARY KEY,
  project_type VARCHAR(64) NOT NULL,
  task_id VARCHAR(128) NOT NULL,
  user_id BIGINT NULL,
  runner VARCHAR(255) NOT NULL DEFAULT '',
  entrypoint VARCHAR(255) NOT NULL DEFAULT '',
  stage VARCHAR(255) NOT NULL DEFAULT '',
  thread_name VARCHAR(255) NOT NULL DEFAULT '',
  process_id INT NOT NULL DEFAULT 0,
  interrupt_policy VARCHAR(32) NOT NULL DEFAULT 'block_restart',
  started_at DATETIME NULL,
  last_heartbeat_at DATETIME NULL,
  details_json JSON NULL,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  KEY idx_runtime_active_tasks_heartbeat (last_heartbeat_at),
  KEY idx_runtime_active_tasks_type_task (project_type, task_id),
  KEY idx_runtime_active_tasks_policy (interrupt_policy)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS runtime_active_task_snapshots (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
  snapshot_reason VARCHAR(64) NOT NULL,
  project_type VARCHAR(64) NOT NULL,
  task_id VARCHAR(128) NOT NULL,
  user_id BIGINT NULL,
  runner VARCHAR(255) NOT NULL DEFAULT '',
  entrypoint VARCHAR(255) NOT NULL DEFAULT '',
  stage VARCHAR(255) NOT NULL DEFAULT '',
  thread_name VARCHAR(255) NOT NULL DEFAULT '',
  process_id INT NOT NULL DEFAULT 0,
  interrupt_policy VARCHAR(32) NOT NULL DEFAULT 'block_restart',
  started_at DATETIME NULL,
  last_heartbeat_at DATETIME NULL,
  captured_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  details_json JSON NULL,
  KEY idx_runtime_active_task_snapshots_captured_at (captured_at),
  KEY idx_runtime_active_task_snapshots_task (project_type, task_id),
  KEY idx_runtime_active_task_snapshots_reason (snapshot_reason)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS system_audit_logs (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  actor_user_id INT NULL,
  actor_username VARCHAR(64) NULL,
  action VARCHAR(64) NOT NULL,
  module VARCHAR(64) NOT NULL,
  target_type VARCHAR(64) NULL,
  target_id VARCHAR(64) NULL,
  target_label VARCHAR(255) NULL,
  status VARCHAR(16) NOT NULL DEFAULT 'success',
  request_method VARCHAR(8) NULL,
  request_path VARCHAR(512) NULL,
  ip_address VARCHAR(64) NULL,
  user_agent VARCHAR(512) NULL,
  detail_json JSON NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  KEY idx_created_at (created_at),
  KEY idx_actor_created (actor_user_id, created_at),
  KEY idx_action_created (action, created_at),
  KEY idx_module_created (module, created_at),
  KEY idx_target (target_type, target_id, created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- TTS 变速短路收敛 AI 评估记录
-- 每次 ElevenLabs 变速短路（duration loop ±10% 分支）跑一行；
-- 同 task_id + round_index 唯一，重新评估只更新该行。
CREATE TABLE IF NOT EXISTS tts_speedup_evaluations (
  id                  BIGINT AUTO_INCREMENT PRIMARY KEY,
  task_id             VARCHAR(64) NOT NULL,
  round_index         INT NOT NULL,
  language            VARCHAR(16) NOT NULL,
  video_duration      DECIMAL(10,3) NOT NULL,
  audio_pre_duration  DECIMAL(10,3) NOT NULL,
  audio_post_duration DECIMAL(10,3) NOT NULL,
  speed_ratio         DECIMAL(6,4) NOT NULL,
  hit_final_range     TINYINT(1) NOT NULL,

  -- AI 五维评分（评估失败时为 NULL）
  score_naturalness     TINYINT,
  score_pacing          TINYINT,
  score_timbre          TINYINT,
  score_intelligibility TINYINT,
  score_overall         TINYINT,
  summary_text          TEXT,
  flags_json            JSON,

  -- 模型信息 + 计费
  model_provider     VARCHAR(64),
  model_id           VARCHAR(128),
  llm_input_tokens   INT,
  llm_output_tokens  INT,
  llm_cost_usd       DECIMAL(10,6),

  -- 评估状态
  status             VARCHAR(16) NOT NULL DEFAULT 'pending',  -- pending | ok | failed
  error_text         TEXT,

  -- 音频路径（task_dir 相对路径，UI 通过现有 artifact 路由读）
  audio_pre_path     VARCHAR(255) NOT NULL,
  audio_post_path    VARCHAR(255) NOT NULL,

  created_at   DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  evaluated_at DATETIME,

  UNIQUE KEY uk_task_round (task_id, round_index),
  KEY idx_created (created_at),
  KEY idx_lang_overall (language, score_overall),
  KEY idx_status (status, created_at)
);
