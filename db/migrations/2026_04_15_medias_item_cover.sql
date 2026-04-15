-- db/migrations/2026_04_15_medias_item_cover.sql
ALTER TABLE media_items
  ADD COLUMN cover_object_key VARCHAR(255) NULL AFTER thumbnail_path;
