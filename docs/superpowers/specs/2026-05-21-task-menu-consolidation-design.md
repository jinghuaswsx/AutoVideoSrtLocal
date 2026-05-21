# 任务菜单收敛设计

- 日期：2026-05-21
- 上位锚点：
  - `AGENTS.md`
  - `web/templates/CLAUDE.md`
  - `docs/superpowers/specs/2026-05-16-task-center-e2e-flow-design.md`
  - `docs/superpowers/specs/2026-05-21-raw-video-pool-tabs-pagination-design.md`
  - `docs/superpowers/specs/2026-05-21-task-center-raw-self-review-design.md`

## 背景

去字幕原始视频素材处理已经在任务中心详情抽屉里展示状态、素材和审核动作。继续保留 `/raw-video-pool/` 独立集合页和侧边栏子菜单，会让用户以为原始视频处理和小语种翻译是两套入口。当前产品口径是任务流程统一在任务中心里处理。

## 目标

1. 侧边栏任务区域只保留一个菜单入口，名称为“小语种视频翻译”，链接到 `/tasks/`。
2. 不再展示“去字幕原始视频素材处理”侧边栏入口，也不再把任务区域做成展开集合菜单。
3. `/raw-video-pool/` 作为历史页面入口保留兼容，登录后直接重定向到 `/tasks/`。
4. 原始视频处理仍保留在任务中心详情抽屉内；任务中心不再跳转到 `/raw-video-pool/` 页面。
5. `/raw-video-pool/api/*` 下载和上传 API 暂时保留，避免破坏现有服务层和兼容调用。

## 非目标

1. 不删除 `raw_video_pool` service 和 API。
2. 不改变任务状态机、牛马去字幕流程或审核规则。
3. 不新增数据库表或迁移。

## 验证

1. `/raw-video-pool/` 登录后返回 302，`Location` 指向 `/tasks/`。
2. 侧边栏模板不包含 `/raw-video-pool/` 菜单链接和 `sidebar-task-group` 集合菜单。
3. `/tasks/` 页面标题、浏览器标题和侧边栏入口显示“小语种视频翻译”。
4. 任务中心模板不再通过 `window.open('/raw-video-pool/'...)` 跳转旧页面。
