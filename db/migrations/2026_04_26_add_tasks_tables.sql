-- db/migrations/2026_04_26_add_tasks_tables.sql
-- 任务中心骨架（C 子系统）
-- - tasks: 双层任务模型（父=素材级 / 子=国家级），单表 + parent_task_id 区分
-- - task_events: 审计流，未来 F 子系统的统计基础
-- 详见 docs/superpowers/specs/2026-04-26-task-center-skeleton-design.md

CREATE TABLE IF NOT EXISTS tasks (
  id               INT AUTO_INCREMENT PRIMARY KEY,
  parent_task_id   INT DEFAULT NULL,
  media_product_id INT NOT NULL,
  media_item_id    INT DEFAULT NULL,
  country_code     VARCHAR(8) DEFAULT NULL,
  assignee_id      INT DEFAULT NULL,
  status           VARCHAR(24) NOT NULL,
  last_reason      TEXT DEFAULT NULL,
  created_by       INT NOT NULL,
  created_at       DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at       DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  claimed_at       DATETIME DEFAULT NULL,
  completed_at     DATETIME DEFAULT NULL,
  cancelled_at     DATETIME DEFAULT NULL,
  KEY idx_parent (parent_task_id),
  KEY idx_product (media_product_id),
  KEY idx_assignee_status (assignee_id, status),
  KEY idx_status_parent (status, parent_task_id),
  UNIQUE KEY uk_parent_country (parent_task_id, country_code)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS task_events (
  id            BIGINT AUTO_INCREMENT PRIMARY KEY,
  task_id       INT NOT NULL,
  event_type    VARCHAR(32) NOT NULL,
  actor_user_id INT DEFAULT NULL,
  payload_json  JSON DEFAULT NULL,
  created_at    DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  KEY idx_task (task_id, created_at),
  KEY idx_actor (actor_user_id, created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
