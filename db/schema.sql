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
    type             ENUM('translation','copywriting','video_creation','video_review','text_translate','de_translate','fr_translate') NOT NULL DEFAULT 'translation',
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
    model_name             VARCHAR(128),
    called_at              DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    success                TINYINT(1) NOT NULL DEFAULT 1,
    input_tokens           INT,
    output_tokens          INT,
    audio_duration_seconds FLOAT,
    extra_data             JSON,
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
