-- omni_translate experimental task plugin_config snapshot (Phase 2).
--
-- 每个 omni 任务创建时把当时勾选的 8 个能力点配置展开存进这个字段。
-- resume / 重跑全部读快照，不回查 preset（preset 改了不影响已有任务）。
-- 老 omni 任务 / 其他类型任务这一列为 NULL，runtime 读不到时回退到全站
-- 默认 preset。

ALTER TABLE projects
  ADD COLUMN plugin_config JSON NULL
  COMMENT 'omni_translate 任务的能力点配置快照（spec §4.4 schema）';
