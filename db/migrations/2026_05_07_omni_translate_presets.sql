-- omni_translate experimental preset system (Phase 1).
--
-- Two-tier preset model:
--   * scope='system' presets — admin-maintained, all users see read-only
--   * scope='user'   presets — per-user private
--
-- Plugin config schema lives in appcore/omni_plugin_config.py; the column
-- here just stores opaque JSON so future capability additions don't need
-- another migration.
--
-- The "global default" preset id is recorded in system_settings under the
-- key `omni_translate.default_preset_id` (no foreign key — soft reference;
-- if the referenced preset is deleted the setting falls back at runtime).

CREATE TABLE IF NOT EXISTS omni_translate_presets (
  id              BIGINT PRIMARY KEY AUTO_INCREMENT,
  scope           ENUM('system','user') NOT NULL,
  user_id         INT NULL,
  name            VARCHAR(64) NOT NULL,
  description     VARCHAR(255) NULL,
  plugin_config   JSON NOT NULL,
  created_at      DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at      DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  INDEX idx_scope_user (scope, user_id),
  CONSTRAINT chk_omni_preset_user_scope CHECK (
    (scope = 'system' AND user_id IS NULL) OR
    (scope = 'user' AND user_id IS NOT NULL)
  )
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- Seed 4 baseline system presets (matches design doc §3 acceptance table).
INSERT IGNORE INTO omni_translate_presets (scope, user_id, name, description, plugin_config) VALUES
  ('system', NULL, 'multi-like',       '复刻 multi_translate 行为：英文标准化 + 标准翻译 + 5 轮 rewrite + ASR 对齐字幕',
    JSON_OBJECT(
      'asr_post', 'asr_normalize',
      'shot_decompose', false,
      'translate_algo', 'standard',
      'source_anchored', false,
      'tts_strategy', 'five_round_rewrite',
      'subtitle', 'asr_realign',
      'voice_separation', true,
      'loudness_match', true
    )),
  ('system', NULL, 'omni-current',     'omni 当前默认：同语言 ASR 纯净化 + source-anchored prompt + 5 轮 rewrite + ASR 对齐字幕',
    JSON_OBJECT(
      'asr_post', 'asr_clean',
      'shot_decompose', false,
      'translate_algo', 'standard',
      'source_anchored', true,
      'tts_strategy', 'five_round_rewrite',
      'subtitle', 'asr_realign',
      'voice_separation', true,
      'loudness_match', true
    )),
  ('system', NULL, 'av-sync-current',  '复刻 sentence_translate：英文标准化 + 句级 av_translate（shot_notes 驱动）+ 句级 reconcile + 句级字幕',
    JSON_OBJECT(
      'asr_post', 'asr_normalize',
      'shot_decompose', false,
      'translate_algo', 'av_sentence',
      'source_anchored', false,
      'tts_strategy', 'sentence_reconcile',
      'subtitle', 'sentence_units',
      'voice_separation', true,
      'loudness_match', true
    )),
  ('system', NULL, 'lab-current',      '复刻 translate_lab：英文标准化 + 镜头分镜 + 按镜头字符上限翻译 + 5 轮 rewrite + ASR 对齐字幕',
    JSON_OBJECT(
      'asr_post', 'asr_normalize',
      'shot_decompose', true,
      'translate_algo', 'shot_char_limit',
      'source_anchored', false,
      'tts_strategy', 'five_round_rewrite',
      'subtitle', 'asr_realign',
      'voice_separation', true,
      'loudness_match', true
    ));

-- Seed the global default to omni-current if no admin choice yet.
-- 用 SELECT 拿 id（不能在 INSERT 里直接子查询写当前事务刚 INSERT 的行 id），
-- 启动器执行该脚本时已经 commit 上面的 4 条 INSERT，子查询能命中。
INSERT IGNORE INTO system_settings (`key`, `value`)
SELECT 'omni_translate.default_preset_id', CAST(id AS CHAR)
  FROM omni_translate_presets
 WHERE scope = 'system' AND name = 'omni-current'
 LIMIT 1;
