-- db/migrations/2026_05_28_add_is_new_product_push_to_media_push_logs.sql
-- Add column to mark whether this push is the one that successfully matching and binding the product's Mingkong ID
ALTER TABLE media_push_logs ADD COLUMN is_new_product_push TINYINT NOT NULL DEFAULT 0;

-- Backfill existing successful push logs that bound the mk_id for pushed (non-imported) products
UPDATE media_push_logs
SET is_new_product_push = 1
WHERE id IN (
  SELECT MIN(l2.id)
  FROM media_push_logs l2
  JOIN media_items i2 ON i2.id = l2.item_id
  JOIN media_products p2 ON p2.id = i2.product_id
  WHERE l2.status = 'success'
    AND p2.mk_id IS NOT NULL
    AND p2.id NOT IN (
      SELECT DISTINCT i3.product_id
      FROM media_item_mk_bindings b
      JOIN media_items i3 ON i3.id = b.media_item_id
    )
  GROUP BY i2.product_id
);
