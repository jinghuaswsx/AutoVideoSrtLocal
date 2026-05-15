-- Meta hot-post video analysis retry limits.
-- Docs-anchor: docs/superpowers/specs/2026-05-15-meta-hot-posts-unified-video-analysis-queue-design.md

ALTER TABLE meta_hot_post_video_copyability_analyses
  MODIFY status ENUM('pending', 'running', 'done', 'failed', 'suspended')
  NOT NULL DEFAULT 'pending';

ALTER TABLE meta_hot_post_europe_assessments
  MODIFY status ENUM('pending', 'running', 'done', 'failed', 'suspended')
  NOT NULL DEFAULT 'pending';

UPDATE meta_hot_post_video_copyability_analyses
SET
  status = 'suspended',
  last_error = COALESCE(last_error, 'video analysis suspended after 3 failed attempts')
WHERE status = 'failed'
  AND attempts >= 3
  AND local_video_path IS NOT NULL
  AND TRIM(local_video_path) <> '';

UPDATE meta_hot_post_europe_assessments e
JOIN meta_hot_posts p ON p.id = e.post_id
SET
  e.status = 'suspended',
  e.last_error = COALESCE(e.last_error, 'Europe fit analysis suspended after 3 failed attempts')
WHERE e.status = 'failed'
  AND e.attempts >= 3
  AND p.local_video_status = 'downloaded'
  AND p.local_video_path IS NOT NULL
  AND TRIM(p.local_video_path) <> '';
