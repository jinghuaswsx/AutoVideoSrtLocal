# 素材管理 · 单页添加/编辑弹窗 重构设计

> 状态：已与用户确认需求，待实现计划
> 涉及模块：web/templates/_medias_edit_modal.html · web/static/medias.js · web/routes/medias.py · appcore/medias.py · DB 迁移

## 1. 背景与目标

当前"添加/编辑产品素材"弹窗使用三段 Tab（基本信息 / 文案 / 视频素材），用户反馈操作流被切断，希望"一个页面全部搞定"。同时精简字段、明确必填项、约束上传行为。

**目标**
- 取消 Tab，改为单页垂直滚动布局
- 精简表单字段至 5 项，其中 3 项必填
- 新增"产品 ID（slug）"业务字段，带唯一约束
- 新增"封面图"独立上传
- 视频上传改为"每次一个"，且保存时至少 1 条
- 移除旧的 `色号/代言人` 与 `来源` UI 入口（列保留）

## 2. 表单字段

| 字段 | 必填 | 存储列 | 规则 |
|---|---|---|---|
| 产品名称 | ✅ | `media_products.name` | 最长 120 字符 |
| 产品 ID | ✅ | `media_products.product_code` (新增 VARCHAR(64) UNIQUE) | 正则 `^[a-z0-9][a-z0-9-]{1,62}[a-z0-9]$`；全库唯一；保存冲突返回 409 |
| 封面图 | ✅ | `media_products.cover_object_key` (新增 VARCHAR(255))、`media_products.cover_url` (新增 TEXT) | 单张图片；复用 TOS，独立 bootstrap/complete；`image/*` |
| 视频素材 | ✅ ≥ 1 | `media_items` 行 | 单次 input 只接收 1 个文件；保存时后端校验 `COUNT(items) >= 1` |
| 文案 | ❌ | `media_copywritings` 多行 | 沿用现有多条结构，可增删 |

**被移除的 UI 字段**：`color_people`（色号/代言人）、`source`（来源）。数据库列保留，不做清理；前端不再读写。

## 3. 数据库迁移

```sql
ALTER TABLE media_products
  ADD COLUMN product_code    VARCHAR(64)  NULL AFTER name,
  ADD COLUMN cover_object_key VARCHAR(255) NULL AFTER source,
  ADD COLUMN cover_url        TEXT         NULL AFTER cover_object_key,
  ADD UNIQUE KEY uk_media_products_product_code (product_code);
```

- `product_code` 允许 NULL（老数据过渡期），但新增/编辑保存路径强制要求非空。
- 唯一索引在列为 NULL 时不冲突，老数据可安全共存。
- 迁移脚本放到项目现有迁移目录（按 AutoVideoSrt 约定，后续 plan 阶段确认路径）。

## 4. 后端改动

### 4.1 appcore/medias.py
- `create_product` 签名追加 `product_code`、`cover_object_key`、`cover_url`；SQL 同步扩展。
- `update_product` 的 `allowed` 白名单追加 `product_code`、`cover_object_key`、`cover_url`。
- 新增 `get_product_by_code(code) -> dict | None`，用于唯一性冲突检查（可选，亦可靠数据库唯一索引异常反馈）。

### 4.2 web/routes/medias.py
- 产品 `POST /medias/api/products` 与 `PUT /medias/api/products/<id>`：
  - 接收并写入新字段。
  - 校验 `product_code` 正则；若已存在（或 `IntegrityError`）返回 `409 {"error": "产品 ID 已被占用"}`。
- **保存校验**（在 `PUT` 路径，前端"保存"按钮最终落点）：
  - `cover_object_key` 为空 → 400。
  - `media_items` 下无任何有效行 → 400 `{"error":"至少需要 1 条视频素材"}`。
- **新增封面上传接口**：
  - `POST /medias/api/products/<pid>/cover/bootstrap` → 返回 `{ upload_url, object_key }`（与 item bootstrap 平行，key 前缀改为 `covers/`）。
  - `POST /medias/api/products/<pid>/cover/complete` → body `{ object_key }` → 更新 `cover_object_key` 与 `cover_url`，返回 `{ cover_url }`。
- `GET /medias/api/products/<pid>`：响应 `product` 块新增 `product_code`、`cover_url`。
- 列表接口：响应行追加 `cover_url` 字段（列表卡片优先使用它，见 §5.3）。

### 4.3 "未填名称即创建"的兜底
当前 `ensureProductIdForUpload` 在上传视频前如果还没产品 id 就用 name 先建一条产品记录。改版后 `product_code` 是必填：
- 前端在允许触发封面/视频上传前，先校验名称与 product_code 均已填好且格式合法，否则提示并阻止上传。
- 服务端 `create_product` 强制要求 `product_code`；未提供返回 400。

## 5. 前端改动

### 5.1 _medias_edit_modal.html（结构重写）
- 删除 `.oc-tabs` 与三个 `.oc-panel`。
- `oc-modal-body` 内改为垂直堆叠区块（区块间 `--oc-sp-6`）：

  1. **基本信息区**：两列栅格 `[产品名称*][产品 ID*]`，产品 ID 下方预留一行微提示："只能用小写字母、数字和连字符，如 `sonic-lens-refresher`"。
  2. **封面图区**（必填）：
     - 无图：`.oc-cover-dropzone`（160×90 缩略比例，圆角，虚线边框，文案"点击或拖拽上传封面图"）。
     - 有图：预览缩略 + `更换` 与 `删除` 两个小按钮。
     - `<input type="file" accept="image/*" hidden>`。
  3. **视频素材区**（必填 ≥1）：
     - 顶部：标题 + 计数徽标。
     - `.oc-dropzone`（复用现样式）文案改为"点击或拖拽上传 1 个视频素材"；`<input>` **去掉 `multiple`**。
     - 进度行 + 已上传缩略网格（沿用现有 `renderItems`）。
  4. **文案区**（可选）：
     - 标题行 `文案 (可选)`＋`+ 添加文案` 按钮。
     - 列表 `#cwList` 与现有 `cwCard` 结构一致。

- 底部仍为 `取消 / 保存`。

### 5.2 web/static/medias.js
- 删除 `switchTab` 相关逻辑（所有调用点删除）。
- 状态扩展：`state.current.product.cover_url / product_code / cover_object_key`。
- `openEdit(pid)` / `openCreate()`：初始化新字段，移除对 `mColor`、`mSource` 的读写。
- 新增 `uploadCover(file)`：调用 cover bootstrap → PUT → complete，成功后刷新预览。
- `uploadFiles(files)` → 改为 `uploadVideo(file)`（单文件）；input `change` 监听取 `files[0]`，拖拽取第一个。
- `save()`：
  - 前端校验顺序：`name` 必填 → `product_code` 格式 → 封面已设置 → `document.querySelectorAll('.oc-item').length >= 1`；任一失败 `alert` 并聚焦相应字段。
  - PUT payload 追加 `product_code`、`cover_object_key`、`cover_url`。
  - 接收 409 时：`alert('产品 ID 已被占用')` 并聚焦 `product_code` 输入。
- `ensureProductIdForUpload`：
  - 触发条件改为"进入封面或视频上传时"；
  - 要求名称与 `product_code` 均填好才创建；创建 payload 带 `product_code`；
  - 创建失败（409）统一走上面的提示。

### 5.3 medias_list.html 卡片封面
- `cardHTML`：`p.cover_thumbnail_url` 的取值后端变为"优先 `product.cover_url`，其次首个 item 的 thumbnail"（后端处理；前端不用改渲染逻辑）。

### 5.4 样式
- 新增 `.oc-cover-dropzone` 与 `.oc-cover-preview` 两套样式，风格与 `.oc-dropzone` 一致（Ocean Blue tokens，大圆角，虚线边框，hover 态切换到 accent）。
- 保留现有 `.oc-dropzone`、`.oc-items-grid`、`.oc-cw-list` 样式不动。
- 全部使用现有 `--oc-*` token，不引入新色值，严格零紫色。

## 6. 三态与交互

- **空态**（create）：封面区显示虚线 dropzone；视频区显示 dropzone+提示；文案区为空。
- **加载态**：弹窗内部无独立 skeleton；依赖按钮禁用与进度条。
- **错误态**：
  - 产品 ID 格式错误：输入失焦时红边提示文案（或保存时 alert，MVP 先走 alert）。
  - 409 冲突：alert + focus。
  - 上传失败：沿用现有 `.oc-upload-row.err` 行。
- **键盘可达**：Tab 顺序为 名称 → 产品 ID → 封面上传 → 视频上传 → 文案 → 取消/保存；Esc 关闭。

## 7. 验收清单

- [ ] 新建时，未填 3 个必填项中任一，保存被拦下并聚焦
- [ ] 产品 ID 可识别合法 slug；非法串（大写、下划线、首尾连字符）被拦下
- [ ] 复用已存在的 product_code 保存时得到 409 并提示
- [ ] 封面上传走 TOS 后正确落入 `covers/` 前缀
- [ ] 视频 input 无 `multiple`，拖拽多个时仅采用第一个
- [ ] 保存时无视频素材 → 后端 400；有 1 条即通过
- [ ] 编辑已有产品：表单回填 `product_code` 与封面；可继续追加多个视频
- [ ] 列表卡片封面优先使用 `product.cover_url`
- [ ] 弹窗内无紫色/靛蓝；hue 全在 200–240；无 emoji
- [ ] 移动端（<768）单列；弹窗可滚动

## 8. 非目标（YAGNI）

- 不做 `color_people`/`source` 的数据清理或迁移。
- 不做 `product_code` 的批量补填脚本（老数据保持 NULL，下次编辑时再强制填写）。
- 不做产品封面的自动裁剪/水印。
- 不做产品 ID 的自动生成或建议。
