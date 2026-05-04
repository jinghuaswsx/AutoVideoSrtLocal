-- AI 视频分析（手动触发，一键对源/译两端做多模态评估）
-- source_type 区分入口：'multi_translate_task' / 'media_item'
-- 同一 source 可有多次 run（按 run_id 自增），UI 默认显示最新
CREATE TABLE IF NOT EXISTS video_ai_reviews (
  id BIGINT PRIMARY KEY AUTO_INCREMENT,
  source_type VARCHAR(32) NOT NULL,
  source_id   VARCHAR(64) NOT NULL,
  run_id      INT NOT NULL,
  status      VARCHAR(16) NOT NULL DEFAULT 'pending',  -- pending|running|done|failed|cancelled
  channel     VARCHAR(64),                              -- e.g. gemini_vertex_adc
  model       VARCHAR(128),                             -- e.g. gemini-3.1-pro-preview
  triggered_by         VARCHAR(16) NOT NULL DEFAULT 'manual',  -- manual|auto
  triggered_by_user_id INT,

  -- 提交资料快照（用于 Modal 展示）
  submitted_inputs JSON,         -- 文案、语言、视频/音频路径、产品信息等
  prompt_text      MEDIUMTEXT,    -- 实际发给 LLM 的 system + user 摘要

  -- LLM 返回
  raw_response     JSON,
  overall_score    INT,           -- 综合分 0-100
  dimensions       JSON,          -- 各维度评分
  verdict          VARCHAR(32),   -- recommend|usable_with_minor_issues|needs_review|recommend_redo
  verdict_reason   TEXT,
  issues           JSON,          -- string[]
  highlights       JSON,          -- string[]

  -- 性能 & 时间
  request_duration_ms INT,
  started_at          DATETIME,
  completed_at        DATETIME,
  error_text          MEDIUMTEXT,
  created_at          DATETIME DEFAULT CURRENT_TIMESTAMP,

  KEY idx_source (source_type, source_id, run_id),
  KEY idx_status (status, created_at)
);
