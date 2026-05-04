-- TTS 变速短路收敛 AI 评估记录
-- 每次 ElevenLabs 变速短路（duration loop ±10% 分支）跑一行；
-- 同 task_id + round_index 唯一，重新评估只更新该行。
CREATE TABLE IF NOT EXISTS tts_speedup_evaluations (
  id                  BIGINT AUTO_INCREMENT PRIMARY KEY,
  task_id             VARCHAR(64) NOT NULL,
  round_index         INT NOT NULL,
  language            VARCHAR(16) NOT NULL,
  video_duration      DECIMAL(10,3) NOT NULL,
  audio_pre_duration  DECIMAL(10,3) NOT NULL,
  audio_post_duration DECIMAL(10,3) NOT NULL,
  speed_ratio         DECIMAL(6,4) NOT NULL,
  hit_final_range     TINYINT(1) NOT NULL,

  -- AI 五维评分（评估失败时为 NULL）
  score_naturalness     TINYINT,
  score_pacing          TINYINT,
  score_timbre          TINYINT,
  score_intelligibility TINYINT,
  score_overall         TINYINT,
  summary_text          TEXT,
  flags_json            JSON,

  -- 模型信息 + 计费
  model_provider     VARCHAR(64),
  model_id           VARCHAR(128),
  llm_input_tokens   INT,
  llm_output_tokens  INT,
  llm_cost_usd       DECIMAL(10,6),

  -- 评估状态
  status             VARCHAR(16) NOT NULL DEFAULT 'pending',  -- pending | ok | failed
  error_text         TEXT,

  -- 音频路径（task_dir 相对路径，UI 通过现有 artifact 路由读）
  audio_pre_path     VARCHAR(255) NOT NULL,
  audio_post_path    VARCHAR(255) NOT NULL,

  created_at   DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  evaluated_at DATETIME,

  UNIQUE KEY uk_task_round (task_id, round_index),
  KEY idx_created (created_at),
  KEY idx_lang_overall (language, score_overall),
  KEY idx_status (status, created_at)
);
