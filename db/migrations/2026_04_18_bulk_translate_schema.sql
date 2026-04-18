-- 2026-04-18 bulk translate 设计迁移
-- 设计文档: docs/superpowers/specs/2026-04-18-bulk-translate-design.md
--
-- 1) projects.type 扩展两个枚举值 bulk_translate / copywriting_translate
-- 2) 四张素材表增加关联追踪字段
-- 3) 新增 media_video_translate_profiles 表

-- ========== 1. projects 表 type 扩展 ==========
ALTER TABLE projects
  MODIFY COLUMN type ENUM(
    'translation','de_translate','fr_translate','copywriting',
    'video_creation','video_review','translate_lab',
    'image_translate','subtitle_removal',
    'bulk_translate','copywriting_translate'
  ) NOT NULL;

-- ========== 2. 四张素材表加关联追踪字段 ==========
ALTER TABLE media_copywritings
  ADD COLUMN source_ref_id      VARCHAR(64)  NULL        COMMENT '指向源英文条目 id',
  ADD COLUMN bulk_task_id       VARCHAR(64)  NULL        COMMENT '指向父任务 projects.id',
  ADD COLUMN auto_translated    TINYINT(1)   NOT NULL DEFAULT 0,
  ADD COLUMN manually_edited_at TIMESTAMP    NULL DEFAULT NULL
                                              COMMENT '用户手工修改自动翻译结果的时间',
  ADD INDEX idx_cw_source_ref  (source_ref_id),
  ADD INDEX idx_cw_bulk_task   (bulk_task_id);

ALTER TABLE media_product_detail_images
  ADD COLUMN source_ref_id      VARCHAR(64)  NULL,
  ADD COLUMN bulk_task_id       VARCHAR(64)  NULL,
  ADD COLUMN auto_translated    TINYINT(1)   NOT NULL DEFAULT 0,
  ADD COLUMN manually_edited_at TIMESTAMP    NULL DEFAULT NULL,
  ADD INDEX idx_detail_source_ref (source_ref_id),
  ADD INDEX idx_detail_bulk_task  (bulk_task_id);

ALTER TABLE media_items
  ADD COLUMN source_ref_id      VARCHAR(64)  NULL,
  ADD COLUMN bulk_task_id       VARCHAR(64)  NULL,
  ADD COLUMN auto_translated    TINYINT(1)   NOT NULL DEFAULT 0,
  ADD COLUMN manually_edited_at TIMESTAMP    NULL DEFAULT NULL,
  ADD INDEX idx_item_source_ref (source_ref_id),
  ADD INDEX idx_item_bulk_task  (bulk_task_id);

ALTER TABLE media_product_covers
  ADD COLUMN source_ref_id      VARCHAR(64)  NULL,
  ADD COLUMN bulk_task_id       VARCHAR(64)  NULL,
  ADD COLUMN auto_translated    TINYINT(1)   NOT NULL DEFAULT 0,
  ADD COLUMN manually_edited_at TIMESTAMP    NULL DEFAULT NULL,
  ADD INDEX idx_cover_source_ref (source_ref_id),
  ADD INDEX idx_cover_bulk_task  (bulk_task_id);

-- ========== 3. 视频翻译参数持久化表 ==========
CREATE TABLE IF NOT EXISTS media_video_translate_profiles (
  id           BIGINT PRIMARY KEY AUTO_INCREMENT,
  user_id      VARCHAR(64) NOT NULL,
  product_id   VARCHAR(64) NULL COMMENT 'NULL = 用户级默认',
  lang         VARCHAR(8)  NULL COMMENT 'NULL = 产品级全语言默认',
  params_json  JSON        NOT NULL,
  created_at   TIMESTAMP   NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at   TIMESTAMP   NOT NULL DEFAULT CURRENT_TIMESTAMP
                                    ON UPDATE CURRENT_TIMESTAMP,
  UNIQUE KEY uk_scope (user_id, product_id, lang),
  INDEX idx_user (user_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='视频翻译 12 项参数三层持久化(user/product/lang)';
