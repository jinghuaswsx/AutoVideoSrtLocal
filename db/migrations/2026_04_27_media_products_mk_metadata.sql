-- 2026-04-27 — A subsystem: 加 mk-import 需要的产品维度字段
ALTER TABLE media_products
  ADD COLUMN product_link VARCHAR(2048) DEFAULT NULL,
  ADD COLUMN main_image VARCHAR(2048) DEFAULT NULL;
