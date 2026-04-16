# 图片翻译功能设计

**日期**: 2026-04-16
**状态**: 待实施

## 1. 背景与目标

跨境电商/海外短视频场景下，用户需要把产品详情图、视频封面图里的英文文字翻译成其他目标语种，同时保持原图布局、字体、配色、图像内容不变。本功能接入 Google 新出的 Nano Banana 系列图像生成模型（Gemini 3 Pro Image / Gemini 3.1 Flash Image），通过图生图 + 文字替换的能力完成批量翻译。

**交付目标**：
- 侧栏新增"图片翻译"菜单，对应列表页 + 详情页
- 支持 1–20 张图片批量翻译，串行处理、实时进度
- 两种场景预设（封面图 / 产品详情图），系统级默认 prompt，用户可按次修改
- 目标语言读取 `media_languages` 表；源语言固定英文（不暴露）
- 模型可切换 Nano Banana 2 / Pro，记忆用户上次选择
- 单张失败自动重试 3 次，失败后可手动重试，整任务不中止
- 单张下载 + zip 打包下载
- 遵守全局 `GEMINI_BACKEND` 切换（aistudio / cloud）

## 2. 范围与非目标

**范围**：
- 路由：`GET /image-translate`、`GET /image-translate/<task_id>`、`/api/image-translate/*`
- 数据：复用 `projects` 表，新增 enum 值 `image_translate`；state 全写在 `state_json`
- 运行时：独立 runner，后台线程串行处理，Socket.IO 推送
- 存储：TOS 对象存储，路径见 §6
- 管理员页：新增"图片翻译默认提示词"分区（两条 prompt）

**非目标**：
- 多轮编辑 / 参考图对话（本期不做）
- 翻译质量评分、自动校对
- 批量再翻译、历史搜索过滤
- 任务清理策略变化（走全局清理机制）
- 任务运行中暂停 / 恢复控制（只做失败重试，不做主动暂停）

## 3. 信息架构与入口

### 侧栏
在"字幕移除"下方新增菜单项 **"图片翻译"**，链接 `/image-translate`。
图标：沿用项目 lucide 体系，建议 `languages` 或 `image` 图标。

### 页面路由
| 路径 | 页面 | 用途 |
|------|------|------|
| `GET /image-translate` | 列表页 | 顶部新建任务区 + 底部历史任务列表 |
| `GET /image-translate/<task_id>` | 详情页 | 任务信息 + 进度 + 图片对比 + 下载 |

## 4. 页面交互设计

### 列表页（`image_translate_list.html`）

顶部 **新建任务区**（卡片）：
- **场景预设** 下拉：`封面图翻译` / `产品详情图翻译`。切换时自动把"提示词"输入框填成对应预设（本次调用；不影响系统默认）
- **目标语言** 下拉：数据来自 `/api/languages`（复用 `media_languages` 表，过滤 `enabled=1` 且 `code != 'en'`）。**无默认**，用户每次手动选
- **使用模型** 下拉：两个选项（见 §5）。初值为用户上次选择（从 `api_keys.extra_config` 读），第一次用户没偏好时无预选
- **提示词** 多行输入框：展示当前预设的默认 prompt（从 `/api/image-translate/system-prompts` 拉取），用户可编辑
- **图片上传区**：拖拽 + 点击，最多 20 张。已选图显示缩略图列表，支持逐张删除
- **提交按钮**：点击后验证（有图、选了语言、选了模型、prompt 非空），走上传直传 → 完成 → 重定向到详情页

底部 **历史任务列表**：
- 按创建时间倒序
- 每行展示：时间、预设、目标语言、模型、进度（`done/total`）、状态 chip
- 点击进详情页

### 详情页（`image_translate_detail.html`）

- **任务信息卡**：预设、目标语言、模型、prompt 快照（只读）
- **进度卡**：总进度 `done/total`，失败数，总状态
- **图片对比区**：每张图一行，左原图、右译图（或失败状态），单张"下载"按钮；失败的行显示错误原因和"重试"按钮
- **底部**：`打包下载全部（zip）` 按钮（只打包已完成的）+ `删除任务` 按钮

## 5. 模型与 Prompt

### 可选模型
```python
IMAGE_MODELS = [
    ("gemini-3-pro-image-preview",   "Nano Banana Pro（高保真）"),
    ("gemini-3.1-flash-image",       "Nano Banana 2（快速）"),
]
```
`model_id` 在实施阶段用 `google-genai` SDK 最新文档再确认一次。

### 用户默认偏好
复用 `api_keys.extra_config`，service = `image_translate`：
```json
{"default_model_id": "gemini-3-pro-image-preview"}
```
- 任务提交成功后后端异步更新
- 列表页加载时 `/api/image-translate/models` 返回 `{items: [...], default_model_id}`

### 系统级默认 Prompt
存 `system_settings` 表：
- key `image_translate.prompt_cover`
- key `image_translate.prompt_detail`

内置初值：
```
把图中出现的所有文字翻译成 {target_language_name}，
保持原有布局、字体风格、颜色、图像内容不变，
只替换文字本身。对于装饰性排版或特殊字体，尽量保持视觉一致。
```

- 管理员在 `/admin` 页面可编辑两条 prompt
- 用户提交任务时，前端会把 `{target_language_name}` 占位符替换成当前选的语言中文名再提交；后端再次执行替换兜底（防止前端遗漏）
- 仅支持 `{target_language_name}` 一个变量，其他形如 `{foo}` 的占位符原样保留

## 6. 数据模型

### `projects` 表
新增 enum 值 `image_translate`：
```sql
ALTER TABLE projects 
  MODIFY COLUMN type ENUM('translation','copywriting','video_creation','video_review',
                          'text_translate','de_translate','fr_translate',
                          'subtitle_removal','image_translate') 
  NOT NULL DEFAULT 'translation';
```

### `state_json` 结构
```json
{
  "preset": "cover|detail",
  "target_language": "de",
  "target_language_name": "德语",
  "model_id": "gemini-3-pro-image-preview",
  "prompt": "用户最终提交的 prompt（变量已替换）",
  "steps": {
    "prepare": "done",
    "process": "pending|running|done|error"
  },
  "progress": {"total": 20, "done": 0, "failed": 0, "running": 0},
  "items": [
    {
      "idx": 0,
      "filename": "cover1.jpg",
      "src_tos_key": "uploads/image_translate/{uid}/{task_id}/src_0.jpg",
      "dst_tos_key": "artifacts/image_translate/{uid}/{task_id}/out_0.png",
      "status": "pending|running|done|failed",
      "attempts": 0,
      "error": ""
    }
  ],
  "error": ""
}
```

### TOS 存储路径
- 原图：`uploads/image_translate/{user_id}/{task_id}/src_{idx}.{ext}`
- 译图：`artifacts/image_translate/{user_id}/{task_id}/out_{idx}.png`

## 7. API 规范

| 方法 | 路径 | 请求 | 响应 |
|------|------|------|------|
| GET | `/api/image-translate/system-prompts` | — | `{"cover": "...", "detail": "..."}` |
| GET | `/api/image-translate/models` | — | `{"items": [{id,name}], "default_model_id": "..."}` |
| POST | `/api/image-translate/upload/bootstrap` | `{count, files:[{filename,size,content_type}]}` | `{task_id, uploads:[{idx, upload_url, object_key}]}` |
| POST | `/api/image-translate/upload/complete` | `{task_id, preset, target_language, model_id, prompt, uploaded:[{idx, object_key, filename, size}]}` | `{task_id}` |
| GET | `/api/image-translate/<task_id>` | — | 全部 state |
| GET | `/api/image-translate/<task_id>/artifact/source/<idx>` | — | 原图（302 到 TOS 签名 URL 或代理流）|
| GET | `/api/image-translate/<task_id>/artifact/result/<idx>` | — | 译图 |
| GET | `/api/image-translate/<task_id>/download/result/<idx>` | — | 单张下载（`Content-Disposition: attachment`）|
| GET | `/api/image-translate/<task_id>/download/zip` | — | zip 打包（包含所有 `done` 的译图，文件名 `{idx:02}_{filename_stem}.png`）|
| POST | `/api/image-translate/<task_id>/retry/<idx>` | — | 202，重置单张状态，runner 重新拾起 |
| DELETE | `/api/image-translate/<task_id>` | — | 204，软删除 + 清 TOS |

权限：所有接口要求 `login_required`，且任务归属当前用户。

## 8. 运行时处理流程

### Runner（`appcore/image_translate_runtime.py`）
```
1. 加载 state，steps.process = running
2. 遍历 items（按 idx）：
   若 status in {done, failed}，跳过（failed 表示 3 次自动重试已耗尽）
   否则：
     status = running，progress.running += 1，emit item_updated
     尝试生成：
       - 从 TOS 下载原图
       - 调 gemini_image.generate_image(...)
       - 成功：上传译图到 TOS，dst_tos_key=..., status=done
       - 可重试错误：attempts+=1，指数退避（1s,2s,4s），最多 3 次
       - 不可重试错误 / 重试耗尽：status=failed，error=...
     progress 更新，emit item_updated + progress
3. steps.process = done（无论有无失败）
4. emit task_done
```

### 手动重试（`POST /retry/<idx>`）
```
1. 校验 idx 存在且 status = failed
2. 重置该 item：status=pending, attempts=0, error=""
3. 若 runner 不在运行中，启动 runner（runner 会跳过已 done/failed，处理 pending）
4. 返回 202
```

### 可重试 vs 不可重试
- **可重试**：网络超时、429、5xx → 自动重试
- **不可重试**：安全过滤、鉴权失败、输入格式非法 → 直接 failed，不重试
- **未知错误**：按可重试处理

### 服务重启恢复
启动时扫描 `projects` 表中 `type=image_translate AND status IN ('queued','running')` 的记录，重新启动对应 runner（与字幕移除一致的恢复模式）。

## 9. Socket.IO 事件

### 客户端加入房间
```js
socket.emit("join_image_translate_task", {task_id})
```

### 服务端事件
| 事件名 | payload |
|--------|---------|
| `image_translate:item_updated` | `{task_id, idx, status, attempts, error, dst_tos_key?}` |
| `image_translate:progress` | `{task_id, total, done, failed, running}` |
| `image_translate:task_done` | `{task_id, status}` |

## 10. Gemini 图像生成封装

新增 `appcore/gemini_image.py`：

```python
def generate_image(
    prompt: str,
    *,
    source_image: bytes,
    source_mime: str,
    model: str,
    user_id: int | None = None,
    project_id: str | None = None,
    service: str = "image_translate",
) -> tuple[bytes, str]:
    """
    调 Gemini 图像生成模型，传原图 + prompt，返回 (译图 bytes, mime)。
    遵守全局 GEMINI_BACKEND 切换。
    """
```

内部：
- 复用 `appcore/gemini.py` 的 `_get_client`、`resolve_config`
- 请求：`client.models.generate_content(model, contents=[Part.from_bytes(source_image, mime), Part.from_text(prompt)])`
- 响应：遍历 `candidates[0].content.parts`，取第一个 `inline_data`，读 `data` + `mime_type`
- 若无图像 part（被安全过滤），抛 `GeminiImageError` 并带 `finish_reason`
- 记录 `usage_logs`（service=image_translate，model_name=具体 model_id）

### 错误类型
- `GeminiImageError`（不可重试）
- `GeminiImageRetryable`（可重试）

## 11. 管理员设置

`/admin` 页面新增一块 **"图片翻译默认提示词"**：
- 两个多行文本框（封面图 / 产品详情图）
- 各自"保存"按钮
- 初始化：若 `system_settings` 中两条 key 不存在，首次 GET 管理员页或 user 页时自动写入内置默认值
- 变量占位符说明：提示"支持 `{target_language_name}` 占位符，提交时会替换为用户选择的目标语言"

## 12. 错误处理策略

| 场景 | 行为 |
|------|------|
| 上传 bootstrap 失败 | 前端弹错误，不创建任务 |
| 单张图下载失败 | 记 failed，不重试（TOS 已上传过的才会到这一步，下载失败几乎只会是配置问题） |
| 单张生成 5xx/timeout | 指数退避自动重试最多 3 次 |
| 单张生成被安全过滤 | 直接 failed，不重试，错误信息含 `finish_reason` |
| 单张上传译图到 TOS 失败 | 记 failed，可手动重试 |
| runner 异常崩溃 | 未完成的 items 状态保持，重启服务后自动恢复 |
| 用户删除任务 | 清 state，删 TOS 原图和所有译图，`projects.deleted_at` 置当前时间 |

## 13. 测试策略

### `tests/test_image_translate_routes.py`
- bootstrap 返回正确签名 URL 结构
- complete 创建 project 正确，state_json 完整
- 查询状态返回完整 state
- 单张重试接口：failed → pending，runner 会重新处理
- 打包下载返回 zip，只包含 done 的图
- 删除后再查 404

### `tests/test_image_translate_runtime.py`
- 正常串行处理 3 张全部成功
- 某张 mock 为 retryable 错，重试 3 次后成功
- 某张 mock 为不可重试错，status=failed，其他继续
- 服务重启后 runner 恢复未完成的任务

### `tests/test_gemini_image.py`
- Mock SDK，验证 `generate_content` 参数构造（Part.from_bytes + Part.from_text）
- 响应解析：正常返回 inline_data
- 无图像 part 时抛 GeminiImageError

### 增量
- `tests/test_admin_routes.py`：管理员读/写两条系统 prompt

## 14. 实施顺序（交给 writing-plans 细化）

1. 数据库迁移 + 模型层（state 构造函数）
2. `gemini_image.py` 封装 + 单测
3. Runner + 单测（mock gemini_image）
4. 路由 + API + 单测
5. 前端列表页（新建区 + 上传直传 + 历史列表）
6. 前端详情页（进度 + 对比 + 下载）
7. 管理员设置页集成
8. Socket.IO 事件接入与前端订阅
9. 手动重试 / zip 下载接入
10. 端到端冒烟（本地 dev + 测试环境）

## 15. 风险与回滚

**风险**：
- Nano Banana 模型名在官方预览阶段可能改动 → 集中在 `IMAGE_MODELS` 常量里维护，出问题只改一处
- 图像生成模型对 prompt 的解释质量不可控 → 用户能改 prompt、可手动重试，作为兜底
- `GEMINI_BACKEND=cloud` 下的 API key 必须已授权 Vertex AI API → 在 Admin 配置页给出使用说明链接

**回滚**：
- 出问题时可直接隐藏侧栏菜单项，API 返回 404，runner 不拾起新任务
- 数据库 migration 只加 enum 值，不影响其他项目类型，即使完全回滚也只是"enum 多一个没人用的值"

---

**相关文件（实施时新增 / 修改）**：
- 新增：`appcore/gemini_image.py`、`appcore/image_translate_runtime.py`
- 新增：`web/routes/image_translate.py`、`web/services/image_translate_runner.py`
- 新增：`web/templates/image_translate_list.html`、`image_translate_detail.html`、`_image_translate_styles.html`、`_image_translate_scripts.html`
- 修改：`appcore/task_state.py`（`create_image_translate`）、`appcore/settings.py`（加 label）、`web/app.py`（注册蓝图）、`web/templates/layout.html`（菜单项）、`web/routes/admin.py`（prompt 设置）
- 迁移：`db/migrations/2026_04_16_add_image_translate_project_type.sql`
- 测试：`tests/test_image_translate_routes.py`、`tests/test_image_translate_runtime.py`、`tests/test_gemini_image.py`
