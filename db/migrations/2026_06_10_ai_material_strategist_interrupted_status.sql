-- Allow AI素材军师 projects to stop at an explicit interrupted state.
-- Docs anchor: docs/superpowers/specs/2026-06-09-ai-material-strategist-project-design.md#断点续跑与恢复

ALTER TABLE ai_material_strategist_projects
  MODIFY status ENUM('running','success','failed','interrupted') NOT NULL DEFAULT 'running';
