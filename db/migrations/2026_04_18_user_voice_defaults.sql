-- 2026-04-18 用户×语言 默认 TTS 音色
-- 让用户在多语种视频翻译里点"设为默认"，下次自动置顶可选
-- 未设置时 fallback 到 appcore.video_translate_defaults.TTS_VOICE_DEFAULTS

CREATE TABLE IF NOT EXISTS user_voice_defaults (
  user_id    INT         NOT NULL,
  lang       VARCHAR(8)  NOT NULL COMMENT 'de/fr/es/it/ja/pt（以后可扩）',
  voice_id   VARCHAR(64) NOT NULL COMMENT 'elevenlabs_voices.voice_id',
  voice_name VARCHAR(128) NULL    COMMENT '展示名，UI 回显用',
  updated_at DATETIME    DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (user_id, lang)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
