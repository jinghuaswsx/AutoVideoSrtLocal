# 素材管理单语种商品链接检测设计文档

**日期**: 2026-04-19  
**作者**: Codex  
**关联模块**: 素材管理编辑弹窗 `medias`、链接检测 `link_check`、任务持久化 `projects`

## 1. 背景

当前项目已经有一套独立的“链接检测”能力：

- `web/routes/link_check.py` 提供创建任务、查询任务、预览图片的接口
- `appcore/link_check_runtime.py` 已经能抓取商品链接页面、下载图片、对比参考图，并输出整体结论与逐图结果
- `web/static/link_check.js` 已经定义了现成的结果结构和摘要字段

同时，素材管理编辑页也已经具备发起检测所需的全部要素：

- 当前编辑的产品
- 当前激活的语种 `activeLang`
- 当前语种对应的商品链接
- 当前语种对应的产品主图和详情图

缺失的只是把这两部分打通，并把检测任务与“产品 + 语种”建立稳定关联。现在用户必须跳去独立的 `/link-check` 页面手工上传参考图，素材页关闭后也无法继续追踪这一语种的最近检测结果，这不符合素材管理的实际工作流。

## 2. 目标

在素材管理编辑弹窗的单语种页面中，为“商品链接”增加内嵌的“链接检测”能力，做到：

1. 不再要求用户手动上传参考图
2. 直接复用现有 `link_check` 任务与判断逻辑
3. 自动使用当前语种页面已有的参考图片作为检测输入
4. 把最近一次检测任务稳定关联到当前产品的当前语种
5. 在编辑页内展示检测进度、摘要结果，并可查看详细结果
6. 页面刷新、重新打开弹窗后，仍能恢复最近一次检测状态

## 3. 范围

### 本次必须完成

- 在编辑弹窗单语种页的商品链接输入框尾部增加“链接检测”按钮
- 新增素材管理专用链接检测接口，内部复用现有 `create_link_check` + `link_check_runner.start(...)`
- 后端自动收集当前语种参考图，不让前端拼接文件上传
- 把最近一次检测任务元信息按语种保存到产品记录中
- 把 `link_check` 任务持久化到 `projects`，保证详情可恢复
- 在编辑页内展示检测状态、摘要和“查看详情”
- 补齐前后端测试

### 本次不做

- 在素材页中重写一套新的图片审查逻辑
- 支持一次同时检测多个语种
- 支持用户自选参考图范围或手动增删本次检测参考图
- 把完整逐图结果复制保存到产品表中
- 在素材页内重做一整套独立的详情弹窗样式系统

## 4. 核心方案

### 4.1 入口与交互

在编辑弹窗单语种页面的“商品链接”输入框右侧增加检测操作区：

- 一个主按钮：`链接检测`
- 一个状态标签：`未检测 / 检测中 / 通过 / 待复核 / 失败`
- 一个结果摘要区，位于商品链接输入框下方

交互规则：

1. 用户停留在某个语种页时，按钮只作用于当前 `activeLang`
2. 点击按钮后，前端先把当前输入框值 flush 到 `localized_links`
3. 若当前语种没有有效链接，阻止发起并提示“请先填写商品链接”
4. 发起后按钮进入 loading，状态变为“检测中”
5. 前端轮询关联任务，持续刷新状态与摘要
6. 任务完成后展示摘要结果，并提供“查看详情”操作
7. 重新打开编辑弹窗时，自动读取该语种最近一次关联任务并恢复摘要

### 4.2 参考图来源

素材页不再要求手工上传参考图，后端自动按当前产品和当前语种构建参考图列表：

1. 优先加入当前语种的产品主图（若存在）
2. 再加入当前语种的产品详情图，按 `sort_order` 顺序追加

这是本次方案的唯一参考图来源，不额外混入 EN 图或其他语种图片。

这样做的原因：

- 用户明确要求“这个页面有语言，有链接，有参考图片，所有要素齐全了”
- 当前语种页的数据和目标页面最一致
- 能最大程度复用现有 `link_check` 的“参考图匹配 -> 二值快检 -> LLM 复核”流程

若当前语种完全没有参考图：

- 后端仍允许创建任务，但会返回 400，提示“当前语种缺少参考图，至少需要主图或详情图之一”
- 前端展示错误态，不进入轮询

### 4.3 任务关联方式

仅靠前端记住一个临时 `task_id` 不足以满足“关联好对应任务”。本次采用“双层关联”：

1. `link_check` 任务本身写入 `projects`
2. `media_products` 额外存每个语种最近一次链接检测的元信息

新增 `media_products.link_check_tasks_json` 字段，结构如下：

```json
{
  "de": {
    "task_id": "uuid",
    "status": "review_ready",
    "link_url": "https://newjoyloo.com/de/products/demo",
    "checked_at": "2026-04-19T22:10:00",
    "summary": {
      "overall_decision": "unfinished",
      "pass_count": 3,
      "replace_count": 1,
      "review_count": 0
    }
  }
}
```

字段约定：

- `task_id`: 当前产品该语种最近一次检测任务 ID
- `status`: 最近一次任务状态，取值沿用 `link_check` 任务状态
- `link_url`: 发起检测时使用的链接，便于判断输入框是否已变更
- `checked_at`: 最近一次发起或完成检测的时间
- `summary`: 仅保存编辑页摘要渲染所需的轻量字段

不把完整逐图结果塞进这个 JSON，完整明细仍以 `projects.state_json` 为准。

### 4.4 link_check 任务持久化

当前 `appcore.task_state.create_link_check(...)` 中设置了 `_persist_state = False`，导致该任务不会同步到 `projects`。这会让页面刷新后无法恢复。

本次改为：

- `create_link_check(...)` 创建任务时直接 `_db_upsert(...)`
- 删除 `_persist_state = False`
- 后续 `task_state.update(...)` 可以持续把 `status`、`progress`、`summary`、`items` 同步到 `projects.state_json`

这样带来的效果：

1. 素材页和独立 `/link-check` 页面都能受益
2. 任务详情在进程重启或弹窗关闭后仍可恢复
3. 素材页保存的 `task_id` 可以长期指向一个可查询的真实任务

### 4.5 后端接口设计

新增素材管理专用接口：

#### POST `/medias/api/products/<pid>/link-check`

用途：

- 按当前产品 + 当前语种创建一个链接检测任务
- 自动收集参考图
- 记录语种级任务关联
- 启动后台线程

请求体：

```json
{
  "lang": "de",
  "link_url": "https://newjoyloo.com/de/products/demo"
}
```

返回体：

```json
{
  "task_id": "uuid",
  "status": "queued",
  "reference_count": 4
}
```

处理流程：

1. 校验产品存在且当前用户可访问
2. 校验语种合法且非空
3. 校验 `link_url` 非空且看起来是 `http/https`
4. 收集该产品该语种主图与详情图
5. 若没有任何参考图，返回 400
6. 创建 `link_check` 任务并持久化
7. 把任务元信息写入 `media_products.link_check_tasks_json[lang]`
8. 启动 `link_check_runner`
9. 返回 `task_id`

#### GET `/medias/api/products/<pid>/link-check/<lang>`

用途：

- 读取当前产品当前语种最近一次关联任务
- 直接返回编辑页需要的摘要视图

返回体包含：

- 关联元信息
- 任务当前状态
- 任务摘要
- 是否存在详情可查看

若该语种从未检测，返回：

```json
{
  "task": null
}
```

#### GET `/medias/api/products/<pid>/link-check/<lang>/detail`

用途：

- 读取最近一次关联任务的完整详情
- 实际内部查询 `task_id` 对应的 `link_check` 任务
- 返回结构尽量复用现有 `/api/link-check/tasks/<task_id>` 的序列化格式

这样素材页不需要自己知道底层 `task_id` 的拼接和归属校验逻辑。

### 4.6 产品序列化返回

`GET /medias/api/products/<pid>` 目前已经返回：

- `product.localized_links`
- `covers`
- `items`
- `copywritings`

本次补充返回：

- `product.link_check_tasks`

格式为 `dict {lang: {...}}`，由 `link_check_tasks_json` 反序列化得到。

前端打开编辑弹窗时，就能立即知道当前语种有没有最近检测记录，无需先额外触发一次列表查询。

## 5. 前端设计

### 5.1 组件位置

在 [G:\Code\AutoVideoSrt\.worktrees\material-editor-link-check\web\templates\_medias_edit_detail_modal.html](G:\Code\AutoVideoSrt\.worktrees\material-editor-link-check\web\templates\_medias_edit_detail_modal.html) 的商品链接字段区域内增加：

- 检测按钮
- 状态 badge
- 结果摘要容器

布局要求：

- 保持现有 Ocean Blue Admin 风格
- 不引入紫色
- 颜色和尺寸继续走现有 token
- 状态展示优先轻量，不要把商品链接区做成第二个大面板

建议结构：

```text
商品链接 [input..................................] [链接检测按钮] [状态]
说明/默认链接 hint
最近检测摘要
```

### 5.2 状态与三态

前端必须覆盖以下状态：

- empty: 该语种从未检测
- loading: 正在发起任务或轮询中
- success: 最近一次任务完成且整体结论为 `done`
- review: 最近一次任务结束但整体结论为 `unfinished` 或任务状态为 `review_ready`
- error: 最近一次任务发起失败或任务状态为 `failed`

摘要区显示内容：

- 最近检测时间
- 检测链接
- 抓取图片总数
- 通过数
- 待替换数
- 待复核数
- 一个“查看详情”按钮

### 5.3 轮询策略

前端新增一套编辑页内部的轮询控制器：

1. 发起检测后开始轮询最近关联任务
2. 轮询周期 1 秒
3. 遇到终态停止轮询：`done / review_ready / failed`
4. 切换语种、关闭弹窗时清理定时器

轮询基于当前产品 + 当前语种，而不是直接基于一个裸 `task_id`。这样切语种时逻辑更清晰，也更符合页面数据模型。

## 6. 错误处理

### 6.1 发起前错误

- 商品链接为空：前端直接拦截
- 当前语种没有参考图：后端返回 400
- 产品不存在或无权限：返回 404
- 语种非法：返回 400

### 6.2 任务执行错误

沿用现有 `link_check` 任务语义：

- 页面抓取失败
- 语种锁定失败
- 图片下载失败
- 某张图片分析失败

编辑页只展示任务级错误摘要；逐图错误留在“查看详情”里看。

### 6.3 链接变更后的提示

若当前输入框中的链接与 `link_check_tasks_json[lang].link_url` 不一致：

- 仍展示上一次检测结果
- 但在摘要顶部加一条提示：`当前链接已修改，下面是旧链接的检测结果，建议重新检测`

这样能避免用户误把旧结果当成新链接结果。

## 7. 数据与代码改动

### 7.1 数据库

新增迁移：

- `db/migrations/2026_04_19_media_products_link_check_tasks.sql`

内容：

```sql
ALTER TABLE media_products
  ADD COLUMN link_check_tasks_json JSON NULL COMMENT '按语种保存最近一次链接检测任务摘要 {lang: {...}}';
```

### 7.2 后端

重点修改文件：

- `appcore/task_state.py`
- `appcore/medias.py`
- `web/routes/medias.py`
- 可能新增一个轻量 helper，用于拼装参考图列表

职责调整：

- `appcore/task_state.py`: 让 `link_check` 进入 `projects` 持久化
- `appcore/medias.py`: 提供 `link_check_tasks_json` 读写支持
- `web/routes/medias.py`: 暴露素材页专用创建、查询、详情接口，并在产品序列化中返回 `link_check_tasks`

### 7.3 前端

重点修改文件：

- `web/templates/_medias_edit_detail_modal.html`
- `web/static/medias.js`

新增逻辑：

- 商品链接检测按钮与状态容器
- 当前语种检测摘要渲染
- 发起检测
- 轮询关联任务
- 查看详情

“查看详情”不新开独立页面，优先在当前编辑弹窗内使用一个轻量二级弹层展示结果摘要和逐图明细；内部数据字段复用现有 `link_check` 任务结构。

## 8. 测试策略

### 8.1 后端测试

新增或修改测试覆盖：

- `tests/test_link_check_routes.py`
  - `create_link_check` 会持久化到 `projects`
- `tests/test_appcore_task_state.py`
  - `create_link_check` 默认可持久化
- `tests/test_web_routes.py`
  - 素材编辑页 HTML 包含“链接检测”入口
- 新增 `tests/test_medias_link_check_routes.py`
  - 创建素材页链接检测任务会自动组装参考图
  - 无参考图时报错
  - 查询最近关联任务成功
  - 非本人产品访问被拒绝

### 8.2 前端静态测试

新增或修改：

- 检查模板中存在 `edProductLinkCheckBtn`、状态容器、摘要容器
- 检查 `medias.js` 中存在语种级轮询清理逻辑
- 检查“当前链接已修改”提示文案逻辑存在

### 8.3 手工验证

至少验证以下场景：

1. 当前语种有主图和详情图，点击检测，能成功跑完并回显摘要
2. 当前语种只有主图，没有详情图，也能发起检测
3. 当前语种没有任何参考图，会被阻止并提示
4. 修改商品链接后，旧摘要仍显示但会提示结果已过期
5. 关闭再打开编辑弹窗，最近检测结果仍可恢复
6. 切换语种时，状态和摘要跟着语种切换，不串数据

## 9. 风险与约束

### 9.1 projects.state_json 体积增加

`link_check` 会存 `items` 明细和部分分析结果，体积会比很多任务大。但这是现有独立页面已经在内存里持有的数据，并且本次只把真实任务状态同步进 `projects`，没有重复保存完整结果到产品表，所以可接受。

### 9.2 当前语种参考图不足

有些语种可能只有主图没有详情图，检测覆盖面会弱于完整参考图组，但仍优于不带参考图直接检测。当前方案允许部分参考图存在，只禁止“一个都没有”。

### 9.3 编辑页复杂度上升

`medias.js` 已经较大，本次新增逻辑需要尽量以内聚 helper 的形式组织，避免把链接检测逻辑散落在各个事件回调里。

## 10. 最终决策

本功能采用“素材页发起、link_check 复用、任务持久化、产品语种级关联”的方案。

具体结论如下：

1. 不新增手动上传参考图入口
2. 默认使用当前语种已有主图和详情图作为参考图
3. `link_check` 任务改为持久化到 `projects`
4. `media_products.link_check_tasks_json` 只保存最近一次关联任务的轻量摘要
5. 编辑页内直接展示状态、摘要和详情入口
6. 所有实现都只在独立 worktree `codex/material-editor-link-check` 中完成
