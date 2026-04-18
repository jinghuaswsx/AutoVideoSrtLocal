-- 2026-04-19 每个产品按语言存储商品链接（可选覆盖默认模板）
-- 默认值由前端根据 product_code + lang 生成，保存用户手改的值

ALTER TABLE media_products
  ADD COLUMN localized_links_json JSON NULL COMMENT '按语言覆盖的商品链接 {lang: url}';
