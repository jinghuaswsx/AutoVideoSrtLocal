-- 多语种翻译人声分离 + 响度匹配的总配置（system_settings 表）
--
-- 上线初期 enabled=0 是默认行为：除非管理员显式打开总开关，
-- 否则 runtime 走旧逻辑（不调分离 API、不做响度匹配、不混背景音）。
--
-- 任务运行时的分离结果不需要单独建表 —— task["separation"] 这个 JSON 字段
-- 跟着 projects.state_json 一起持久化（详见 appcore/task_state.py 的
-- _sync_task_to_db），不需要列扩展。
INSERT IGNORE INTO system_settings (`key`, `value`) VALUES
  ('audio_separation_enabled',          '0'),
  ('audio_separation_api_url',          ''),
  ('audio_separation_preset',           'vocal_balanced'),
  ('audio_separation_task_timeout',     '300'),
  ('audio_separation_background_volume','0.8');
