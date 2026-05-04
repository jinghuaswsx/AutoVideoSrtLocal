-- 2026-05-04 基础设施凭据数据库化（火山 TOS / VOD / TOS 灾备）
--
-- 目标：
--   1) 把 .env 里的 TOS_ACCESS_KEY / TOS_SECRET_KEY、TOS_BACKUP_*、VOD_*
--      迁移到数据库，以 DB 作为唯一可信源。
--   2) 启动时由 appcore.infra_credentials.sync_to_runtime() 一次性读 DB，
--      覆盖 config.XXX 模块属性 + os.environ，运行期间业务代码继续读
--      config.TOS_ACCESS_KEY 等，不增加任何 DB 查询负担。
--   3) admin 在 /settings?tab=infrastructure 保存后，同一函数再触发一次 sync，
--      并清掉 tos_clients / tos_backup_storage / vod_client 的进程级 client 缓存，
--      新值立即生效。
--
-- 与 llm_provider_configs 的区别：
--   - llm_provider_configs 是 LLM/API 供应商凭据（每次调用都现读 DB）
--   - 本表是基础设施凭据（启动时一次性读，写到 config + env）
--   - settings 页表单也独立：本表的字段在 UI 上明文展示（admin 自己运维）

CREATE TABLE IF NOT EXISTS infra_credentials (
  code         VARCHAR(64)  NOT NULL,
  display_name VARCHAR(128) NOT NULL,
  group_code   VARCHAR(32)  NOT NULL DEFAULT 'object_storage',
  config       JSON         NULL,
  enabled      TINYINT(1)   NOT NULL DEFAULT 1,
  updated_by   BIGINT       NULL,
  created_at   TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at   TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (code),
  KEY idx_infra_credentials_group (group_code)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 种子三行（幂等）：tos_main / tos_backup / vod_main
INSERT IGNORE INTO infra_credentials (code, display_name, group_code, config) VALUES
  ('tos_main',   '火山引擎 TOS · 主对象存储',  'object_storage', JSON_OBJECT()),
  ('tos_backup', '火山引擎 TOS · 灾备桶',      'object_storage', JSON_OBJECT()),
  ('vod_main',   '火山引擎 VOD · 视频点播',    'object_storage', JSON_OBJECT());
