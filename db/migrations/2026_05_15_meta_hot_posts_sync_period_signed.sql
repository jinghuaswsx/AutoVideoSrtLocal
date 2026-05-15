-- Allow negative period-like deltas from the upstream Meta hot posts API.
-- Docs-anchor: docs/superpowers/specs/2026-05-15-meta-hot-posts-full-sync-design.md

ALTER TABLE meta_hot_posts
  MODIFY COLUMN sync_period_likes BIGINT NULL;
