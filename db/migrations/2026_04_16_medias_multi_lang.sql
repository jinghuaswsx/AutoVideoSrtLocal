-- db/migrations/2026_04_16_medias_multi_lang.sql
-- Medias 多语种管理：加 lang 维度、语种配置表、产品主图按语种分表

-- 1. 语种配置表
CREATE TABLE IF NOT EXISTS media_languages (
  code       VARCHAR(8)  PRIMARY KEY,
  name_zh    VARCHAR(32) NOT NULL,
  sort_order INT         NOT NULL DEFAULT 0,
  enabled    TINYINT(1)  NOT NULL DEFAULT 1
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

INSERT IGNORE INTO media_languages (code, name_zh, sort_order) VALUES
  ('en', '英语', 1),
  ('de', '德语', 2),
  ('fr', '法语', 3),
  ('es', '西班牙语', 4),
  ('it', '意大利语', 5),
  ('ja', '日语', 6),
  ('pt', '葡萄牙语', 7);

-- 2. 素材加 lang
ALTER TABLE media_items
  ADD COLUMN lang VARCHAR(8) NOT NULL DEFAULT 'en' AFTER product_id,
  ADD KEY idx_product_lang (product_id, lang, deleted_at);

-- 3. 文案加 lang
ALTER TABLE media_copywritings
  ADD COLUMN lang VARCHAR(8) NOT NULL DEFAULT 'en' AFTER product_id,
  ADD KEY idx_product_lang (product_id, lang, idx);

-- 4. 产品主图按语种分表
CREATE TABLE IF NOT EXISTS media_product_covers (
  product_id INT          NOT NULL,
  lang       VARCHAR(8)   NOT NULL,
  object_key VARCHAR(255) NOT NULL,
  updated_at DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP
                                    ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (product_id, lang)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 5. 回填：已有产品主图作为英文主图
INSERT IGNORE INTO media_product_covers (product_id, lang, object_key)
SELECT id, 'en', cover_object_key
FROM media_products
WHERE cover_object_key IS NOT NULL AND deleted_at IS NULL;
