# 任务中心端到端流程补全设计

- **日期**：2026-05-16
- **上位**：[明空流水线主 spec](2026-04-26-mingkong-pipeline-master.md)
- **实现分支**：`shaky-falcon`

## 目标

补全 明空选品(A) → 任务中心(C) → 素材管理(B) → 推送管理(F) 业务主线断点。

## 三 Phase 概述

| Phase | 内容 | 新增文件 | 修改文件 |
|-------|------|---------|---------|
| 1 | 选品页一键创建任务 | — | `appcore/tasks.py`, `web/routes/tasks.py`, `web/templates/mk_selection.html` |
| 2 | 任务-素材绑定 | `db/migrations/2026_05_16_task_binding_to_media_items.sql` | `appcore/medias.py`, `appcore/mk_import.py`, `appcore/tasks.py`, `web/routes/tasks.py`, `web/templates/tasks_list.html`, `web/templates/medias_list.html` |
| 3 | 产出面板增强 + 推送溯源 | — | `web/templates/tasks_list.html`, `web/static/pushes.js` |

## Phase 1: 选品 → 一键创建任务

### 服务层
- `appcore/tasks.py` — `import_and_create_task()`：链式调用 `mk_import.import_mk_video()` + `create_parent_task()`。已入库视频走 `find_existing_product_item_by_meta()` 回退。
- `appcore/mk_import.py` — `find_existing_product_item_by_meta()`：按 product_code 查已有产品+英文素材。

### 路由
- `POST /tasks/api/import-and-create` — `@login_required + @admin_required`，异常分类 409/502/500/400

### 前端
- `mk_selection.html`：视频卡片增加"做小语种"按钮 + 翻译员/国家选择 modal

## Phase 2: 任务-素材绑定

### 数据库
- `media_items.task_id INT DEFAULT NULL` + index `idx_task`
- 英文素材（任务输入）task_id=NULL；翻译产出素材 task_id=子任务 id

### 服务层
- `appcore/medias.py` — `create_item(task_id=...)`、`update_item_task_id()`
- `appcore/tasks.py` — `list_task_artifacts()`、`list_unbound_items_for_task()`

### 路由
- `GET /tasks/api/<tid>/artifacts` — 列出任务产出素材
- `GET /tasks/api/<tid>/unbound-items` — 列出可手动绑定的素材
- `POST /tasks/api/<tid>/bind-items` — 手动绑定素材到任务

### 前端
- `tasks_list.html`：任务详情新增"产出素材"面板 + "绑定产出物"按钮 + modal
- `medias_list.html`：桥接脚本暴露 `window.MEDIAS_TASK_BRIDGE_TASK_ID`

## Phase 3: 产出面板增强 + 推送溯源

### 前端
- `tasks_list.html`：产出面板显示推送状态（已推送/未推送）；支持 `?task_id=N` 深链自动打开抽屉
- `pushes.js`：素材行若 `task_id` 非空显示"任务 #N"徽章，链接到任务详情

## 验证

1. `pytest tests/ -q` 通过
2. mk_selection → 做小语种 → 任务列表可见 → 翻译员操作 → 提交 → 审核 → 产出面板显示素材 → 推送页可见任务徽章
3. 手动绑定弹窗正常
4. `?task_id=N` 深链自动打开
