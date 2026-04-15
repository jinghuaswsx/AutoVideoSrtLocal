# 素材管理 · 列表表格化 + 独立编辑弹窗 设计

> 状态：需求已确认
> 前置：`2026-04-15-medias-add-single-page-design.md`（新增弹窗已上线）

## 1. 目标

- 列表页从卡片网格改为表格，展示更多元信息
- 单关键词搜索（同时匹配产品名与产品 ID）
- 编辑从"新增 modal 复用"拆出为**独立编辑 modal**，布局更宽松
- 产品主图显示框统一改为 400×400 正方形
- 产品主图不再是编辑保存的硬必填（新增仍必填）

## 2. 列表页

### 2.1 表格列

| # | 列 | 值来源 |
|---|---|---|
| 1 | ID | `media_products.id` |
| 2 | 产品主图 | 48×48 缩略，无图占位（小图标） |
| 3 | 产品名称 | `name`，点击打开编辑 modal |
| 4 | 产品 ID | `product_code`，等宽字体 |
| 5 | 素材数量 | `COUNT(items)` 徽标 |
| 6 | 素材文件名 | 每行一个文件名，最多展示 5 条；>5 条显示"+N 更多"在末行 |
| 7 | 创建时间 | `YYYY-MM-DD HH:mm` |
| 8 | 修改时间 | 同上 |
| 9 | 操作 | 编辑 / 删除 |

### 2.2 搜索与过滤

- 单关键词框：`WHERE name LIKE ? OR product_code LIKE ?`
- 保留"已归档"与"查看全部"两个 chip
- 分页沿用现有

### 2.3 样式

- `<table class="oc-table">`，行高约 68px（容纳 5 行文件名 + padding）；文件名列固定最大宽度 260px，超长 `text-overflow: ellipsis`
- 单行超过 5 条时最后一个 `<li>` 渲染 `+N 更多`，不再滚动
- 表头固定在列表区顶部（非 sticky 也可接受，初版不做 sticky）

## 3. 新增入口

- 保留现有 modal（`_medias_edit_modal.html`）
- 三处调整：
  - 产品主图显示框由 aspect-ratio 9/16 改为 400×400 square（实际上限受列宽约束）
  - 其他字段、校验、提交逻辑完全不变

## 4. 编辑入口（新 modal）

新文件：`web/templates/_medias_edit_detail_modal.html`，id=`editDetailMask`。由列表"编辑"操作触发。

### 4.1 布局（从上到下）

1. **基本信息行**
   - 左：产品名称（必填）
   - 右：产品 ID（必填，slug）
2. **产品主图**
   - 400×400 显示框 + 右侧两个按钮：`更换` / `清空`
   - 无图时显示 dropzone
   - 可空；保存时不阻塞
3. **文案**（网格）
   - 每卡：单 textarea + 右上角"删除"
   - `grid-template-columns: repeat(auto-fill, minmax(260px, 1fr))`
   - `+ 添加文案` 追加一卡
4. **视频素材**（网格）
   - 每卡：缩略图（16:9）+ 文件名 + 右上角"删除"
   - `grid-template-columns: repeat(auto-fill, minmax(180px, 1fr))`
   - `+ 上传视频` 单文件入口
5. **底部按钮**：取消 / 保存

### 4.2 交互

- 打开方式：列表点"编辑"或产品名称，拉取 `/api/products/<pid>` 回填
- 主图独立流程：点"更换"走封面 bootstrap/complete；点"清空"本地清除并标记 `_clearCover=true`，保存时后端接收 `cover_object_key=null`
- 视频上传走现有 `items/bootstrap+complete`（单文件）
- 保存：`PUT /api/products/<pid>` 带 `name / product_code / cover_object_key / copywritings`；校验仅"名称+产品 ID+≥1 视频"

## 5. 后端改动

### 5.1 `web/routes/medias.py`
- `api_update_product`：移除"封面必填"硬校验，仅保留 slug + "≥1 视频素材"
- `api_update_product` 支持 `cover_object_key: null` 显式清空（`update_product(cover_object_key=None)`）。需要 DAO 白名单支持 NULL 覆盖——`update_product` 目前已经 `fields[k]`，NULL 即写入 NULL，无需改
- `api_list_products` 响应行增加 `items_filenames: [..]` 字段（最多 5 条，按 `sort_order, id`）
- 无需新增页面路由（编辑不跳转）

### 5.2 `appcore/medias.py`
- 新增 `list_item_filenames_by_product(product_ids, limit_per=5) -> dict[int, list[str]]`

## 6. 前端改动

- `web/static/medias.js`
  - 渲染函数 `renderGrid` → `renderTable`（新 DOM 和事件）
  - `openEdit(pid)` 原调用改为 `openEditDetail(pid)`，展示新 modal（不再是 create modal）
  - 新增 `_medias_edit_detail_modal.html` 对应的 DOM id 前缀：`ed*`（如 `edName`, `edCode`, `edCoverBox`, `edCoverImg`, `edCwList`, `edItemsGrid`, `edSaveBtn`, `edCancelBtn`, `edMask` 等），和 create modal 命名完全分离避免冲突
  - 现有 create modal 的 DOM id 保持不变
- `web/templates/_medias_edit_modal.html`（create modal）：仅把封面 `.oc-cover-box` 的 `aspect-ratio:9/16` 改为 400×400 正方形（`width: 400px; height: 400px`，媒体查询 < 768 自适应）

## 7. 非目标

- 不做表头固定 / 列排序 / 列宽拖拽
- 不做文件名列的"展开/折叠"交互（>5 条只显示前 5 + "+N 更多"，无展开）
- 不做编辑页的字段校验红边（沿用 alert）
- 不做产品的批量操作
