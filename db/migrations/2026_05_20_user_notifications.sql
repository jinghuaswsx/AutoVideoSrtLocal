-- db/migrations/2026_05_20_user_notifications.sql
-- Per-user in-app notifications. First producer: task center.
-- Design: docs/superpowers/specs/2026-05-20-task-message-center-design.md

CREATE TABLE IF NOT EXISTS user_notifications (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  user_id INT NOT NULL,
  source_type VARCHAR(32) NOT NULL,
  source_id INT NOT NULL,
  event_type VARCHAR(48) NOT NULL,
  title VARCHAR(120) NOT NULL,
  body VARCHAR(512) DEFAULT NULL,
  target_url VARCHAR(255) NOT NULL,
  read_at DATETIME DEFAULT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  KEY idx_user_read_created (user_id, read_at, created_at),
  KEY idx_source (source_type, source_id, event_type)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
