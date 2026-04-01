CREATE TABLE IF NOT EXISTS user_voices (
    id INT AUTO_INCREMENT PRIMARY KEY,
    user_id INT NOT NULL,
    name VARCHAR(100) NOT NULL,
    gender ENUM('male','female') NOT NULL,
    elevenlabs_voice_id VARCHAR(50) NOT NULL,
    description TEXT,
    style_tags JSON DEFAULT NULL,
    preview_url VARCHAR(500) DEFAULT '',
    source VARCHAR(50) DEFAULT 'manual',
    labels JSON DEFAULT NULL,
    is_default BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uq_user_voice (user_id, elevenlabs_voice_id)
);

CREATE TABLE IF NOT EXISTS user_prompts (
    id INT AUTO_INCREMENT PRIMARY KEY,
    user_id INT NOT NULL,
    name VARCHAR(100) NOT NULL,
    prompt_text TEXT NOT NULL,
    is_default BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
);
