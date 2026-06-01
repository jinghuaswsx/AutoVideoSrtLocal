-- Docs-anchor: docs/superpowers/specs/2026-05-28-dialogue-video-translation-design.md
ALTER TABLE projects
  MODIFY COLUMN type ENUM(
    'translation','de_translate','fr_translate','copywriting',
    'video_creation','video_review','text_translate','subtitle_removal',
    'translate_lab','image_translate','multi_translate',
    'bulk_translate','copywriting_translate','link_check','ja_translate',
    'omni_translate','video_cover','english_redub','task_creator',
    'omni_translate_v2','dialogue_translate'
  ) NOT NULL DEFAULT 'translation';
