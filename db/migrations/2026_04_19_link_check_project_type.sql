ALTER TABLE projects
  MODIFY COLUMN type ENUM(
    'translation','de_translate','fr_translate','copywriting',
    'video_creation','video_review','translate_lab',
    'image_translate','subtitle_removal',
    'bulk_translate','copywriting_translate',
    'multi_translate','link_check'
  ) NOT NULL DEFAULT 'translation';
