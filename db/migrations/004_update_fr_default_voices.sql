-- 更新所有用户的法语默认音色为新选定的 ElevenLabs 音色
-- 旧男声 Martin (D7dkYvH17OKLgp4SLulf) → 新男声 Simon (mvhJVdVoTWVUtL4keT7W)
-- 旧女声 Aida (QttbagfgqUCm9K0VgUyT) → 新女声 Vera (xjlfQQ3ynqiEyRpArrT8)

UPDATE user_voices
SET elevenlabs_voice_id = 'mvhJVdVoTWVUtL4keT7W',
    name = 'Simon',
    description = '法语男声，活力充沛，清晰有感染力，适合广告和播客',
    style_tags = '["cheerful", "energetic", "french"]',
    preview_url = 'https://storage.googleapis.com/eleven-public-prod/database/workspace/84b653bca1e04748a2525942f28ba2a0/voices/mvhJVdVoTWVUtL4keT7W/wLiqCsnrmbxkB32T3jr5.mp3'
WHERE elevenlabs_voice_id = 'D7dkYvH17OKLgp4SLulf'
  AND language = 'fr';

UPDATE user_voices
SET elevenlabs_voice_id = 'xjlfQQ3ynqiEyRpArrT8',
    name = 'Vera',
    description = '法语女声，年轻有活力，生动迷人，适合广告类视频',
    style_tags = '["energetic", "expressive", "french"]',
    preview_url = 'https://storage.googleapis.com/eleven-public-prod/database/workspace/b5a2ab0b95ef4475bf4c144ac8a3fb98/voices/xjlfQQ3ynqiEyRpArrT8/fb50230c-5d38-4fb6-9c8f-3db2aa5d7847.mp3'
WHERE elevenlabs_voice_id = 'QttbagfgqUCm9K0VgUyT'
  AND language = 'fr';
