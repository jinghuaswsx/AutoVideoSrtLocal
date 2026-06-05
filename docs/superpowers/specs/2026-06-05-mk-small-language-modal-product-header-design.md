# 明空小语种创建弹窗产品顶部信息设计

- **日期**：2026-06-05
- **上位锚点**：
  - `AGENTS.md`：文档驱动代码、主工作目录零污染、任务中心流程闭环。
  - `docs/superpowers/specs/2026-05-20-mk-selection-task-contract-alignment.md`：明空小语种创建弹窗必须保留创建中、成功任务 ID/跳转入口或失败原因。
  - `docs/superpowers/specs/2026-05-22-fine-ai-material-import-advice-design.md`：小语种弹窗优先展示精细 AI 国家建议，缺少结果不阻断创建。
  - `docs/superpowers/specs/2026-05-29-mk-import-task-urgent-and-store-card-result-design.md`：创建小语种任务弹窗承载任务创建前的最后确认信息。

## 背景

运营从明空选品卡片点击「创建小语种翻译任务」时，当前弹窗顶部直接进入 AI 精细评估和负责人选择。页面背景被遮罩后，管理员需要回看商品名称、商品链接、产品 ID 和 90 天消耗，只能退出弹窗或依赖卡片位置记忆，容易选错目标语种或负责人。

「加入素材库」进度弹窗已经在顶部展示产品中文名、产品链接、产品 ID、90 天消耗。创建小语种任务弹窗应复用同一组上下文信息，并补充商品主图，方便在创建前最后确认。

## 目标

1. 在「创建小语种翻译任务」弹窗标题下方展示产品信息区。
2. 信息区展示产品主图，固定 200x200，优先使用卡片 `data-mki-main-image`，缺失时回退封面图。
3. 信息区展示产品中文名、产品链接、产品 ID、90 天消耗。
4. 产品链接保留打开新窗口、复制和可访问状态展示；产品 ID 保留复制。
5. 信息区为空时不阻断创建，只显示 `--` 或 `无主图`。

## 不做范围

- 不改 `POST /tasks/api/parent` 请求契约。
- 不新增后端字段、数据库字段或迁移。
- 不改变 AI 精细评估、目标国家选择、紧急任务勾选、负责人选择逻辑。
- 不改 Meta 热帖页同名小语种创建弹窗。

## 实现范围

`web/templates/mk_selection.html`：

1. 新增小语种弹窗产品信息 DOM，位置在标题和运营备注/AI 建议之前。
2. 新增前端 helper，从 `mkiXiaoOpenModal(options)` 的 `sourceButton` 或显式 options 中读取：
   - `productName`
   - `productLink`
   - `productCode`
   - `spends`
   - `mainImage`
   - `coverUrl`
3. `mkiXiaoCreateFromImportedMaterial()` 打开弹窗时继续传入 `sourceButton: btn`，不额外请求后端。
4. 链接状态检查使用独立 DOM id，避免覆盖「加入素材库」进度弹窗的链接状态。

## 验证

1. `pytest tests/test_xuanpin_routes.py -q`
2. `node --check` 对 `web/templates/mk_selection.html` 内脚本片段做语法检查，或使用现有模板静态断言覆盖新增 helper 和 DOM。
3. `git diff --check`
