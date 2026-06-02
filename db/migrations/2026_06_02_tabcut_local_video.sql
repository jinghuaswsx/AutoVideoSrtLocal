-- Add local video columns to tabcut_videos for local download & playback
ALTER TABLE tabcut_videos
ADD COLUMN local_video_path VARCHAR(1024) NULL,
ADD COLUMN local_video_duration_seconds FLOAT NULL,
ADD COLUMN local_video_cover_path VARCHAR(1024) NULL,
ADD COLUMN local_video_status VARCHAR(32) NOT NULL DEFAULT 'pending',
ADD COLUMN local_video_attempts INT NOT NULL DEFAULT 0,
ADD COLUMN local_video_last_attempt_at DATETIME NULL,
ADD COLUMN local_video_error VARCHAR(1024) NULL,
ADD KEY idx_tabcut_videos_local_status (local_video_status);
