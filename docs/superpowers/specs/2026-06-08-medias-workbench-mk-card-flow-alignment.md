# 素材工作台明空卡片流程对齐设计

Date: 2026-06-08

## Anchors

- `AGENTS.md`：文档驱动代码、素材管理路由验证、POST CSRF、测试环境验收。
- `docs/superpowers/specs/2026-06-06-medias-product-video-workbench-design.md`：素材工作台首版入口、`POST /mk-import/video` 入库、`POST /tasks/api/parent` 创建小语种任务。
- `docs/superpowers/specs/2026-05-20-mk-import-progress-modal-design.md`：明空视频卡片入库进度弹窗步骤、失败反馈和后续任务入口。
- `docs/superpowers/specs/2026-05-21-mk-import-product-owner-step-design.md`：入库前确认产品负责人，发布域名前不得开放后续任务入口。
- `docs/superpowers/specs/2026-06-05-mk-small-language-modal-product-header-design.md`：小语种弹窗顶部展示产品信息、链接检测、复制和 90 天消耗。

## Context

`/medias/product/video_workbench/<pid>` 已经能展示产品级明空视频卡片，但当前「加入素材库」只是按钮旁边的文字状态，「创建小语种任务」也只是简化表单。运营在同一个产品工作台里处理素材时，体验和 `/xuanpin/mk#videos` 的成熟视频卡片流程割裂，容易漏选产品负责人、漏确认发布域名，也缺少 AI 建议、产品上下文和任务创建结果反馈。

生产页面验证显示，成熟明空视频卡片流程至少包含：

1. 「加入素材库」打开入库工作进程弹窗。
2. 入库弹窗展示产品中文名、产品链接可达状态、产品 ID、90 天消耗、素材名、AI 精细评估建议。
3. 入库流程按步骤展示：准备素材信息、选择产品负责人、检查产品与链接、下载明空原视频、写入素材库、选择发布域名、后续任务入口。
4. 发布域名保存后才显示「下一步：创建小语种任务 / 去任务中心 / 去素材管理 / 关闭」。
5. 「创建小语种翻译任务」弹窗展示产品信息、AI 建议、小语种翻译负责人、原视频处理人、目标国家卡片、已有任务和强制创建、紧急任务、内联成功/失败反馈。

## Goals

1. 素材工作台未入库卡片点击「加入素材库」时，打开与明空视频素材库一致的入库进度弹窗，而不是仅更新卡片底部状态。
2. 工作台入库弹窗必须支持产品负责人步骤、发布域名步骤和后续任务入口；未确认发布域名前不开放小语种任务创建。
3. 工作台已入库卡片点击「创建小语种任务」时，打开与明空视频素材库一致的小语种创建弹窗。
4. 小语种弹窗必须展示产品信息区、负责人语义说明、目标国家卡片、已有任务禁用与强制创建、紧急任务、成功任务 ID/跳转入口和失败原因。
5. 工作台继续使用现有后端契约：`POST /mk-import/video`、`POST /tasks/api/parent`、`/tasks/api/translation-work-users`、`/tasks/api/languages`、`/medias/api/products/<pid>/product-link-domains`。

## Non-Goals

1. 不新增数据库表或迁移。
2. 不改变明空选品页现有成熟流程。
3. 不改变任务中心父任务创建契约。
4. 不强制要求工作台首版补齐所有 AI 精细评估重跑入口；缺少评估结果时显示“暂无/建议先评估”，不阻断创建。

## UX Contract

### 加入素材库

工作台卡片点击「加入素材库」后：

1. 立即打开 `vwImportProgressModal`。
2. 顶部显示产品中文名、产品链接、链接检测状态、产品 ID、90 天消耗。
3. 步骤按成熟明空流程展示，并在每一步内承载重试、确认产品负责人、确认发布域名和后续入口按钮。
4. 若产品已存在，产品负责人步骤显示沿用现有产品负责人；若是新品，必须选择产品负责人后才调用入库接口。
5. 入库接口请求体优先发送 `product_owner_id`，`mk_video_metadata.media_product_id` 使用当前工作台产品 ID。
6. 入库成功后加载并保存发布域名；保存成功后才显示「下一步：创建小语种任务」。
7. 点击下一步复用同一个小语种弹窗，并以刚入库返回的 `media_product_id` / `media_item_id` 创建任务。

### 创建小语种任务

工作台卡片点击「创建小语种任务」后：

1. 打开 `vwXiaoModal`，标题为「创建小语种翻译任务」。
2. 顶部产品信息区展示产品主图或视频封面、产品中文名、产品链接、产品 ID、90 天消耗。
3. 负责人候选来自 `/tasks/api/translation-work-users`；原视频处理人复用同一候选范围。
4. 目标国家使用卡片式勾选 UI，已有任务默认禁用并提供「强制创建」按钮。
5. 创建请求发送 `media_product_id`、`media_item_id`、`product_link`、`translator_id`、`raw_processor_id`、`language_assignments`、`countries`、`force`、`is_urgent`。
6. 创建中、成功任务 ID/跳转入口、失败请求路径和后端错误原因都在弹窗内展示，不用浏览器 `alert` 承载主反馈。

## Verification

1. `pytest tests/test_medias_product_video_workbench.py -q`
2. `python3 -m compileall web/routes/medias appcore -q`
3. `git diff --check`
4. 启动本地或测试环境服务后，用浏览器点击：
   - 未登录访问 `/medias/product/video_workbench/<pid>` 返回 302。
   - 登录后打开工作台页面 200。
   - 未入库卡片点击「加入素材库」出现入库工作进程弹窗，能看到产品负责人、发布域名和后续任务入口步骤。
   - 已入库卡片点击「创建小语种任务」出现产品信息、AI 建议/暂无提示、目标国家卡片、紧急任务和内联反馈。
   - 发布到 8080 测试环境后重复关键点击验证。
