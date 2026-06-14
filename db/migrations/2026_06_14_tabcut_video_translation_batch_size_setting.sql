-- Runtime batch size for Tabcut video translation scheduler.
-- Docs-anchor: docs/superpowers/specs/2026-06-14-tabcut-video-translation-task-design.md

INSERT IGNORE INTO system_settings (`key`, `value`) VALUES
  ('tabcut_video_translation_batch_size', '250');
