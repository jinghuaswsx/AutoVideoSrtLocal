CREATE TABLE IF NOT EXISTS tts_generation_stats (
    task_id          VARCHAR(64)  NOT NULL PRIMARY KEY,
    project_type     VARCHAR(32)  NOT NULL,
    target_lang      VARCHAR(8)   NOT NULL,
    user_id          INT          NULL,
    translate_calls  INT          NOT NULL,
    audio_calls      INT          NOT NULL,
    finished_at      DATETIME     NOT NULL,
    INDEX idx_user_time (user_id, finished_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
