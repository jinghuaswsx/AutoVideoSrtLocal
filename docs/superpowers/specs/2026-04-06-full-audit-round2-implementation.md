# AutoVideoSrt 第二轮审查 — 实施计划

**前置**: 阅读 `2026-04-06-full-audit-round2-design.md` 了解问题清单和设计决策。
**基线**: commit `9490003`
**原则**: TDD — 先写测试，再改代码，再验证。每轮提交+push。

---

## 实施轮次

### 第 1 轮: 线程安全 (S1, S2, S6)

**文件变更:**

1. **`appcore/task_state.py`** — 给所有写入 `_tasks` 的函数加 `with _lock:`

   需要加锁的函数列表 (共 13 个):
   - `create_copywriting` (L338): `_tasks[task_id] = task` → `with _lock: _tasks[task_id] = task`
   - `update_variant` (L181-188): task 内容修改需加锁
   - `set_step` (L191-195): 同上
   - `set_step_message` (L198-201): 同上
   - `set_current_review_step` (L205-209): 同上
   - `set_artifact` (L212-216): 同上
   - `set_preview_file` (L219-223): 同上
   - `set_variant_artifact` (L226-232): 同上
   - `set_variant_preview_file` (L235-241): 同上
   - `set_keyframes` (L343-348): 同上
   - `set_copy` (L351-357): 同上
   - `update_copy_segment` (L360-369): 同上
   - `confirm_segments` (L281-292): 同上
   - `confirm_alignment` (L295-305): 同上

   **模式**: 将修改 task 的代码放入 `with _lock:` 块内，`_sync_task_to_db()` 放在锁外:
   ```python
   def set_step(task_id: str, step: str, status: str):
       with _lock:
           task = _tasks.get(task_id)
           if task:
               task["steps"][step] = status
       if task:
           _sync_task_to_db(task_id)
   ```

2. **`appcore/db.py`** — 加锁保护连接池初始化

   ```python
   import threading
   _pool_lock = threading.Lock()

   def _get_pool() -> PooledDB:
       global _pool
       if _pool is not None:
           return _pool
       with _pool_lock:
           if _pool is None:
               _pool = PooledDB(...)
       return _pool
   ```

**测试 (先写):**
- `tests/test_appcore_task_state.py`: 新增 `test_concurrent_set_step` — 用 10 线程并发调用 set_step，验证无异常
- `tests/test_appcore_task_state.py`: 新增 `test_create_copywriting_under_lock` — 并发 create_copywriting 不丢数据

**提交信息**: `fix: task_state 全部写操作加锁，db 连接池单例加锁`

---

### 第 2 轮: 上传安全 (S3, S4, S5)

**文件变更:**

1. **`web/upload_util.py`** — 新增图片校验和文件名清洗

   ```python
   ALLOWED_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp"}

   def validate_image_extension(filename: str) -> bool:
       if not filename:
           return False
       ext = os.path.splitext(filename)[1].lower()
       return ext in ALLOWED_IMAGE_EXTS

   def secure_filename_component(filename: str) -> str:
       """保留字母数字中文下划线点号，去除路径分隔符等危险字符，截断 100 字符。"""
       import re
       name = os.path.basename(filename)  # 去路径
       name = re.sub(r'[^\w\u4e00-\u9fff.\-]', '_', name)  # 只留安全字符
       return name[:100] if name else "unnamed"
   ```

2. **`web/routes/copywriting.py`** upload 函数 (L108-199):
   - L113 后新增: `from web.upload_util import validate_video_extension; if not validate_video_extension(file.filename): return jsonify(error="不支持的视频格式"), 400`
   - L177 后新增: `from web.upload_util import validate_image_extension; if not validate_image_extension(product_image.filename): return jsonify(error="不支持的图片格式"), 400`

3. **`web/routes/video_creation.py`** upload 函数 (L80-146):
   - L107-108 修改: `ref_image_path = os.path.join(task_dir, f"ref_{secure_filename_component(ref_image.filename)}")`
   - 顶部导入: `from web.upload_util import secure_filename_component`

**测试 (先写):**
- `tests/test_security_upload_validation.py`: 新增 `TestCopywritingUploadValidation` 类
  - `test_rejects_non_video_copywriting`: 上传 .exe 到 copywriting/upload → 400
  - `test_accepts_mp4_copywriting`: 上传 .mp4 → 201
- `tests/test_security_upload_validation.py`: 新增 `TestImageUploadValidation` 类
  - `test_rejects_exe_as_image`: validate_image_extension(".exe") → False
  - `test_accepts_jpg`: validate_image_extension(".jpg") → True
  - `test_accepts_png`: validate_image_extension(".png") → True
- `tests/test_common_utils.py`: 新增 `TestSecureFilename` 类
  - `test_strips_path_traversal`: `secure_filename_component("../../etc/passwd")` → `etc_passwd` 或类似安全结果
  - `test_preserves_normal_name`: `secure_filename_component("photo.jpg")` → `photo.jpg`
  - `test_truncates_long_name`: 超过 100 字符被截断

**提交信息**: `fix: 补全 copywriting 上传校验，清洗 video_creation 图片文件名`

---

### 第 3 轮: 归属校验 + 端点安全 (S7, M6, M9)

**文件变更:**

1. **`web/routes/copywriting.py`** `update_inputs` (L202-230):
   - L207 后新增归属检查:
   ```python
   task = task_state.get(task_id)
   if not task or task.get("_user_id") != current_user.id:
       return jsonify(error="任务不存在"), 404
   ```

2. **`web/routes/task.py`** `resume_from_step` (L709-744):
   - 调换归属检查顺序: 先查 DB 归属，再 store.get:
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

3. **`web/routes/copywriting.py`** `fix_step` (L415-427):
   - 限制可设置的状态:
   ```python
   ALLOWED_FIX_STATUSES = {"pending"}
   ...
   if status not in ALLOWED_FIX_STATUSES:
       return jsonify(error="不允许设置该状态"), 400
   ```

**测试 (先写):**
- `tests/test_security_ownership.py`: 新增 `TestCopywritingInputsOwnership`
  - `test_update_inputs_wrong_user_returns_404`: 用户 A 的任务，用户 B 调用 update_inputs → 404
- `tests/test_security_ownership.py`: 新增 `TestResumeOwnership`
  - `test_resume_wrong_user_returns_404`: 用户 A 的任务，用户 B 调用 resume → 404
- `tests/test_security_ownership.py`: 新增 `TestFixStepRestriction`
  - `test_fix_step_rejects_done_status`: 设置 status="done" → 400
  - `test_fix_step_allows_pending`: 设置 status="pending" → 200

**提交信息**: `fix: 补全 copywriting update_inputs 归属校验，限制 fix_step 状态范围`

---

### 第 4 轮: 健壮性 (S8, M7)

**文件变更:**

1. **`appcore/runtime.py`** `_upload_artifacts_to_tos` (L75-76):
   ```python
   # 改前:
   except Exception:
       pass
   # 改后:
   except Exception:
       log.warning("[runtime] TOS artifact upload failed for task %s", task_id, exc_info=True)
   ```
   - 需顶部加: `log = logging.getLogger(__name__)`

2. **`web/routes/task.py`** `_send_with_range` (L310-341):
   - 在 range 解析后加验证:
   ```python
   start = max(0, start)
   end = min(end, file_size - 1)
   if start > end:
       start, end = 0, file_size - 1
       status = 200
   ```

**测试 (先写):**
- `tests/test_web_routes.py` 或新文件: 新增 `TestRangeRequest`
  - `test_invalid_range_returns_200`: Range: bytes=999999-0 → 正常 200 响应
  - `test_negative_range_start`: Range: bytes=-1-100 → 正常响应
  - `test_valid_range_returns_206`: Range: bytes=0-99 → 206

**提交信息**: `fix: TOS 上传异常加日志，Range 请求边界校验`

---

### 第 5 轮: 代码重复消除 + 清理 (M2/L4, M4, M5)

**文件变更:**

1. **`pipeline/ffutil.py`** — 新增 `extract_thumbnail`:
   ```python
   def extract_thumbnail(video_path: str, output_dir: str, scale: str | None = None) -> str | None:
       """从视频提取第一帧作为 JPEG 缩略图。"""
       thumb_path = os.path.join(output_dir, "thumbnail.jpg")
       cmd = ["ffmpeg", "-y", "-i", video_path, "-vframes", "1"]
       if scale:
           cmd += ["-vf", f"scale={scale}"]
       cmd += ["-f", "image2", thumb_path]
       try:
           subprocess.run(cmd, capture_output=True, timeout=30)
           return thumb_path if os.path.exists(thumb_path) else None
       except Exception:
           return None
   ```

2. **`web/routes/task.py`**: 删除 `_extract_thumbnail` (L38-50)，改为:
   ```python
   from pipeline.ffutil import extract_thumbnail as _extract_thumbnail
   ```

3. **`web/routes/copywriting.py`**: 删除 `_extract_thumbnail` (L551-562)，改为:
   ```python
   from pipeline.ffutil import extract_thumbnail as _extract_thumbnail
   ```

4. **`web/routes/video_creation.py`**: 删除 `_extract_thumbnail` (L36-47)，改为:
   ```python
   from pipeline.ffutil import extract_thumbnail
   ```
   - 注意: video_creation 版本有 `-vf scale=360:-2`，传 `scale="360:-2"` 参数

5. **`web/routes/video_creation.py`** `delete` (L306-313):
   - 加入文件清理:
   ```python
   from appcore import cleanup
   row = db_query_one("SELECT task_dir, state_json FROM projects WHERE id=%s AND user_id=%s AND type='video_creation'", (task_id, current_user.id))
   if not row:
       return jsonify(error="not found"), 404
   cleanup.delete_task_storage(row)
   db_execute("UPDATE projects SET deleted_at=NOW() WHERE id=%s AND user_id=%s", (task_id, current_user.id))
   ```

6. **`pipeline/translate.py`**: 重命名函数 (去下划线):
   - `_resolve_provider_config` → `resolve_provider_config` (保留 `_resolve_provider_config = resolve_provider_config` 别名)
   - `_parse_json_content` → `parse_json_content` (同上)

7. **`web/routes/text_translate.py`** L13: 更新导入:
   ```python
   from pipeline.translate import resolve_provider_config, parse_json_content, get_model_display_name
   ```

**测试 (先写):**
- `tests/test_common_utils.py`: 新增 `TestExtractThumbnail`
  - `test_extract_thumbnail_missing_video_returns_none`: 不存在的路径 → None
  - `test_extract_thumbnail_with_scale_param`: scale 参数被传入 ffmpeg

**提交信息**: `refactor: 统一 _extract_thumbnail，video_creation delete 加清理，translate 函数公开化`

---

### 第 6 轮: 回归测试 + 发布

1. 运行全量非 DB 测试: `python -m pytest tests/ -v --tb=short -k "not (test_web_routes or test_pipeline_runner or test_appcore_db or test_appcore_task_state_db or test_appcore_users or test_appcore_api_keys)"`
2. 确认全部通过
3. 推送到远程
4. SSH 部署:
   ```bash
   ssh -i "C:\Users\admin\.ssh\CC.pem" root@172.30.254.14 \
     "cd /opt/autovideosrt && git pull && systemctl restart autovideosrt"
   ```
5. 验证服务状态: `systemctl status autovideosrt`

---

## 文件变更清单

| 文件 | 操作 | 轮次 |
|------|------|------|
| `appcore/task_state.py` | 修改 (加锁) | 1 |
| `appcore/db.py` | 修改 (池加锁) | 1 |
| `web/upload_util.py` | 修改 (新增函数) | 2 |
| `web/routes/copywriting.py` | 修改 (校验+归属+fix_step) | 2, 3 |
| `web/routes/video_creation.py` | 修改 (文件名清洗+delete清理) | 2, 5 |
| `web/routes/task.py` | 修改 (Range+resume) | 3, 4 |
| `appcore/runtime.py` | 修改 (日志) | 4 |
| `pipeline/ffutil.py` | 修改 (新增 thumbnail) | 5 |
| `pipeline/translate.py` | 修改 (重命名) | 5 |
| `web/routes/text_translate.py` | 修改 (导入更新) | 5 |
| `tests/test_appcore_task_state.py` | 修改 (新增并发测试) | 1 |
| `tests/test_security_upload_validation.py` | 修改 (新增测试) | 2 |
| `tests/test_common_utils.py` | 修改 (新增测试) | 2, 5 |
| `tests/test_security_ownership.py` | 修改 (新增测试) | 3 |
| `tests/test_web_routes.py` | 修改 (新增 Range 测试) | 4 |
