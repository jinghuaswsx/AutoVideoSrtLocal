ALTER TABLE projects
MODIFY COLUMN type ENUM(
    'translation',
    'copywriting',
    'video_creation',
    'video_review',
    'text_translate',
    'de_translate',
    'fr_translate',
    'subtitle_removal'
) NOT NULL DEFAULT 'translation';
