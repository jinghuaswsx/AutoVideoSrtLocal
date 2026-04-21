# 素材管理 · 原始去字幕素材（Raw Sources）设计

日期：2026-04-21
分支：`worktree-feature-medias-raw-sources`
相关模块：`medias`、`bulk_translate`、`image_translate`

## 1. 背景与目标

当前素材管理（`/medias`）每个产品下已有 5 类资源：素材视频（`media_items`，按语种分）、文案（`media_copywritings`）、主图（`media_product_covers`）、详情图（`media_product_detail_images`）。视频翻译 / 批量翻译（`bulk_translate`）直接以 `media_items WHERE lang='en'` 的英文素材作为翻译源。

用户希望在产品层面引入一种新的独立资源：**原始去字幕素材**——即用户在外部已处理好的、去字幕 + 去水印 + 去尾巴的英文原始视频。它**不是**现有英文素材的替代品，而是翻译流程的上游：以后翻译视频到其他语种时，输入源不再是英文 `media_items`，而是这份原始去字幕素材。

### 目标

- 新增一类产品级资源「原始去字幕素材」，和现有 5 类资源并存。
- 支持在产品层面上传、列表、改名、删除原始去字幕素材。
- 每条原始素材 = mp4 + 英文封面图，两者都必传。
- 改造翻译入口：点击产品列表行的「翻译」按钮时，弹出本产品的原始素材列表，用户勾选 N 条视频 × M 种目标语言 → 提交翻译；**原英文 `media_items` 不再作为翻译源**。
- 翻译产出物继续写入 `media_items`（各目标语种），同时通过图片翻译生成对应语种的封面，`media_items.cover_object_key` 带上翻译后的封面，保持「一套素材完整（视频 + 封面）」。

### 非目标

- 不迁移现有 44 个产品的老数据。老产品的英文 `media_items` 保留作为「英文成品素材」展示，不再参与翻译；用户会手动为需要跑新翻译的老产品补传原始去字幕素材。
- 不在本项目内实现「在系统内直接去字幕」；假设用户在系统外（或走现有 `subtitle_removal` 模块）已经得到了干净的 mp4，再上传进来。
- 不改变文案 / 主图 / 详情图三类资源的翻译链路（仍按语种批量翻译，和现在一致）。

## 2. 术语

- **原始去字幕素材（raw source）**：产品下一条独立的英文视频（+ 英文封面），已经过去字幕、去水印、去尾巴处理，可直接作为翻译的输入源。
- **英文成品素材**：`media_items WHERE lang='en'`，代表英文可发布的成品视频，不再进入翻译流程。
- **多语种成品**：`media_items WHERE lang IN ('de','fr','es','it','ja','pt')`，翻译产出物。

## 3. 数据模型

### 3.1 新表 `media_raw_sources`

```sql
CREATE TABLE media_raw_sources (
  id INT AUTO_INCREMENT PRIMARY KEY,
  product_id INT NOT NULL,
  user_id   INT NOT NULL,
  display_name     VARCHAR(255) DEFAULT NULL,
  video_object_key VARCHAR(500) NOT NULL,   -- TOS mp4
  cover_object_key VARCHAR(500) NOT NULL,   -- TOS 英文封面（必填）
  duration_seconds FLOAT  DEFAULT NULL,
  file_size        BIGINT DEFAULT NULL,
  width            INT    DEFAULT NULL,
  height           INT    DEFAULT NULL,
  sort_order       INT    NOT NULL DEFAULT 0,
  created_at       DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  deleted_at       DATETIME DEFAULT NULL,
  KEY idx_product_deleted (product_id, deleted_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
```

设计要点：
- **无 lang 维度**。原始素材语义上就是英文，不需要按语种区分。
- `cover_object_key` `NOT NULL`：封面必传。
- 软删除走 `deleted_at`，符合项目「medias 永久保存」硬规则。
- 不挂外键，与 `media_products` 保持最小依赖（与现有 `media_items` 对齐）。

### 3.2 `media_items` 扩展一列

```sql
ALTER TABLE media_items
  ADD COLUMN source_raw_id INT NULL AFTER cover_object_key,
  ADD KEY idx_source_raw (source_raw_id);
```

- 翻译产出的多语种 item 在 `source_raw_id` 上指回它的原始源，用于溯源、排查、未来的重跑场景。
- 现有英文 items 的 `source_raw_id` 保持 NULL。
- 迁移脚本：`db/migrations/2026_04_21_medias_raw_sources.sql`（表 + 列 + 索引一起上）。

### 3.3 TOS object_key 规范

- 视频：`media/<user_id>/<product_id>/raw_sources/<uuid>.mp4`
- 封面：`media/<user_id>/<product_id>/raw_sources/<uuid>.cover.<ext>`

复用 `tos_clients.build_media_object_key(...)` 的 bucket / 前缀规则，避免另起命名空间。

### 3.4 对象引用登记

`appcore.medias.collect_media_object_references()` 需要追加两项：
- `source: "raw_source_video"` → `video_object_key`
- `source: "raw_source_cover"` → `cover_object_key`

确保任何未来的 TOS 清理逻辑都能看到这些对象（实际上根据硬规则不会清理，但登记表要完整）。

## 4. UI 布局

### 4.1 产品列表行（`/medias` 表格）

- 新增「原始视频 (n)」按钮（位于现有「编辑 / 翻译」操作列中），n 为该产品下 `deleted_at IS NULL` 的原始素材数。
- 现有「翻译」按钮保留，但点击行为改造为打开「翻译弹窗」（见 4.3）。

### 4.2 原始视频抽屉（右侧 drawer，宽 480–560）

- 顶部：产品名 + 「上传素材」按钮。
- 列表：紧凑卡片 / 行，每条展示封面缩略图 + display_name（缺失时用文件名）+ 时长 + 文件大小 + 删除图标。
- 上传入口：点「上传素材」弹出二级小弹窗：
  - 视频 file input（必填，`.mp4` / `.mov`）
  - 封面 file input（必填，`.jpg` / `.jpeg` / `.png` / `.webp`）
  - display_name（可选，默认取视频文件名 stem 截断 64 字符）
  - 两个文件都选齐后才允许「提交」按钮可点。
- 空状态：居中图标 + 文案「还没有原始去字幕素材，上传第一条」+ 主按钮。
- 遵循 Ocean Blue token：`--radius-lg` 卡片、`--space-4` 间距、海洋蓝主色、无紫色。

### 4.3 翻译弹窗（居中 modal）

- 左列：该产品的原始素材列表，每条带复选框（默认全选），展示封面缩略图 + 名称 + 时长。
- 右列：目标语言多选（依据 `media_languages WHERE enabled=1 AND code <> 'en'`）。
- 底部：「提交翻译」按钮 + 预览文字「将生成 N × M = K 条多语种素材」。
- 勾选原始素材为 0 或目标语言为 0 → 提交按钮禁用。
- 提交成功后跳转到 `/tasks/<父任务 id>` 查看进度。

### 4.4 产品编辑页

- **不开新 tab**，编辑页结构保持不变。
- 编辑页内「英文素材」区块的语义更新为「英文成品素材」，仅用于展示 / 发布，不再和翻译链路耦合。
- 编辑页的翻译入口（如有）同步替换成新的翻译弹窗。

## 5. 后端接口

### 5.1 REST 路由

| Method | Path | 作用 |
|---|---|---|
| GET | `/medias/products/<pid>/raw-sources` | 列出原始素材 |
| POST | `/medias/products/<pid>/raw-sources` | 上传（视频 + 封面） |
| PATCH | `/medias/raw-sources/<rid>` | 改 `display_name` |
| DELETE | `/medias/raw-sources/<rid>` | 软删 |
| GET | `/medias/raw-sources/<rid>/video` | 签名 URL（预览） |
| GET | `/medias/raw-sources/<rid>/cover` | 签名 URL（缩略图） |
| POST | `/medias/products/<pid>/translate` | 提交翻译（替换旧逻辑） |

路由挂载在现有 `bp = Blueprint("medias", __name__, url_prefix="/medias")` 之下。

### 5.2 上传接口细节

- `multipart/form-data`，字段：`video`、`cover`、`display_name`（可选）。
- 校验：
  - 两个 file 都必须存在，缺任一 → 400 `{"error": "video and cover both required"}`。
  - MIME 白名单：视频 `video/mp4 / video/quicktime`；封面 `image/jpeg / image/png / image/webp`。
  - 大小上限：视频 ≤ 2GB（对齐现有 `media_items` 上传），封面 ≤ 15MB（对齐现有 `_MAX_IMAGE_BYTES`）。
- 流程：
  1. 生成 uuid。
  2. 上传视频到 TOS `media/<user_id>/<pid>/raw_sources/<uuid>.mp4`。
  3. 上传封面到 TOS `media/<user_id>/<pid>/raw_sources/<uuid>.cover.<ext>`。
  4. 用 `pipeline.ffutil.get_media_duration / probe_media_info` 抽视频时长 / 分辨率 / 大小。
  5. 写入 `media_raw_sources` 行。
  6. 返回新行（含签名 URL，便于前端立刻渲染缩略图）。
- 失败回滚：任一步骤失败（TOS 上传、ffprobe、入库），把已上传的 TOS 对象全部 `delete_media_object` 删掉，DB 不留半成品行。
- 并发保护：前端按钮提交期间 disable，后端无额外锁（可接受同一产品并发上传多条）。

### 5.3 DAO 层扩展（`appcore/medias.py`）

新增函数：
- `create_raw_source(product_id, user_id, *, display_name, video_object_key, cover_object_key, duration_seconds, file_size, width, height) -> int`
- `list_raw_sources(product_id) -> list[dict]`（默认过滤软删）
- `get_raw_source(rid) -> dict | None`（默认过滤软删）
- `update_raw_source(rid, **fields)`：白名单 `{display_name, sort_order}`
- `soft_delete_raw_source(rid) -> int`
- `count_raw_sources_by_product(product_ids) -> dict[int, int]`
- `collect_media_object_references()`：追加 video_object_key / cover_object_key 两类 source。

### 5.4 产品列表接口扩充

产品列表响应中，每行补一个 `raw_sources_count` 字段（复用 `count_raw_sources_by_product` 批量查询，避免 N+1）。

### 5.5 翻译入口

新接口 `POST /medias/products/<pid>/translate`：
```json
{
  "raw_ids": [123, 124],
  "target_langs": ["de", "fr", "ja"]
}
```
- 校验：`raw_ids` 和 `target_langs` 都必须非空；`raw_ids` 全部属于 pid 且未软删；`target_langs` 全部在 `media_languages enabled=1` 内且 ≠ 'en'。
- 生成 `bulk_translate` 父任务，返回父任务 id。
- 前端收到后跳转到任务页。

旧的翻译入口（若有独立路由）改向这里，或一并删除。

## 6. 翻译管线改造（bulk_translate）

### 6.1 plan 生成器（`appcore/bulk_translate_plan.py`）

入参扩展：
```python
def build_plan(
    product_id: int,
    target_langs: list[str],
    content_types: set[str],        # 现有
    raw_source_ids: list[int],      # 新增
) -> list[dict]:
```

- `video` kind 的源从 `SELECT id FROM media_items WHERE lang='en'` 改为 `SELECT id FROM media_raw_sources WHERE id IN (...) AND deleted_at IS NULL AND product_id=%s`。
- 每条原始素材 × 每个支持的 target_lang → 一条 plan item，ref 结构改为：
  ```python
  {"source_raw_id": <rid>}
  ```
- `detail` / `cover` / `copywriting` 三类 kind 的 plan 生成**完全不变**。
- 传入空 `raw_source_ids` 且 `content_types` 包含 `video` → 抛 ValueError（上层路由转 400）。

### 6.2 runtime（`appcore/bulk_translate_runtime.py`）

处理 `video` kind 的子任务时：
1. 从 `media_raw_sources` 取 `video_object_key` + `cover_object_key`。
2. 视频：沿用现有翻译流程（下载 → ASR → 翻译 → 配音 → 合成）。下载源改为 `video_object_key` 的签名 URL。
3. 封面：复用 `image_translate` 的 runtime（或其底层函数），对 `cover_object_key` 跑一次单图翻译，得到目标语言的新封面 object_key。
4. 产出写 `media_items`：
   ```python
   create_item(
       product_id=...,
       user_id=...,
       filename=<translated mp4 name>,
       object_key=<translated video object key>,
       cover_object_key=<translated cover object key>,
       duration_seconds=...,
       file_size=...,
       lang=<target_lang>,
   )
   # 额外 UPDATE 写 source_raw_id
   ```
- 现有直接走 en items → 翻译 → 写其他语种 items 的代码路径**整体废弃**。需要同步检查：
  - `runtime_v2.py` / `runtime_de.py` / `runtime_fr.py` / `runtime_multi.py` 是否还有直接读 en items 跑翻译的入口？本 spec 只处理 bulk_translate；其他路径若仍在使用 en items 作为源，由后续 plan 决定是一并迁移还是作为独立任务保留。

### 6.3 tasks / events

- 父任务 `type='bulk_translate'` 的元数据 JSON 里记录：`raw_ids`、`target_langs`、生成的 plan。
- 子任务进度、事件结构保持兼容，前端任务详情页不需要改动。

## 7. 边界与错误处理

### 7.1 上传
- 视频或封面缺一 → 400，不建半成品行。
- MIME 不在白名单 → 400，错误信息透传到前端 alert。
- TOS 上传失败 → 已上传的对象立即 delete，DB 回滚。
- `display_name` 默认取视频文件名 stem 截断 64；UTF-8 输入合法。

### 7.2 删除
- 软删（`deleted_at = NOW()`），TOS 对象保留。
- 存在 `media_items.source_raw_id = rid` 的多语种产出物时仍允许删；产出物不动（成品已生效）。
- 前端删除前 confirm：「删除后无法恢复，该素材不会再出现在翻译弹窗，但已翻译出来的多语种素材不受影响。确定？」

### 7.3 翻译
- `raw_ids` 空或 `target_langs` 空 → 400。
- `raw_ids` 含已软删或不属于该产品 → 400。
- `target_langs` 含 `en` 或不在启用列表 → 400。
- 同 `(raw_id, target_lang)` 重复翻译 → 允许，产出新行，和现有 bulk_translate 语义一致。

### 7.4 并发
- 前端上传按钮在请求期间 disable。
- 同产品已有未完成 bulk_translate 父任务时 → 前端提示「存在进行中的翻译任务」，仍允许提交（和当前行为一致）。

### 7.5 软删后恢复
- 不做恢复 UI。需要找回得手动改 DB。

## 8. 验收 / 测试策略

### 8.1 单元测试（pytest）
- `tests/test_appcore_medias_raw_sources.py`
  - `create / list（含软删过滤）/ get / update / soft_delete` happy path + 边界。
  - `count_raw_sources_by_product` 批量计数。
  - `collect_media_object_references` 返回结果包含 raw_source_video + raw_source_cover 两类。
- `tests/test_bulk_translate_plan_raw_sources.py`
  - 传 `raw_ids + target_langs` → plan 里 `video` kind 条数 = N × M，ref 字段为 `source_raw_id`。
  - 空 raw_ids 且 content_types 含 video → ValueError。
  - raw_ids 含已软删 / 跨产品 → 拒绝。
  - `detail / cover / copywriting` kind 的 plan 条数不因本次改动变化（回归）。

### 8.2 路由测试
- `tests/test_medias_raw_sources_routes.py`
  - 上传缺 video / 缺 cover / MIME 非法 → 400。
  - 正常上传（mock `tos_clients`）→ 201 + row + 两个 TOS object 都被写入。
  - 上传过程 TOS 失败 → 两个 object 都被 delete，DB 无残留行。
  - DELETE 软删不影响 `media_items.source_raw_id = rid` 的产出物。
  - `POST /medias/products/<pid>/translate` 空 body → 400；正常 body → 返回父任务 id，父任务 plan 结构正确。

### 8.3 E2E（Playwright / webapp-testing）
- 在 `/medias` 页面：新建产品 → 产品列表行显示「原始视频 (0)」→ 上传 1 条（mp4 + 封面）→ 按钮变「原始视频 (1)」。
- 点「翻译」→ 弹窗出现 → 勾选 1 条 + 选 de/fr → 提交 → 跳到 `/tasks/<id>`。
- 关键视觉：无紫色 / 靛蓝，遵循 Ocean Blue token，大圆角，抽屉 / 弹窗样式和现有模块一致。

### 8.4 迁移验证
- `alembic`/项目内的 migration 工具 dry run：
  - `media_raw_sources` 建表成功。
  - `media_items.source_raw_id` 列 + 索引到位。
- 回归：现有 44 产品 + 所有 en items 数据不动，`collect_media_object_references` 结果数量不减。

### 8.5 手动冒烟清单（部署后）
- 上传 mp4 + 封面 → 抽屉列表展示正确。
- 翻译 1 条原始素材 × 2 语言 → 生成 2 条多语种 items，每条 `cover_object_key` 指向翻译后的新封面。
- 软删原始素材 → 已产出多语种素材仍在；翻译弹窗不再列出该素材。

## 9. 工作流约定

按项目 memory 约定：
- Claude 负责 brainstorming + spec + 实施计划；Codex 负责按 plan 实现代码。
- 本需求在独立 worktree（`worktree-feature-medias-raw-sources`）完成，合入主干前 PR review。
- commit message 中文。
- 部署走 `systemctl restart`，不手动 gunicorn。

## 10. 待决 / 后续

- 现有非 bulk_translate 的翻译入口（`runtime_de.py`、`runtime_fr.py`、`runtime_multi.py` 等）是否仍直接读 en items 作为源？本 spec 不处理，留给后续 task：若线上已被这次新入口替代，安排一次清理；若仍在使用，评估是否一并迁移到 raw_sources 源。
- 未来若支持「在系统内一键去字幕并落库为原始素材」（Q3 的 A/D 方案），可以在此模型上平滑追加，无需改数据结构。
