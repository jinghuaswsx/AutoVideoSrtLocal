-- User-scoped favorites for Meta hot-post cards.
-- Docs-anchor: docs/superpowers/specs/2026-05-19-meta-hot-posts-user-favorites-design.md

CREATE TABLE IF NOT EXISTS meta_hot_post_favorites (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
  user_id INT NOT NULL,
  hot_post_id BIGINT UNSIGNED NOT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE KEY uq_meta_hot_post_favorites_user_post (user_id, hot_post_id),
  KEY idx_meta_hot_post_favorites_user_created (user_id, created_at),
  KEY idx_meta_hot_post_favorites_post (hot_post_id),
  CONSTRAINT fk_meta_hot_post_favorites_user
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
  CONSTRAINT fk_meta_hot_post_favorites_post
    FOREIGN KEY (hot_post_id) REFERENCES meta_hot_posts(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
