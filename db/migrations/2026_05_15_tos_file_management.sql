-- 2026-05-15 TOS 文件管理表
--
-- 目标：
--   1) 提供受保护业务文件的清单、统计和同步控制
--   2) 记录扫描运行历史和文件映射状态
--   3) 支持手动触发 WJ 通道同步/干运行

-- 扫描运行表：每次扫描一行
CREATE TABLE IF NOT EXISTS tos_file_scan_runs (
  id                    BIGINT       NOT NULL AUTO_INCREMENT,
  status                VARCHAR(32)  NOT NULL DEFAULT 'running',
  target_channel_code   VARCHAR(64)  NOT NULL DEFAULT 'tos_wj',
  target_bucket         VARCHAR(255) NOT NULL DEFAULT '',
  total_files           INT          NOT NULL DEFAULT 0,
  total_bytes           BIGINT       NOT NULL DEFAULT 0,
  local_missing_count   INT          NOT NULL DEFAULT 0,
  target_missing_count  INT          NOT NULL DEFAULT 0,
  failed_count          INT          NOT NULL DEFAULT 0,
  module_summary_json   JSON         NULL,
  error_message         TEXT         NULL,
  triggered_by          BIGINT       NULL,
  started_at            TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
  finished_at           TIMESTAMP    NULL,
  created_at            TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  KEY idx_tos_file_scan_runs_started_at (started_at),
  KEY idx_tos_file_scan_runs_status (status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 文件映射表：每个本地文件每个目标通道一行
CREATE TABLE IF NOT EXISTS tos_file_mappings (
  id                    BIGINT       NOT NULL AUTO_INCREMENT,
  scan_run_id           BIGINT       NULL,
  module_code           VARCHAR(64)  NOT NULL,
  module_name           VARCHAR(128) NOT NULL,
  file_type             VARCHAR(64)  NOT NULL,
  source_labels_json    JSON         NULL,
  source_object_keys_json JSON       NULL,
  local_path            TEXT         NOT NULL,
  local_path_hash       CHAR(64)     NOT NULL,
  local_exists          TINYINT(1)   NOT NULL DEFAULT 0,
  local_size_bytes      BIGINT       NOT NULL DEFAULT 0,
  backup_object_key     TEXT         NOT NULL,
  target_channel_code   VARCHAR(64)  NOT NULL,
  target_bucket         VARCHAR(255) NOT NULL DEFAULT '',
  target_object_key     TEXT         NOT NULL,
  target_exists         TINYINT(1)   NOT NULL DEFAULT 0,
  target_size_bytes     BIGINT       NOT NULL DEFAULT 0,
  sync_status           VARCHAR(32)  NOT NULL DEFAULT 'unknown',
  last_error            TEXT         NULL,
  last_seen_at          TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
  last_synced_at        TIMESTAMP    NULL,
  created_at            TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at            TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  UNIQUE KEY uniq_tos_file_mapping_channel_path (target_channel_code, local_path_hash),
  KEY idx_tos_file_mappings_module (module_code),
  KEY idx_tos_file_mappings_status (sync_status),
  KEY idx_tos_file_mappings_scan (scan_run_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 同步运行表：每次同步/干运行一行
CREATE TABLE IF NOT EXISTS tos_file_sync_runs (
  id                    BIGINT       NOT NULL AUTO_INCREMENT,
  scan_run_id           BIGINT       NULL,
  target_channel_code   VARCHAR(64)  NOT NULL DEFAULT 'tos_wj',
  target_bucket         VARCHAR(255) NOT NULL DEFAULT '',
  module_code           VARCHAR(64)  NULL,
  dry_run               TINYINT(1)   NOT NULL DEFAULT 1,
  status                VARCHAR(32)  NOT NULL DEFAULT 'running',
  files_checked         INT          NOT NULL DEFAULT 0,
  uploaded_count        INT          NOT NULL DEFAULT 0,
  skipped_existing_count INT         NOT NULL DEFAULT 0,
  failed_count          INT          NOT NULL DEFAULT 0,
  bytes_uploaded        BIGINT       NOT NULL DEFAULT 0,
  summary_json          JSON         NULL,
  error_message         TEXT         NULL,
  triggered_by          BIGINT       NULL,
  started_at            TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
  finished_at           TIMESTAMP    NULL,
  created_at            TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  KEY idx_tos_file_sync_runs_started_at (started_at),
  KEY idx_tos_file_sync_runs_status (status),
  KEY idx_tos_file_sync_runs_channel (target_channel_code)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
