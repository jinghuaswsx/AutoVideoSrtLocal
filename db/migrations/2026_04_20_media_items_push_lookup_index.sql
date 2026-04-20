-- 推送端按 (product_id, lang, filename) 三元组精确定位素材，
-- 补一个覆盖 filename 的联合索引。
-- filename 是 VARCHAR(500)，在多列索引里用 191 前缀避免超出 InnoDB 单 key 字节上限。
ALTER TABLE media_items
  ADD KEY idx_product_lang_filename (product_id, lang, filename(191));
