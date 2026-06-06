# 任务中心新品任务视频创建流程设计

- 日期：2026-06-06
- 上位锚点：
  - `AGENTS.md`
  - `docs/superpowers/specs/2026-05-16-task-center-e2e-flow-design.md`
  - `docs/superpowers/specs/2026-05-20-task-center-direct-assignment-design.md`
  - `docs/superpowers/specs/2026-05-20-task-center-per-language-assignment-design.md`
  - `docs/superpowers/specs/2026-04-21-medias-raw-sources-design.md`
  - `docs/superpowers/specs/2026-05-21-task-center-raw-source-reuse-design.md`

## 背景

当前任务中心已有 `POST /tasks/api/parent`，可基于素材管理里的英文 `media_items` 创建去字幕父任务和小语种翻译子任务，并在创建后自动提交牛马去字幕。素材管理和 Meta 热帖也分别具备产品/英文视频入库能力。

缺口是：没有一个统一入口可以把外部本地视频或 Meta 热帖视频直接变成“素材管理产品 + 英文视频素材 + 去字幕父任务 + 小语种子任务”。用户仍需要在不同页面之间手工切换、重复上传或先加入素材库再创建任务。

## 目标

1. 任务中心左侧子菜单新增“新品任务”，页面标题为“创建新产品和任务”。
2. 支持管理员上传本地带货短视频，并填写产品中文名、商品链接、商品主图等必要信息，一次性创建素材管理产品、英文视频素材和任务中心父子任务。
3. 创建任务时必须提交 `raw_processor_id`、目标语种和 `language_assignments`，并在创建成功后自动提交牛马去字幕。
4. Meta 热帖视频卡片可直接发起新品任务；如果视频尚未本地化完成，需要明确提示等待本地视频就绪。
5. Meta 热帖已有入库产品/素材时复用已有 `local_product_id` 和 `local_media_item_id`，避免重复入库；未入库时后端先完成入库再创建任务。
6. 创建成功后返回产品 ID、英文素材 ID、父任务 ID 和去字幕提交状态，前端展示任务中心跳转入口。

## 非目标

1. 不重做现有 7 步 `/task-creator/` 项目式流水线；该工具保留用于需要 AI 分析、封面、文案生成的复杂流程。
2. 不新增数据库表；复用 `media_products`、`media_items`、`tasks`、`task_events`。
3. 不改变任务中心父子任务状态机。
4. 不改变 `media_raw_sources` 的审核入库规则；去字幕结果仍在父任务审核通过后桥接成原始去字幕素材。
5. 不在本次实现 Meta 视频下载任务本身；只消费已经 `local_video_status=downloaded` 的视频。

## 请求契约

### 本地上传入口

`POST /tasks/api/new-product`

`multipart/form-data` 字段：

- `source=upload`
- `product_name`：必填，写入素材管理产品名。
- `product_link`：可选但建议填写；若填写，写入 `localized_links_json.en`，并兼容写入旧 `product_link` 字段。
- `product_main_image_url`：可选，兼容写入旧 `main_image` 字段。
- `product_code`：可选；若未填且商品链接包含 `/products/<handle>`，自动取 handle。
- `video_file`：必填，支持现有视频扩展。
- `owner_id`：必填，素材管理产品负责人。
- `raw_processor_id`：必填，去字幕原始视频处理人。
- `countries`：JSON 数组或逗号分隔目标语种。
- `language_assignments`：JSON object，覆盖每个目标语种。
- `is_urgent`：可选，`true/1` 时父子任务均标紧急。
- `force`：可选，允许强制绕过同素材同语种重复任务检查。

### Meta 热帖入口

同一接口使用 JSON：

```json
{
  "source": "meta_hot_post",
  "post_id": 123,
  "owner_id": 9,
  "raw_processor_id": 9,
  "countries": ["DE", "FR"],
  "language_assignments": {"DE": 9, "FR": 10},
  "is_urgent": false,
  "force": false
}
```

## 后端设计

新增服务函数负责统一编排：

1. 标准化并校验目标语种、负责人、翻译负责人。
2. `source=upload`：
   - 创建或复用素材管理产品。
   - 用规范文件名把视频写入 `local_media_storage`。
   - 创建 `media_items lang='en'`，`skip_push=1`。
3. `source=meta_hot_post`：
   - 若已有 `local_product_id/local_media_item_id`，直接复用。
   - 否则复用 Meta 热帖现有入库逻辑，把本地视频写入素材管理。
4. 调用 `tasks.create_parent_task()` 创建父任务和子任务。
5. 若已有有效 `source_raw_id`，沿用 `find_ready_raw_source_for_media_item()` 的复用规则并跳过去字幕；否则调用 `task_raw_video_processing.start_niuma_processing_for_parent_task()`。
6. 记录系统审计，响应中返回：

```json
{
  "ok": true,
  "media_product_id": 1,
  "media_item_id": 2,
  "parent_task_id": 3,
  "is_new_product": true,
  "product_detail_url": "/medias/example-handle",
  "raw_processing": {"status": "submitted"}
}
```

## 前端设计

### 任务中心新品任务页

- 路由：`/tasks/new-product`
- 权限：`login_required + task_center + admin`
- 表单分区：
  - 产品信息：产品中文名、商品链接、商品主图 URL、产品负责人。
  - 视频来源：本地视频上传。
  - 任务分配：原视频处理人、目标语种、每语种翻译负责人、紧急任务。
- 提交期间展示进行中状态；成功后使用后端返回的 `product_detail_url` 显示素材管理产品和任务中心详情链接；失败显示请求路径、HTTP 状态和后端错误。

### Meta 热帖页

- 视频卡片新增或复用“新品任务”动作。
- 点击后打开任务分配弹窗，预填商品标题、商品链接、主图和视频来源；顶部负责人作为素材归属和默认翻译负责人，每个目标语种可以单独选择翻译负责人。
- 提交到 `POST /tasks/api/new-product`，由后端决定复用已有入库素材或先导入再创建。
- 未本地化视频禁用按钮或返回明确错误；若历史记录标记为已下载但本地视频文件已经缺失，也必须禁用“新品任务”入口，避免点击后才失败。
- `local_video_url` / `local_video_cover_url` 仅在对应本地文件真实存在时返回，避免 Meta 热帖视频卡片加载缺失封面造成 404。
- Meta 热帖入库时，英文视频素材的 `filename` / `display_name` 必须使用无空格安全文件名，避免商品英文标题含空格时被素材管理文件名规则拒绝。
- Meta 热帖列表响应需要返回 `new_product_parent_task_id`：基于 `local_media_item_id` 查找未归档、未取消的父任务，用于刷新后仍显示“任务 #xxx”，防止同一素材重复发起新品任务。

## 验证

1. `tests/test_tasks_routes.py` 覆盖新品任务页面、上传接口参数解析、Meta 热帖 JSON 入口，以及创建链路透传 `raw_processor_id/language_assignments/is_urgent`。
2. `tests/test_meta_hot_posts_routes.py` 覆盖卡片包含新品任务入口、紧急任务选项和 `/tasks/api/new-product` 提交契约。
3. `python -m compileall appcore/new_product_tasks.py web/routes/tasks.py`
4. `git diff --check`
