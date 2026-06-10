-- Add mingkong_product_id column to mingkong_procurement_links table for product-level fuzzy pairing candidates
ALTER TABLE mingkong_procurement_links ADD COLUMN mingkong_product_id BIGINT DEFAULT NULL AFTER id;
ALTER TABLE mingkong_procurement_links ADD KEY idx_mk_proc_product (mingkong_product_id);
