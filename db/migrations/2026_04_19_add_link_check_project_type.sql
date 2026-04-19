ALTER TABLE projects
  MODIFY COLUMN type ENUM(
    'translation','de_translate','fr_translate','copywriting',
    'video_creation','video_review','text_translate','subtitle_removal',
    'translate_lab','image_translate','multi_translate',
    'bulk_translate','copywriting_translate','link_check'
  ) NOT NULL DEFAULT 'translation';
