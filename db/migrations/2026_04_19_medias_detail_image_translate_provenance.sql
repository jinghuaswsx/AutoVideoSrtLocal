ALTER TABLE media_product_detail_images
  ADD COLUMN origin_type VARCHAR(32) NOT NULL DEFAULT 'manual' COMMENT 'manual|from_url|image_translate',
  ADD COLUMN source_detail_image_id INT NULL COMMENT '若来自英文详情图翻译，则记录源详情图 id',
  ADD COLUMN image_translate_task_id VARCHAR(64) NULL COMMENT '若来自图片翻译任务，则记录任务 id',
  ADD KEY idx_detail_image_origin_task (image_translate_task_id),
  ADD KEY idx_detail_image_source (source_detail_image_id);
