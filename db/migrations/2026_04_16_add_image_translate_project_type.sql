ALTER TABLE projects
  MODIFY COLUMN type ENUM(
    'translation','copywriting','video_creation','video_review',
    'text_translate','de_translate','fr_translate',
    'subtitle_removal','translate_lab','image_translate'
  ) NOT NULL DEFAULT 'translation';
