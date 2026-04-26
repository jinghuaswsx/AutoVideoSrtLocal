# D 子系统：原始素材任务库（raw-video-pool）设计文档

- **日期**：2026-04-26
- **范围**：D — 把 C 阶段的"父任务【已上传】按钮 prompt fallback"替换为完整的下载/上传/认领工作面板
- **上位**：[docs/任务中心需求文档-2026-04-26.md](../../任务中心需求文档-2026-04-26.md)

---

## 0. 一句话目标

让"原始视频处理人"有专属工作面板（左侧栏新菜单"原始素材任务库"），可以从待认领池里挑任务、下载原始视频、本地处理后上传成品、自动绑回任务。同时 C 任务详情抽屉里也加同样的下载/上传按钮替换 prompt fallback。

**v1 不做**：批量下载/上传、resumable 上传、释放认领、版本保留（处理后 REPLACE 原始）。

---

## 1. 范围

### 1.1 做什么

1. 新 service `appcore/raw_video_pool.py`：4 个核心函数（`list_tasks` / `download_stream` / `upload_processed` / `infer_visible_tasks`）
2. 新 Blueprint `web/routes/raw_video_pool.py`，前缀 `/raw-video-pool`，4 个端点
3. 新模板 `web/templates/raw_video_pool_list.html`：list view 主页 + 上传 modal
4. layout.html 加新菜单"原始素材任务库"
5. 改 `appcore/permissions.py`：加 `raw_video_pool` 菜单权限
6. 改 `web/templates/tasks_list.html`（C 模板）：替换 `tcParentUploadDone` 的 prompt fallback 为新的下载/上传 modal

### 1.2 不做

- 批量下载（zip 打包）/ 批量上传 / 批量认领
- Resumable upload
- 释放认领（要让别人接：admin 在 C 取消任务后重建）
- 处理前后版本共存（直接 REPLACE）
- 处理工具集成（去字幕、去尾巴等是处理人本地用别的工具）

---

## 2. 数据模型

**不新增表**。复用 C 的 `tasks` + 现有 `media_items`。

D 的工作流相当于：
- 待认领 = `tasks WHERE parent_task_id IS NULL AND status='pending'`
- 我已认领 = `tasks WHERE parent_task_id IS NULL AND status='raw_in_progress' AND assignee_id=<self>`
- 我已上传（待审）= `tasks WHERE parent_task_id IS NULL AND status='raw_review' AND assignee_id=<self>`

**关键约束**：A 已让所有任务的 `media_item_id` 在创建时就有值（admin 在 C 创建任务时从已存在的英文 item 中选）。所以 D 时代的任务**不再有 `media_item_id IS NULL` 的情况**。`tcParentUploadDone` 的 fallback 路径 (prompt 输入 item ID) 实际只在退化场景才用——D 完成后这条路径可以删除。

### 2.1 文件覆盖约定

处理后视频上传到原 `media_items.object_key` 路径（同位置覆盖）。`object_key` 不变；DB 行不动。文件大小、duration、cover 是否需要更新：v1 **暂不更新**（保留 A 入库时的元数据，用户后续在素材管理可以手动修正 duration/cover）。

---

## 3. 状态机交互

D 不引入新状态。它只是把 C 的 `mark_uploaded` 用更友好的 UI 触发：

```
[D] 处理人在"原始素材任务库"看到 status=pending 的任务
    ↓ 点【认领】
[D] 调用 /tasks/api/parent/<id>/claim (复用 C 已有 endpoint)
    → C 把状态翻 pending → raw_in_progress
    ↓
[D] 处理人点【下载原始视频】→ 浏览器流式下载本地文件
    ↓ (本地处理 — D 不管)
[D] 处理人点【上传处理后视频】→ 弹上传 modal → 选 mp4 → 上传
    ↓
[D] 上传成功 → 后端覆盖原 object_key 对应的本地文件 → 自动调用 C 的 mark_uploaded
    → C 把状态翻 raw_in_progress → raw_review
    ↓
[C 现有] admin 在任务中心审核（不变）
```

---

## 4. 服务层（`appcore/raw_video_pool.py`）

```python
def list_visible_tasks(*, viewer_user_id: int, viewer_role: str) -> dict:
    """返回 {'pending': [...], 'in_progress': [...], 'review': [...]} 三段。
    Admin 看全部；处理人只看自己已认领/已提交 + 全局 pending 池。
    每条返回：task_id / product_name / country_codes / created_at / claimed_at / mp4_filename / mp4_size_mb"""

def get_task_for_processing(task_id: int, viewer_user_id: int) -> dict:
    """获取单个 parent task 的处理上下文：media_item_id / object_key / 本地路径 / 任务 status / assignee_id。
    用于权限检查 + 提供下载源 / 上传目标。"""

def stream_original_video(task_id: int, viewer_user_id: int) -> tuple[str, str]:
    """返回 (本地路径, suggested_filename)。
    路径用于 Flask send_file。权限：必须是 admin 或 assignee。"""

def replace_processed_video(task_id: int, actor_user_id: int, uploaded_file) -> int:
    """处理人上传成品 mp4 → 用同 object_key 覆盖本地文件 → 自动调 mark_uploaded。
    返回新 file_size。
    权限：必须是 task.assignee_id"""
```

---

## 5. API 路由（Blueprint `raw_video_pool`，前缀 `/raw-video-pool`）

| 方法 | 路径 | 用途 | 权限 |
|---|---|---|---|
| GET  | `/raw-video-pool/`                       | 主页（render `raw_video_pool_list.html`） | login_required + has_permission('raw_video_pool') |
| GET  | `/raw-video-pool/api/list`               | 三段任务清单 JSON | login_required + capability |
| GET  | `/raw-video-pool/api/task/<tid>/download` | 流式下载原始 mp4 | login_required + (admin OR assignee) |
| POST | `/raw-video-pool/api/task/<tid>/upload`  | multipart 上传处理后 mp4，自动 mark_uploaded | login_required + assignee |

---

## 6. 前端

### 6.1 raw_video_pool_list.html

- header：标题 + "刷新"按钮
- 3 个 section（collapsible，默认全展开）：待认领 / 我已认领 / 我已上传待审
- 每个 section 是表格：产品名 / 国家清单 / 创建时间 / 操作按钮
- 操作按钮：
  - 待认领：[认领]（capability `can_process_raw_video` 才显示）
  - 我已认领：[下载原始] + [上传处理后]
  - 我已上传：禁用按钮"待管理员审核"
- 上传 modal：选文件 + 进度条 + 取消按钮

### 6.2 C 任务详情抽屉改造

替换 `tcParentUploadDone` 函数：
- 当前 prompt 弹窗 → 改为打开复用同样的下载/上传 modal（轻量版）
- 父任务 status=raw_in_progress 时按钮组：[下载原始] + [上传处理后]（取代单一【已上传】）

---

## 7. 错误处理

| 场景 | 后端响应 | 前端展示 |
|---|---|---|
| 任务不在 raw_in_progress 状态 | 409 | toast "任务状态已变更，请刷新" |
| 不是 assignee（且非 admin） | 403 | toast "无权限" |
| media_item 缺失（理论不应发生） | 422 | toast "任务异常，联系管理员" |
| 文件读取失败（本地路径不存在） | 500 | toast "原始视频文件不存在" |
| 上传文件 > 500MB | 413 | toast "文件过大（≤500MB）" |
| 上传文件不是视频（mime/ext 检查） | 415 | toast "请上传视频文件" |
| 上传超时 | 504 | toast "上传超时，重试" |

**上传大小限制**：500MB（可配，常规视频 ≤ 200MB）。

---

## 8. 测试策略

### 8.1 单元测试 `tests/test_appcore_raw_video_pool.py`

- `list_visible_tasks`：admin 看全部；处理人 + can_process_raw_video 看自己 + pending 池
- `stream_original_video`：返回正确路径；非 assignee/admin 抛 PermissionError
- `replace_processed_video`：覆盖文件 + 自动调 mark_uploaded；非 assignee 拒绝；状态错误拒绝

### 8.2 集成测试 `tests/test_raw_video_pool_routes.py`

- GET / 渲染（authed_client_no_db）
- 4 个 API 端点 smoke（authed_client_no_db）
- 非 capability 用户被 403

### 8.3 手动验收

服务器测试环境：登录 → 任务中心建一个父任务 → 切到"原始素材任务库" → 看到待认领 → 点认领 → 下载 → 上传一个新 mp4 → 状态自动到 raw_review → admin 在任务中心通过审核

---

## 9. 接驳

- 上游：C 的所有 `tasks` 数据 + claim/mark_uploaded service
- 下游：素材管理页面查看 media_items（现有）
- 修改 C 模板的【已上传】按钮 → 移除 prompt fallback
