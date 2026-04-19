# 商品素材编辑页小语种详情图翻译集成设计

> 状态：需求已确认
> 范围：`/medias/` 商品编辑弹窗、小语种商品详情图、`image_translate` 任务复用与回填
> 前置：`2026-04-15-medias-list-table-and-edit-modal-design.md`、`2026-04-16-image-translate-design.md`、`2026-04-16-medias-multi-language-design.md`

## 1. 背景

当前商品素材编辑页已经具备三块基础能力：

1. 商品详情图已经有独立数据表 `media_product_detail_images`，且后端接口已经支持 `lang` 维度的列表、手工上传、从商品链接下载。
2. 编辑弹窗前端目前仍把“商品详情图”区块限制为仅英语显示，非英语语种只能看到视频素材、文案、主图。
3. 图片翻译模块 `image_translate` 已经具备持久化任务、任务详情页、失败重试、历史记录和任务恢复能力，但它仍是一个相对独立的模块，和商品素材编辑页没有业务关联。

本次需求不是重新做一套“小语种详情图翻译任务系统”，而是把现有两条能力线真正接起来：

- 小语种详情图和英语详情图一样可管理。
- 小语种可直接从自己的商品链接下载对应语种页面里的详情图。
- 小语种可从英语版详情图发起一键翻译。
- 翻译任务要和当前商品、当前语种、当前编辑入口强关联。
- 用户不仅能跳转到图片翻译详情页，还能在素材编辑页看到“有没有做过一键翻译、结果如何、是否已经写回当前小语种详情图”。

## 2. 目标

### 2.1 用户目标

- 在商品编辑弹窗切到 `de/fr/es/it/ja/pt` 等小语种时，也能直接管理“商品详情图”。
- `从商品链接一键下载` 对所有语种保持一致行为：抓取当前语种商品链接对应页面中的轮播/详情图。
- `从英语版一键翻译` 能在确认英语详情图存在后创建真实图片翻译任务。
- 用户可以从当前页面直接查看该任务进度、跳转详情页、查看历史记录、重新翻译。
- 任务结果如果全部成功，会自动整组写回当前小语种详情图位置；如果部分失败，则不覆盖当前小语种详情图。

### 2.2 系统目标

- 不新建第二套任务系统，继续复用现有 `image_translate` 项目类型、详情页、runner 和重试能力。
- 任务状态和素材状态都能回答“当前页的这组小语种详情图是否来自英语版一键翻译”。
- UI 上能同时表达：
  - 当前语种是否发起过一键翻译。
  - 最近一次一键翻译结果如何。
  - 当前显示的详情图是否已经由某次翻译任务成功回填。

## 3. 非目标

- 不新建独立的 `medias_detail_translate` 任务模块。
- 不改图片翻译详情页的核心布局，仅在必要处补充上下文展示。
- 不在本次引入“人工审核后再回填”的二次确认流。
- 不做英语详情图和小语种详情图之间的逐张手工映射编辑。
- 不改变现有英语详情图上传、从商品链接下载、删除、排序的主流程。

## 4. 核心决策

| 决策点 | 选择 |
|---|---|
| 任务系统 | 复用现有 `image_translate` |
| 发起入口 | 保留在商品素材编辑弹窗的小语种详情图区块内 |
| 历史记录 | 仍存在 `projects` / `state_json`，不新建任务表 |
| 页面关联键 | `product_id + preset=detail + source_lang=en + target_language + entry=medias_edit_detail` |
| 回填策略 | 只有任务内全部图片成功才整组覆盖当前小语种详情图 |
| 当前图来源追踪 | 在 `media_product_detail_images` 上新增来源字段，不能只靠任务历史推断 |
| 重新翻译 | 新建一条新任务，不覆盖旧历史 |

## 5. 用户流程

### 5.1 小语种详情图管理

当用户在编辑弹窗切到非英语语种（例如 `de`）时：

1. 仍然显示“商品详情图”区块。
2. 区块中提供三个入口：
   - `选择图片批量上传`
   - `从商品链接一键下载`
   - `从英语版一键翻译`
3. 区块下方显示该商品当前语种的“翻译任务记录”。

### 5.2 从商品链接一键下载

行为与英语版保持一致，但 URL 来源改为当前语种：

1. 优先使用 `localized_links_json[lang]`。
2. 若为空，则按既有规则从 `product_code` 生成默认链接：
   - `en`: `https://newjoyloo.com/products/{code}`
   - 其他语种：`https://newjoyloo.com/{lang}/products/{code}`
3. 抓取完成后直接写入当前语种的 `media_product_detail_images`。

这条流程不依赖图片翻译任务系统，和英语版逻辑完全一致。

### 5.3 从英语版一键翻译

用户点击后：

1. 后端校验当前语种不是 `en`。
2. 后端校验该商品英语详情图存在且至少一张。
3. 后端把当前英语详情图构造成 `image_translate` 任务输入，创建真实任务。
4. 前端打开“任务启动/进度弹窗”，显示：
   - 任务状态
   - 总数 / 已完成 / 失败数
   - 打开任务详情页入口
5. 用户可继续停留在当前页面，也可跳转到图片翻译详情页。

### 5.4 自动回填

任务结束时：

1. 若全部图片成功，runtime 自动整组替换当前小语种详情图。
2. 若存在失败图片，runtime 标记为“未回填”，保留当前小语种详情图不动。
3. 若后续用户在图片翻译详情页把失败项重试到全部成功，runtime 再次执行自动回填。

## 6. 页面表现

## 6.1 编辑弹窗中的详情图区块

### 英语语种

- 标题：`商品详情图（英文原始版，用于后续图片翻译）`
- 按钮：
  - `选择图片批量上传`
  - `从商品链接一键下载`
- 不显示 `从英语版一键翻译`
- 不显示“小语种翻译任务记录”

### 非英语语种

- 标题：`商品详情图（德语）` 之类，继续带当前语种标签。
- 按钮：
  - `选择图片批量上传`
  - `从商品链接一键下载`
  - `从英语版一键翻译`
- 在图片区块下方追加一块 `翻译任务记录`

## 6.2 状态提示

小语种详情图区块标题旁增加状态徽标，状态来源于“当前语种最近一次关联任务”和“当前详情图是否来自翻译结果”：

- `未做翻译`
- `翻译中`
- `最近翻译成功`
- `最近翻译失败`
- `已由英语版翻译回填`

当当前语种现有详情图来自某次英语版翻译写回时，在图片区块顶部显示轻提示条：

> 当前语种详情图来自英语版一键翻译任务

提示条右侧提供：

- `查看任务详情`

## 6.3 翻译任务记录

位于小语种详情图区块下方，列表仅展示当前商品 + 当前语种 + `preset=detail` + `entry=medias_edit_detail` 的任务。

每条记录展示：

- 发起时间
- 任务状态
- 总图数 / 成功数 / 失败数
- 是否已回填到当前语种详情图
- 最后更新时间

每条记录提供操作：

- `查看详情`
- `重新翻译`

失败任务不在编辑页单独做“逐张重试”，沿用图片翻译详情页里的已有重试能力。

## 7. 数据模型

## 7.1 `projects.state_json` 增加素材页关联上下文

继续复用 `image_translate` 项目类型，但在其状态中增加专门的素材页上下文：

```json
{
  "type": "image_translate",
  "preset": "detail",
  "target_language": "de",
  "target_language_name": "德语",
  "items": [...],
  "medias_context": {
    "entry": "medias_edit_detail",
    "product_id": 123,
    "source_lang": "en",
    "target_lang": "de",
    "source_bucket": "media",
    "source_detail_image_ids": [11, 12, 13],
    "auto_apply_detail_images": true,
    "apply_status": "pending|applied|skipped_failed|apply_error",
    "applied_at": "2026-04-19T21:30:00+08:00",
    "applied_detail_image_ids": [41, 42, 43],
    "last_apply_error": ""
  }
}
```

用途：

- 支撑编辑页历史记录过滤。
- 支撑图片翻译详情页显示“来自哪个商品、哪个语种入口”。
- 支撑任务结束后的自动回填结果追踪。

## 7.2 `media_product_detail_images` 增加来源追踪字段

仅靠任务历史无法可靠回答“当前页面正在显示的这组小语种详情图是否来自翻译任务”，因为用户可能在回填成功后再次手工上传或从链接下载。

因此在 `media_product_detail_images` 上新增以下字段：

```sql
ALTER TABLE media_product_detail_images
  ADD COLUMN origin_type VARCHAR(32) NOT NULL DEFAULT 'manual' COMMENT 'manual|from_url|image_translate',
  ADD COLUMN source_detail_image_id INT NULL COMMENT '若来自英语版翻译，则记录源英语详情图 id',
  ADD COLUMN image_translate_task_id VARCHAR(64) NULL COMMENT '若来自翻译任务，则记录任务 id',
  ADD KEY idx_origin_task (image_translate_task_id),
  ADD KEY idx_source_detail_image (source_detail_image_id);
```

字段语义：

- `manual`: 用户自己上传。
- `from_url`: 从当前语种商品链接下载。
- `image_translate`: 从英语版详情图翻译得到。

这样编辑页可以准确判断：

- 当前详情图是否来自翻译任务。
- 来自哪次任务。
- 若用户后来手动改过图，来源状态是否已经失效。

## 8. 后端设计

## 8.1 `appcore.medias`

扩展详情图 DAO：

- `add_detail_image(...)` 增加可选参数：
  - `origin_type`
  - `source_detail_image_id`
  - `image_translate_task_id`
- `list_detail_images(product_id, lang)` 返回上述来源字段。
- 新增：
  - `soft_delete_detail_images_by_lang(product_id, lang)`
  - `replace_detail_images_for_lang(product_id, lang, images)`  
    用于在“全部翻译成功”后整组替换当前小语种详情图。

## 8.2 `web/routes/medias.py`

保留现有：

- `GET /medias/api/products/<pid>/detail-images?lang=...`
- `POST /medias/api/products/<pid>/detail-images/from-url`
- `POST /medias/api/products/<pid>/detail-images/bootstrap`
- `POST /medias/api/products/<pid>/detail-images/complete`

新增：

### `POST /medias/api/products/<pid>/detail-images/translate-from-en`

职责：

1. 校验 `lang != 'en'`
2. 校验英语详情图存在
3. 读取英语详情图列表
4. 构造 `image_translate` 任务输入并创建任务
5. 启动现有 `image_translate_runner`
6. 返回：

```json
{
  "task_id": "...",
  "detail_url": "/image-translate/<task_id>"
}
```

### `GET /medias/api/products/<pid>/detail-image-translate-tasks?lang=de`

职责：

1. 从 `projects` 中读取当前用户的 `image_translate` 项目
2. 解析 `state_json`
3. 过滤出：
   - `preset = detail`
   - `medias_context.entry = medias_edit_detail`
   - `medias_context.product_id = pid`
   - `medias_context.target_lang = lang`
4. 组装当前页面所需摘要字段，按创建时间倒序返回

### `GET /medias/api/products/<pid>/detail-image-translate-summary?lang=de`

可选的轻量摘要接口，用于编辑页快速渲染顶部状态徽标；若实现时认为没必要，也可以直接复用列表接口首项。

## 8.3 `web/routes/image_translate.py`

复用现有页面和详情路由，仅增加上下文透传：

- 列表页无需改入口逻辑。
- 详情页增加对 `medias_context` 的展示，例如：
  - 来源：商品素材编辑页
  - 商品 ID / 商品名
  - 目标语种
  - 是否已回填到商品详情图

任务创建路径上，需要让 `create_image_translate(...)` 支持附带 `medias_context`。

## 8.4 `appcore.task_state.create_image_translate`

增加可选参数：

- `medias_context: dict | None = None`

创建任务时把它写入 `state_json`，用于后续关联和回填。

## 8.5 `appcore.image_translate_runtime`

新增两类能力：

### 源图读取能力

当前 runtime 默认把源图当普通上传对象走 `tos_clients.download_file(...)`。

本次要支持：

- `source_bucket = upload` 时：沿用 `download_file`
- `source_bucket = media` 时：走 `download_media_file`

### 自动回填能力

当任务结束后，如果 `medias_context.auto_apply_detail_images = true`：

1. 检查 `items` 是否全部 `status = done`
2. 若不是全部成功：
   - 仅更新 `apply_status = skipped_failed`
   - 不改当前小语种详情图
3. 若全部成功：
   - 以任务输出图为数据源
   - 整组替换 `product_id + target_lang` 当前详情图
   - 新插入的详情图记录写入：
     - `origin_type = image_translate`
     - `source_detail_image_id = 对应英语源图 id`
     - `image_translate_task_id = 当前 task_id`
   - 更新任务中的：
     - `apply_status = applied`
     - `applied_at`
     - `applied_detail_image_ids`
4. 若回填过程中异常：
   - 任务本身仍保持原有 `done / error` 逻辑
   - `apply_status = apply_error`
   - `last_apply_error = ...`

这样不会把“翻译成功”和“写回业务数据成功”混为一谈。

## 9. 前端设计

## 9.1 `web/static/medias.js`

编辑弹窗相关改动：

1. 不再在非英语语种隐藏详情图区块。
2. `edRenderActiveLangView()` 中，详情图 controller 始终按当前语种加载。
3. 英语和非英语按钮显隐分开控制：
   - `en`: 上传 + 从商品链接下载
   - `!= en`: 上传 + 从商品链接下载 + 从英语版一键翻译
4. 新增翻译启动弹窗和任务历史渲染逻辑。
5. 每次切换语种时同步刷新：
   - 当前语种详情图
   - 当前语种翻译状态摘要
   - 当前语种翻译历史记录

## 9.2 `web/templates/_medias_edit_detail_modal.html`

详情图 section 扩展为：

- 状态徽标位
- 当前图来源提示条
- `从英语版一键翻译` 按钮
- 翻译任务记录列表
- 任务启动/进度弹窗

视觉继续沿用 Ocean Blue Admin：

- 白底卡片
- 海洋蓝主按钮
- 无紫色
- 任务记录使用低对比度边框和蓝青色状态标签

## 9.3 任务启动弹窗

弹窗职责：

- 启动翻译任务
- 轮询或订阅当前任务状态
- 提供 `查看任务详情` 按钮
- 在成功或失败后允许关闭

关闭弹窗时强制刷新：

- 当前语种详情图
- 当前语种翻译记录
- 状态徽标

## 10. 回填与覆盖规则

### 10.1 全成功

- 删除当前语种旧详情图记录（软删）
- 插入当前任务新结果
- 标记任务 `apply_status = applied`

### 10.2 部分失败

- 不改当前语种旧详情图
- 标记任务 `apply_status = skipped_failed`

### 10.3 用户后续手工修改

若用户在自动回填成功后又手工上传或从链接下载新图：

- 新图 `origin_type` 会变成 `manual` 或 `from_url`
- 页面顶部“已由英语版翻译回填”状态会失效
- 但历史记录里仍保留过去那次翻译任务和“曾经已回填”的事实

## 11. 错误处理

| 场景 | 行为 |
|---|---|
| 非英语语种点击翻译，但英语详情图为空 | 返回 400，提示先准备英语详情图 |
| 当前语种已经有详情图，再发起翻译 | 允许，成功后整组覆盖 |
| 翻译任务部分失败 | 历史显示失败，当前语种详情图保持不变 |
| 翻译任务全部成功但回填失败 | 任务详情可见成功结果，但编辑页显示“回填失败” |
| 用户从历史记录点击重新翻译 | 新建任务，旧任务保留 |

## 12. 测试策略

### 12.1 路由测试

- 小语种可见详情图区块相关接口返回正常。
- `translate-from-en` 在英语详情图为空时拒绝。
- `translate-from-en` 创建出的任务带正确 `medias_context`。
- 历史记录接口只返回当前商品 + 当前语种关联任务。

### 12.2 runtime 测试

- `source_bucket = media` 时能正确下载英语详情图源文件。
- 全部成功时自动回填并写入来源字段。
- 部分失败时不回填。
- 失败后重试到全成功时能补触发回填。

### 12.3 前端测试

- 语种切换后详情图区块不再只限英语。
- 小语种按钮显隐正确。
- 任务记录 empty / loading / error / loaded 四态正确。
- 查看详情链接正确跳转到 `/image-translate/<task_id>`。

## 13. 风险与取舍

### 13.1 风险

- `projects.state_json` 过滤历史任务是应用层解析，不是数据库索引查询，大量任务时会有一定成本。
- 自动回填引入“翻译任务成功但业务写回失败”的第二层状态，需要在 UI 上表达清楚。

### 13.2 取舍

- 本次优先复用现有 `image_translate`，接受“历史过滤在应用层做”的成本，换取更少的重复实现。
- 为了准确表达“当前图来源”，接受给 `media_product_detail_images` 增加来源字段，而不是仅靠任务历史推断。

## 14. 预期文件改动

- 修改：`appcore/medias.py`
- 修改：`appcore/task_state.py`
- 修改：`appcore/image_translate_runtime.py`
- 修改：`web/routes/medias.py`
- 修改：`web/routes/image_translate.py`
- 修改：`web/static/medias.js`
- 修改：`web/templates/_medias_edit_detail_modal.html`
- 新增：`db/migrations/2026_04_19_medias_detail_image_translate_provenance.sql`
- 测试：`tests/test_image_translate_routes.py`
- 测试：`tests/test_image_translate_runtime.py`
- 测试：`tests/test_web_routes.py` 或新增 `tests/test_medias_detail_image_translate_routes.py`
