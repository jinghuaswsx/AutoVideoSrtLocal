# AutoVideoSrt 第二轮全量审查 — 设计文档

**日期**: 2026-04-06
**范围**: 安全漏洞、线程安全、架构一致性、代码质量
**基线**: commit `9490003` (第一轮审查后)

---

## 一、项目架构蓝图

```
┌─────────────────────────────────────────────────────────────┐
│                     Frontend (Jinja2 Templates)              │
│  layout.html ← 21 templates, SocketIO, CSRF interceptor     │
└───────────────────┬─────────────────────────────────────────┘
                    │ HTTP + WebSocket
┌───────────────────▼─────────────────────────────────────────┐
│                     Web Layer (Flask)                         │
│  13 Blueprints: task, copywriting, video_creation,           │
│  video_review, text_translate, projects, voice, prompt,      │
│  settings, admin, admin_usage, auth, tos_upload              │
│  + Flask-Login + CSRF + SocketIO                             │
└──────┬──────────────┬──────────────────┬────────────────────┘
       │              │                  │
┌──────▼──────┐ ┌────▼────────┐ ┌───────▼──────────┐
│ task_state  │ │ EventBus    │ │ pipeline_runner   │
│ (内存+DB)   │ │ (发布/订阅) │ │ (SocketIO 适配)   │
└──────┬──────┘ └────┬────────┘ └───────┬──────────┘
       │              │                  │
┌──────▼──────────────▼──────────────────▼────────────────────┐
│                   Appcore (Business Logic)                    │
│  PipelineRunner, CopywritingRunner, cleanup, usage_log       │
└──────┬──────────────────────────────────────────────────────┘
       │
┌──────▼──────────────────────────────────────────────────────┐
│                   Pipeline (Processing)                       │
│  extract → asr → alignment → translate → tts → subtitle     │
│  → compose → capcut                                          │
│  + copywriting, seedance, video_review, keyframe             │
└──────┬──────────────────────────────────────────────────────┘
       │
┌──────▼──────────────────────────────────────────────────────┐
│              External Services                                │
│  Doubao ASR | OpenRouter/Claude | 豆包 LLM | ElevenLabs TTS  │
│  TOS Storage | Seedance | Gemini | FFmpeg                    │
└─────────────────────────────────────────────────────────────┘
```

**4 条业务管线:**
1. **视频翻译** (主线): upload → extract → ASR → alignment → translate → TTS → subtitle → compose → CapCut
2. **文案创作**: upload → keyframe → LLM copywrite → TTS → compose
3. **视频创作**: upload ref → Seedance generate
4. **视频评分**: upload → Gemini evaluate

---

## 二、发现问题总览

### 🔴 严重 (S1–S8)

| ID | 模块 | 问题 | 影响 |
|----|------|------|------|
| S1 | task_state | 线程安全不完整: `update_variant`、`set_step`、`set_step_message`、`set_current_review_step`、`set_artifact`、`set_preview_file`、`set_keyframes`、`set_copy`、`update_copy_segment`、`confirm_segments`、`confirm_alignment` 未使用 `_lock` | 并发写入导致数据覆盖/丢失 |
| S2 | task_state | `create_copywriting` 直接 `_tasks[task_id] = task`，未加锁 | 同 S1 |
| S3 | copywriting route | upload 不调用 `validate_video_extension`，可上传任意文件 | 恶意文件上传 |
| S4 | copywriting route | product_image 无扩展名/类型校验，直接 save | 恶意文件上传 |
| S5 | video_creation | `ref_{ref_image.filename}` 拼接原始文件名，无清洗 | 路径穿越 |
| S6 | db.py | `_get_pool()` 无锁，并发首次请求可能创建多个连接池 | 连接泄漏 |
| S7 | copywriting route | `update_inputs` 未验证任务属于当前用户 | 越权修改他人商品信息 |
| S8 | runtime.py | `_upload_artifacts_to_tos` 中 `except Exception: pass` 吞掉所有错误 | 配置错误无感知 |

### 🟡 中等 (M1–M9)

| ID | 模块 | 问题 | 影响 |
|----|------|------|------|
| M1 | 架构 | video_creation 用 raw `_update_state`(DB JSON)，其他用 `task_state` 内存+DB，两套状态管理并存 | 重启后状态不一致 |
| M2 | 代码重复 | `_extract_thumbnail` 在 task.py、copywriting.py、video_creation.py 定义 3 次 | 维护困难 |
| M3 | copywriting route | 每次 generate/start_tts 新建 EventBus() 实例 + subscribe，无清理 | 内存泄漏 |
| M4 | video_creation | delete 只设 deleted_at，不清理 task_dir 和 TOS 对象 | 磁盘/存储泄漏 |
| M5 | text_translate | 导入 `_resolve_provider_config`、`_parse_json_content` 等私有函数 | 违反封装 |
| M6 | task.py | `resume_from_step` 先 store.get 后查 DB 归属，逻辑顺序不对 | 可能泄漏任务存在信息 |
| M7 | task.py | `_send_with_range` Range 解析不验证 start <= end 且 start >= 0 | 负 length/越界读取 |
| M8 | video_creation | `_update_state` 读-改-写无事务保护 | 并发更新丢失 |
| M9 | copywriting route | `fix_step` 允许前端任意修改步骤状态 | 状态篡改 |

### 🟢 低 (L1–L6)

| ID | 模块 | 问题 |
|----|------|------|
| L1 | translate.py | `translate_segments` 函数疑似废弃 (主管线用 `generate_localized_translation`) |
| L2 | task.py | 745 行，download 约 60 行嵌套，可拆分 |
| L3 | copywriting route | 572 行，页面路由+API 混在一个文件 |
| L4 | 全局 | `_extract_thumbnail` 应提取到公共 util |
| L5 | task_state | 每个 set_* 调用后都 `_sync_task_to_db`，高频写入 |
| L6 | copywriting_runtime | `_resolve_provider` 与 runtime.py 的 `_resolve_translate_provider` 逻辑不统一 |

---

## 三、设计决策

### 3.1 线程安全策略 (S1, S2, S6)

**方案**: 所有写入 `_tasks` 的操作都必须在 `_lock` 内执行。

**原则**:
- `_lock` 只保护 `_tasks` 字典的读写一致性，不保护 DB 写入
- DB 写入 (`_sync_task_to_db`) 在锁外执行，避免锁内阻塞
- `db.py` 的 `_get_pool()` 加 `threading.Lock` 保护单例

**具体变更**:
- `task_state.py`: 所有修改 `_tasks` 或 task 内容的函数包裹 `with _lock:`
  - `update_variant`, `set_step`, `set_step_message`, `set_current_review_step`
  - `set_artifact`, `set_preview_file`, `set_variant_artifact`, `set_variant_preview_file`
  - `set_keyframes`, `set_copy`, `update_copy_segment`
  - `confirm_segments`, `confirm_alignment`
  - `create_copywriting`
- `db.py`: 加 `_pool_lock = threading.Lock()`，`_get_pool()` 用 `with _pool_lock:`

### 3.2 上传安全加固 (S3, S4, S5)

**方案**: 统一上传校验入口，扩展 `web/upload_util.py`。

**新增**:
```python
ALLOWED_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp"}

def validate_image_extension(filename: str) -> bool:
    ...

def secure_filename_component(filename: str) -> str:
    """清洗文件名: 只保留字母数字下划线点号，截断长度。"""
    ...
```

**变更**:
- `web/routes/copywriting.py` upload: 调用 `validate_video_extension(file.filename)`
- `web/routes/copywriting.py` upload: product_image 调用 `validate_image_extension`
- `web/routes/video_creation.py` upload: ref_image 文件名用 `secure_filename_component` 清洗

### 3.3 归属校验补全 (S7)

**方案**: `update_inputs` 加任务归属验证。

```python
@bp.route("/api/copywriting/<task_id>/inputs", methods=["PUT"])
@login_required
def update_inputs(task_id: str):
    # 验证归属
    task = task_state.get(task_id)
    if not task or task.get("_user_id") != current_user.id:
        return jsonify(error="任务不存在"), 404
    ...
```

### 3.4 TOS 上传异常日志 (S8)

**方案**: 把 `except Exception: pass` 改为 `except Exception: log.warning(...)`。

```python
except Exception:
    log.warning("[runtime] TOS artifact upload failed for task %s", task_id, exc_info=True)
```

### 3.5 Range 解析加固 (M7)

**方案**: 验证 start/end 边界。

```python
start = max(0, start)
end = min(end, file_size - 1)
if start > end:
    start, end = 0, file_size - 1
    status = 200
```

### 3.6 resume_from_step 归属检查修正 (M6)

**方案**: 先查 DB 归属，再从 store 加载任务。

```python
def resume_from_step(task_id):
    row = db_query_one(
        "SELECT id FROM projects WHERE id=%s AND user_id=%s AND deleted_at IS NULL",
        (task_id, current_user.id),
    )
    if not row:
        return jsonify({"error": "Task not found"}), 404
    task = store.get(task_id)
    if not task:
        return jsonify({"error": "Task not found"}), 404
    ...
```

### 3.7 fix_step 端点限制 (M9)

**方案**: 限制可设置的状态值，只允许 `"pending"` 和 `"error"` → `"pending"` 重置。

```python
ALLOWED_FIX_STATUSES = {"pending"}

def fix_step(task_id):
    ...
    if status not in ALLOWED_FIX_STATUSES:
        return jsonify(error="不允许设置该状态"), 400
```

### 3.8 _extract_thumbnail 统一 (M2, L4)

**方案**: 提取到 `pipeline/ffutil.py`(已有)，三处调用改为导入。

```python
# pipeline/ffutil.py 新增
def extract_thumbnail(video_path: str, output_dir: str, scale: str | None = None) -> str | None:
    ...
```

删除 task.py、copywriting.py、video_creation.py 中的 `_extract_thumbnail`。

### 3.9 video_creation delete 补充清理 (M4)

**方案**: 复用 `appcore.cleanup.delete_task_storage`。

```python
@bp.route("/api/video-creation/<task_id>", methods=["DELETE"])
@login_required
def delete(task_id: str):
    row = db_query_one(
        "SELECT task_dir, state_json FROM projects WHERE id = %s AND user_id = %s AND type = 'video_creation'",
        (task_id, current_user.id),
    )
    if not row:
        return jsonify(error="not found"), 404
    cleanup.delete_task_storage(row)
    db_execute(
        "UPDATE projects SET deleted_at = NOW() WHERE id = %s AND user_id = %s",
        (task_id, current_user.id),
    )
    return jsonify({"status": "ok"})
```

### 3.10 text_translate 私有函数改为公开 (M5)

**方案**: 将 `_resolve_provider_config` 和 `_parse_json_content` 去掉下划线前缀，变为公开 API。

- `_resolve_provider_config` → `resolve_provider_config`
- `_parse_json_content` → `parse_json_content`
- 保留原名作为别名一个版本周期(可选，本项目无外部消费者，直接改)

### 3.11 暂不修复项

以下问题暂不在本轮修复，原因如下:

| ID | 原因 |
|----|------|
| M1 | video_creation 的 `_update_state` 重构为走 task_state 需要大量改动，风险较高，单独规划 |
| M3 | EventBus 每次新建的问题需要重构 CopywritingRunner 的生命周期管理，影响面大 |
| M8 | 与 M1 同源，video_creation 状态管理需统一重构 |
| L1-L6 | 代码质量改进，不影响线上运行 |

---

## 四、测试策略

每个修复对应测试:

| 修复 | 测试文件 | 测试内容 |
|------|---------|---------|
| S1/S2 | tests/test_appcore_task_state.py | 新增: 并发写入测试 (threading) |
| S3/S4 | tests/test_security_upload_validation.py | 新增: copywriting 上传 .exe/.php 被拒，product_image .exe 被拒 |
| S5 | tests/test_security_upload_validation.py | 新增: 路径穿越文件名被清洗 |
| S6 | tests/test_appcore_db.py | 新增: 并发 _get_pool() 返回同一实例 |
| S7 | tests/test_security_ownership.py | 新增: update_inputs 非归属用户返回 404 |
| S8 | (日志验证，无独立测试) | — |
| M6 | tests/test_security_ownership.py | 新增: resume 非归属用户返回 404 |
| M7 | tests/test_web_routes.py | 新增: 畸形 Range 头不崩溃 |
| M9 | tests/test_security_ownership.py | 新增: fix_step 拒绝非法状态值 |
| M2 | tests/test_common_utils.py | 新增: ffutil.extract_thumbnail 测试 |
| M4 | (集成测试级别，本轮不新增) | — |
| M5 | (重命名，现有测试覆盖) | — |
