# 任务流程语言标签与明空小语种弹窗修复设计

日期：2026-05-20

## 背景

明空选品 `/xuanpin/mk` 的「加入素材库」后续流程中，点击「下一步：创建小语种任务」会打开小语种任务弹窗。当前弹窗存在三个体验问题：

1. 小语种任务弹窗 `z-index` 低于入库进程弹窗，导致弹窗显示在当前框下面。
2. 目标国家/语种只显示 `DE`、`FR` 这类代码，部分操作员看不懂。
3. 视频素材库和「昨天消耗前100」里未入库素材也能点击「创建小语种翻译任务」，但未入库素材没有本地产品和素材 ID，无法直接创建翻译任务。

用户新增要求：除明空弹窗外，任务流程中所有面向人的 `DE` 这类语言/国家代码展示，都按「中文名称 (代码)」显示，例如 `德语 (DE)`；提交给后端和数据库保存仍保持原始代码。

## 锚点

- `AGENTS.md`：任务中心端到端流程与文档驱动代码门禁。
- `docs/superpowers/specs/2026-05-16-task-center-e2e-flow-design.md`：明空选品创建小语种任务链路。
- `docs/superpowers/specs/2026-05-18-mingkong-video-material-library-subtabs-design.md`：视频素材库 / 昨天消耗前100卡片动作。
- `docs/superpowers/specs/2026-05-20-task-center-child-acceptance-design.md`：任务中心子任务跳素材管理与验收展示。
- `docs/superpowers/specs/2026-05-20-task-center-review-process-view-design.md`：任务中心中文过程视图。
- `web/static/CLAUDE.md`：CSRF 与前端静态资源约束。

## 目标

1. 小语种任务弹窗永远显示在入库进程弹窗上方。
2. 明空小语种语言选择、任务中心列表、任务中心详情、审核流程事实、产出素材、绑定产出物等任务流程展示统一使用 `中文名称 (代码)`。
3. `/tasks/api/languages` 返回代码、中文名和展示标签，前端提交值仍为代码。
4. 明空视频素材库和昨天消耗 Top100 只有已入库素材可点击「创建小语种翻译任务」。
5. 已入库素材点击「创建小语种翻译任务」直接用本地 `media_product_id` / `media_item_id` 调 `/tasks/api/parent` 创建任务，不重复走导入流程。

## 非目标

- 不改变任务表、素材表和明空素材快照表结构。
- 不改变后端保存的 `country_code` / `target_langs` 值。
- 不重做任务中心 UI 布局。
- 不给未入库素材自动静默入库；未入库仍走「加入素材库」按钮。

## 实现要点

- `appcore/tasks.py::list_enabled_target_languages()` 查询 `code, name_zh`，返回 `code` 为大写、`name_zh` 为中文名、`label` 为 `中文名 (CODE)`。
- `web/templates/mk_selection.html`：
  - `mki-xiao-backdrop` 的层级高于 `mki-progress-backdrop`。
  - `mkiXiaoLangLabel()` 渲染 `德语 (DE)`，checkbox value 仍为 `DE`。
  - 卡片按钮根据 `has_local_material_in_library`、`material_ad_status.media_product_id`、`material_ad_status.media_item_id` 设置可用性。
  - 已入库按钮走新的本地任务创建函数，未入库按钮禁用并提示先加入素材库。
- `web/templates/tasks_list.html`：
  - 增加任务中心语言标签 helper，优先用 `/tasks/api/languages`，失败时用内置常见语种兜底。
  - 列表国家列、创建弹窗语言选择、详情标题、验收面板、事件事实、产出素材和绑定弹窗统一使用 helper。
- 批量翻译任务列表、详情、子项、旧版批量翻译弹窗，以及多语/全能视频翻译任务入口里的语言标签也统一显示 `中文名称 (代码)`。

## 验证

```bash
pytest tests/test_appcore_tasks_supporting_data.py tests/test_tasks_routes.py tests/test_task_center_closure_assets.py tests/test_xuanpin_routes.py tests/test_bulk_translate_projection.py -q
python -m compileall appcore web tests -q
git diff --check
```
