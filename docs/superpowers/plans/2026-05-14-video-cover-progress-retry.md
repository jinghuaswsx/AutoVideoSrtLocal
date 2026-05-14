# 文案封面过程可视化与重试 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将文案封面生成从手动逐步执行改为自动串行执行、四卡片过程可视化、结构化结果展示、多图数量选择、失败重试、强制重开，并支持超级管理员维护全局默认模型配置。

**Architecture:** 复用现有 `video_cover` 项目类型和 `state_json`，新增后台串行 runner、状态轮询接口、步骤请求快照、结构化结果字段和项目级模型配置快照。全局默认模型配置保存到 `system_settings`，只暴露给超级管理员；新建项目时读取当前默认值写入 `state_json.model_defaults`，后续执行使用项目快照。

**Tech Stack:** Flask, Jinja2, vanilla JavaScript, pytest, existing `appcore.video_cover_generation`, `appcore.task_recovery`, `web.background`.

---

## Docs Anchor

- `docs/superpowers/specs/2026-05-14-video-cover-generation-design.md#1.1 项目工作流调整`
- `docs/superpowers/specs/2026-05-14-video-cover-generation-design.md#1.2 过程可视化与结构化结果`
- `docs/superpowers/specs/2026-05-14-video-cover-generation-design.md#1.3 全局默认模型配置`
- `web/templates/CLAUDE.md#CSRF / 路由守卫`
- `web/static/CLAUDE.md#Ocean Blue 设计系统`

## File Structure

- Modify `appcore/video_cover_generation.py`: add structured JSON parsing helpers and `image_count` support in `generate_video_covers`.
- Create `appcore/video_cover_settings.py`: own global default model config loading, validation, fallback and persistence through `system_settings`.
- Modify `web/routes/video_cover.py`: add image count parsing, background chain runner, retry/restart/state endpoints, request snapshots, structured step outputs, superadmin-only config APIs and project model default snapshots.
- Modify `web/templates/video_cover_list.html`: add image count capsule selector to create modal and superadmin-only default config button/modal.
- Modify `web/templates/video_cover_detail.html`: replace manual model controls with top progress, restart controls, four process cards, prompt modal, final left-image/right-copy result.
- Modify `tests/test_video_cover_generation.py`: route, service, template regression tests.

## Tasks

- [ ] Write failing tests for image count default, create form capsules, automatic background start, state endpoint, force restart and four-card detail layout.
- [ ] Run focused tests and confirm they fail against current manual-step implementation.
- [ ] Implement backend helpers: `normalize_image_count`, step request snapshots, structured result wrappers, background chain runner, `/state`, `/run/<step>`, `/restart`.
- [ ] Extend cover generation to produce 1-4 covers and attach per-cover hook/copy metadata.
- [ ] Replace detail-page JavaScript with polling, prompt modal, card rendering, retry/restart, copy text, save image and best-effort copy image.
- [ ] Write failing tests for superadmin-only default config visibility, ordinary admin 403, save/read config, project creation snapshot and execution chain model selection.
- [ ] Implement `appcore.video_cover_settings` with `get_model_defaults()` and `save_model_defaults()` using the `system_settings.video_cover_model_defaults` JSON key.
- [ ] Add `/video-cover/api/default-config` GET/POST under `@superadmin_required`; render the config modal only when `current_user.is_superadmin`.
- [ ] On project creation, snapshot current defaults into `state_json.model_defaults`; make automatic execution, retry and restart resolve provider/model from that snapshot for all four steps.
- [ ] Run focused pytest and compile checks.
