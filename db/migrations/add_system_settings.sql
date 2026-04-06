-- db/migrations/add_system_settings.sql
CREATE TABLE IF NOT EXISTS system_settings (
    `key`      VARCHAR(100) PRIMARY KEY,
    `value`    TEXT NOT NULL,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
);

INSERT IGNORE INTO system_settings (`key`, `value`) VALUES ('retention_default_hours', '168');

ALTER TABLE projects MODIFY COLUMN expires_at DATETIME NULL;
