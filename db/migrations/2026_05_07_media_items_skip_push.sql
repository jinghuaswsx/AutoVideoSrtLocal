-- 推送管理「标记不推送」功能。详见
-- docs/superpowers/specs/2026-05-07-pushes-skip-push-design.md
--
--   skip_push      0/1，1 表示该素材被人工标记为不推送
--   skip_push_at   标记时间，便于审计
--   skip_push_by   操作人 user_id（外键留作软引用，不约束）
--
-- 状态优先级在 Python 里实现：appcore.pushes.compute_status() 顶部
-- 直接 if item.skip_push: return STATUS_SKIPPED。
ALTER TABLE media_items
  ADD COLUMN skip_push TINYINT(1) NOT NULL DEFAULT 0,
  ADD COLUMN skip_push_at DATETIME DEFAULT NULL,
  ADD COLUMN skip_push_by INT DEFAULT NULL,
  ADD KEY idx_skip_push (skip_push);
