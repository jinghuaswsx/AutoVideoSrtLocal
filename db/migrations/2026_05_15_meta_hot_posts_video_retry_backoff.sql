-- Meta hot post video download retry backoff terminal state.
-- Failed downloads retry at most 5 attempts, with at least 12 hours between retries.
-- Existing rows already exhausted by earlier logic are moved to the terminal unavailable status.

UPDATE meta_hot_posts
SET local_video_status = 'unavailable',
    local_video_error = CONCAT('unavailable after max retry attempts: ', COALESCE(local_video_error, 'download failed'))
WHERE local_video_status = 'failed'
  AND local_video_attempts >= 5;
