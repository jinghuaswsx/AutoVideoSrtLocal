# 素材管理模块 设计文档

**日期**：2026-04-13
**作者**：noobird + Claude
**参考**：明空网络系统 `/marketing/medias`

## 背景

当前 AutoVideoSrt 各业务模块（视频翻译、文案创作、视频创作等）各自独立处理素材，缺少一个以「产品」为维度、统一存放产品素材视频和对应文案的库。用户需要一个素材管理模块，用于：

- 以产品为维度归类视频素材
- 给产品挂接文案（标题、正文、描述、广告信息）
- 为后续业务模块（视频创作、投放）提供素材来源

## 范围

### 首版要做

- 侧边栏新增一级菜单「📦 素材管理」，路由 `/medias`
- 以产品为维度的列表页（分页、关键词搜索、已归档开关）
- 「添加产品素材」按钮：新建产品 + 上传视频素材
- 行末「编辑」按钮：居中弹窗编辑产品文案、广告信息、素材列表
- 视频素材走 **TOS 直传**（复用现有 `tos_upload.py` 的 bootstrap/complete 模式）
- 素材自动抽取第 1 帧作为缩略图
- 权限：普通用户只能看自己上传的；管理员可通过 `?scope=all` 看全部

### 首版不做（预留但不实现 UI）

- 重要程度 / 趋势评分 / 卖点信息 → 建数据库列，UI 不展示
- 投放次数统计 → 建列，UI 不展示
- 拖拽排序 → 用 `sort_order` 字段，但首版按创建时间排序
- 星级筛选、类型筛选（明空截图里的下拉）

### 明确不做

- 导出 / 导入 / 批量操作
- 客服触达、办公学院等明空的其他模块

## 数据模型

### 新增 3 张表

```sql
CREATE TABLE media_products (
  id INT AUTO_INCREMENT PRIMARY KEY,
  user_id INT NOT NULL,
  name VARCHAR(255) NOT NULL,
  color_people VARCHAR(64) DEFAULT NULL,         -- "色号人" 自由文本
  source VARCHAR(64) DEFAULT NULL,               -- 来源标签（如"运营创意"）
  importance TINYINT DEFAULT NULL,               -- 预留：重要程度 1-5，UI 不展示
  trend_score TINYINT DEFAULT NULL,              -- 预留：趋势评分 1-5，UI 不展示
  selling_points TEXT DEFAULT NULL,              -- 预留：卖点信息，UI 不展示
  archived TINYINT(1) NOT NULL DEFAULT 0,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  deleted_at DATETIME DEFAULT NULL,
  KEY idx_user_deleted (user_id, deleted_at),
  KEY idx_name (name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE media_copywritings (
  id INT AUTO_INCREMENT PRIMARY KEY,
  product_id INT NOT NULL,
  idx INT NOT NULL DEFAULT 1,                    -- #1 #2 排序
  title VARCHAR(500) DEFAULT NULL,
  body TEXT DEFAULT NULL,
  description VARCHAR(500) DEFAULT NULL,
  ad_carrier VARCHAR(255) DEFAULT NULL,          -- 广告媒体库
  ad_copy TEXT DEFAULT NULL,                     -- 广告文案
  ad_keywords VARCHAR(500) DEFAULT NULL,         -- 广告词
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  KEY idx_product_idx (product_id, idx)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE media_items (
  id INT AUTO_INCREMENT PRIMARY KEY,
  product_id INT NOT NULL,
  user_id INT NOT NULL,
  filename VARCHAR(500) NOT NULL,                -- 原始文件名
  display_name VARCHAR(255) DEFAULT NULL,
  object_key VARCHAR(500) NOT NULL,              -- TOS 对象键
  file_url VARCHAR(1000) DEFAULT NULL,           -- 可访问 URL（签名或公开）
  thumbnail_path VARCHAR(500) DEFAULT NULL,      -- 本地缩略图相对路径
  duration_seconds FLOAT DEFAULT NULL,
  file_size BIGINT DEFAULT NULL,
  play_count INT NOT NULL DEFAULT 0,             -- 预留：投放次数
  sort_order INT NOT NULL DEFAULT 0,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  deleted_at DATETIME DEFAULT NULL,
  KEY idx_product_deleted (product_id, deleted_at),
  KEY idx_user (user_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
```

迁移文件：`db/migrations/2026_04_13_add_medias_tables.sql`

## 架构

### 分层

```
web/routes/medias.py        — 蓝图：页面路由 + JSON API
web/services/medias.py      — 业务逻辑：查询、创建、上传回调处理
appcore/tos_clients.py      — 扩展：build_media_object_key()
pipeline/ffutil.py          — 复用：抽取缩略图（已有 ffmpeg 工具则直接调）
web/templates/medias_list.html   — 列表页
web/templates/_medias_edit_modal.html — 编辑弹窗（include）
```

### 路由表

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/medias` | 列表页（HTML） |
| GET | `/medias/api/products` | JSON 列表（支持 `keyword`、`archived`、`page`、`scope=all`） |
| POST | `/medias/api/products` | 创建产品（返回 product_id） |
| GET | `/medias/api/products/<id>` | 详情（含文案、素材） |
| PUT | `/medias/api/products/<id>` | 更新产品 + 文案（整体替换文案列表） |
| DELETE | `/medias/api/products/<id>` | 软删（设 deleted_at） |
| POST | `/medias/api/products/<id>/items/bootstrap` | 获取 TOS 直传签名 URL |
| POST | `/medias/api/products/<id>/items/complete` | 上传完成回调，抽缩略图入库 |
| DELETE | `/medias/api/items/<id>` | 软删素材 |

所有接口：`@login_required`；写接口校验 `product.user_id == current_user.id` 或 admin。

### 上传流程

1. 前端选择文件 → 调 `/api/products/<id>/items/bootstrap` 获得 `upload_url + object_key`
2. 前端 `PUT` 到 TOS signed URL
3. 前端调 `/items/complete`（传 `object_key, filename, file_size`）
4. 后端：
   - 通过 TOS SDK 下载到临时文件（或用 HEAD 拿 metadata）
   - ffmpeg 抽第 1 帧到 `output/media_thumbs/<product_id>/<item_id>.jpg`
   - 读取时长（ffprobe）
   - 写入 `media_items` 表

### TOS 桶与对象键约定

**独立桶：`video-save`**（与现有 `TOS_BUCKET=auto-video-srt` 分离）

新增配置项（`config.py`）：
```python
TOS_MEDIA_BUCKET = _env("TOS_MEDIA_BUCKET", "video-save")
```
`.env.example` 同步新增一行。

Access key / secret / region / endpoint **复用现有 TOS 配置**（假定两个桶在同一账号、同一 region）。如果 region 不同，后续再拆 `TOS_MEDIA_REGION` / `TOS_MEDIA_ENDPOINT`，首版不做。

**`appcore/tos_clients.py` 扩展**：
- 新增 `build_media_object_key(user_id, product_id, filename) -> str`
- 新增 `generate_signed_media_upload_url(object_key) -> str`（内部调用 `get_public_client().pre_signed_url(bucket=TOS_MEDIA_BUCKET, ...)`）
- 新增 `generate_signed_media_download_url(object_key) -> str`
- 新增 `is_media_bucket_configured() -> bool`（额外校验 `TOS_MEDIA_BUCKET` 非空）

对象键约定：
```
{user_id}/medias/{product_id}/{yyyymmdd}_{uuid}_{filename}
```
（桶已隔离，键不再加 bucket 前缀）

**前端**：bootstrap 接口返回 `bucket=video-save`，前端直传到该桶。

## 前端

### 列表页 `medias_list.html`

顶部工具栏：
- 关键词搜索框 + 「搜索」按钮
- 「已归档」复选框（默认不勾，只看未归档）
- 右侧「+ 添加产品素材」按钮

表格列（参考明空截图）：
- ID
- 产品名称（主标题）+ 色号人（副标题灰色小字）
- 素材数量（badge）
- 素材名称（最多展示前 3 条）
- 来源（标签 pill）
- 创建时间（`YY-MM-DD HH:mm` + 相对时间）
- 修改时间（同上 + 距今 X 天/月）
- 操作：「编辑」按钮

分页：底部居中数字分页。

### 添加产品素材（新建流程）

点击「+ 添加产品素材」：

1. 弹出一个小对话框：「产品名称」输入框 + 「色号人」输入框（可选）+ 「来源」输入框（可选） + 文件多选上传
2. 点「创建」：
   - 先调 `POST /api/products` 创建产品
   - 依次对每个文件走 bootstrap → TOS PUT → complete
   - 进度条显示
3. 完成后跳回列表并刷新

### 编辑弹窗 `_medias_edit_modal.html`

居中 Modal（宽 800px，最大高 90vh，内部可滚）：

- 标题「编辑素材」 + 右上角 ×
- 产品名称（输入框，必填）
- 色号人（输入框）
- 来源（输入框）
- 文案区（可添加/删除条目，每条包含）：
  - #N 标号
  - 标题 / 正文 / 描述
  - 广告媒体库 / 广告文案 / 广告词
  - 删除按钮
  - 「+ 添加文案条目」按钮
- 视频素材区：
  - 缩略图网格（缩略图 + 文件名 + × 删除）
  - 「+ 上传更多素材」按钮（多选）
- 底部：「保存」/「取消」

保存策略：
- 产品基础字段 + 文案列表 → `PUT /api/products/<id>`（文案整体替换）
- 素材增删是独立 API（上传/删除后即时生效，不随保存按钮走）

## 权限

| 角色 | 列表页 | 自己的产品 | 别人的产品 |
|---|---|---|---|
| 普通用户 | 仅看自己的 | 增删改查 | 不可见 |
| 管理员 | 默认看自己的；`?scope=all` 看全部 | 增删改查 | 只读查看 |

## 侧边栏

在 `layout.html` 侧边栏「视频评分」之后插入：

```html
<a href="/medias" {% if request.path.startswith('/medias') %}class="active"{% endif %}>
  <span class="nav-icon">📦</span> 素材管理
</a>
```

## 错误处理

- TOS 未配置（`TOS_ACCESS_KEY/SECRET` 缺失 或 `TOS_MEDIA_BUCKET` 为空）：`/medias` 页面顶部横幅提示「素材上传需要先配置 TOS，请联系管理员」，但页面仍可访问查看已有数据
- 上传失败（TOS 返回错误）：前端 toast + 保留断点（文件名/进度条标红），不提交 complete
- 缩略图抽取失败：不阻断入库，`thumbnail_path = null`，前端用占位图
- 产品删除时：软删 + 级联软删下属 items 和 copywritings（DELETE 接口内部事务处理）

## 测试计划

- `tests/web/test_medias_routes.py`：
  - 创建/编辑/删除产品（本人 vs 他人 vs admin）
  - 列表过滤（keyword, archived, scope）
  - 上传 bootstrap/complete 流程（mock TOS）
- `tests/appcore/test_medias_service.py`：
  - 文案整体替换逻辑（新增/修改/删除条目）
  - 软删级联

## 验证命令

- 数据库迁移：`python -c "import pymysql; ..."`（或手动 mysql CLI 跑 SQL）
- 单元测试：`pytest tests/web/test_medias_routes.py tests/appcore/test_medias_service.py -v`
- 手工冒烟：
  1. 登录 → 侧边栏看到「素材管理」
  2. 点击 → 空列表
  3. 点「+ 添加产品素材」→ 填写 + 选 1 个 mp4 → 上传成功
  4. 列表出现 1 行 → 点「编辑」→ 加一条文案 → 保存
  5. 刷新页面数据正确
  6. 切换管理员账号 → `?scope=all` 能看到其他用户的

## 实施拆分

1. **数据层**：迁移 SQL + appcore/medias.py（DAO）
2. **后端**：web/routes/medias.py 蓝图 + TOS 键扩展 + 缩略图生成 + app.py 注册
3. **前端**：列表页 + 编辑弹窗 + 侧边栏接入
4. **测试 + 冒烟 + commit**
