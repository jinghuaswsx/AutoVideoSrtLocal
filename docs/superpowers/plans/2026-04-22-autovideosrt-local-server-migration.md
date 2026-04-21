# AutoVideoSrt 本地生产迁移实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 `172.30.254.14` 成为唯一正式生产入口，运行时完全使用本地 MySQL 和本地文件存储，只有豆包 ASR、Seedance、VOD 字幕移除这类必须公网回拉的链路继续使用 TOS。

**Architecture:** 服务器环境迁移底座已经完成，实施重点不再是装机，而是把仓库代码和真实服务器状态对齐：`systemd` 托管、IP 直连、单机 `Web + MySQL + 本地文件存储`。代码层面保持 `projects.state_json`、`thumbnail_path`、`object_key` 等现有协议不做大改，先把主读写切到本地文件，再把 TOS 收敛成“公网交换层”，最后用迁移脚本校验引用一致性并做整仓验收。

**Tech Stack:** Python, Flask, Flask-SocketIO, Gunicorn, systemd, MySQL, TOS, rsync, pytest, Linux shell

---

## 前置事实

以下内容已经由人工在目标机完成，不再作为本计划的实施目标，只作为后续步骤的前提：

- 目标机：`172.30.254.14`
- 操作系统与托管：`Linux + systemd`
- 应用目录：`/opt/autovideosrt`
- 数据目录：`/data/autovideosrt/uploads`、`/data/autovideosrt/output`
- 软链接：`/opt/autovideosrt/uploads -> /data/autovideosrt/uploads`、`/opt/autovideosrt/output -> /data/autovideosrt/output`
- 基础依赖已安装：`git`、`python3-pip`、`python3-venv`、`ffmpeg`、`mysql-server`、`rsync`
- 生产配置已复制：`.env`、`google_api_key`、`voices/voices.json`
- 远程数据已同步：`uploads` 与 `output`
- 本地 MySQL 已完成导入：库名 `auto_video`
- 当前本地服务已可访问：`http://172.30.254.14/`
- 当前本地 MySQL 已可访问：`172.30.254.14:3306`
- 当前监控入口已可访问：`https://172.30.254.14:9090/`

本计划保持单一文档，不拆分为多个子计划，原因是部署契约、核心上传链路、存储语义、迁移脚本和全模块验收是强耦合事项，必须按同一目标状态一起收口。

## 文件地图

- 运行与部署契约：
  - `deploy/autovideosrt.service`
  - `deploy/publish.sh`
  - `deploy/setup.sh`
  - `.env.example`
  - `config.py`
  - `AutoPush/backend/settings.py`
  - `README.md`
  - `readme_codex.md`
- 主任务链路：
  - `web/routes/task.py`
  - `web/routes/de_translate.py`
  - `web/routes/fr_translate.py`
  - `web/routes/multi_translate.py`
  - `web/templates/_task_workbench_scripts.html`
  - `web/templates/de_translate_list.html`
  - `web/templates/fr_translate_list.html`
  - `web/templates/multi_translate_list.html`
- 本地优先恢复/下载/清理：
  - `appcore/source_video.py`
  - `web/services/artifact_download.py`
  - `appcore/cleanup.py`
  - `web/services/task_restart.py`
  - `appcore/task_state.py`
- 必须公网回拉的交换链路：
  - `pipeline/storage.py`
  - `pipeline/asr.py`
  - `web/routes/subtitle_removal.py`
  - `appcore/subtitle_removal_runtime_vod.py`
  - `appcore/vod_erase_provider.py`
  - `web/routes/video_creation.py`
  - `web/routes/tos_upload.py`
- 素材与图片翻译存储：
  - `appcore/medias.py`
  - `web/routes/medias.py`
  - `web/static/medias.js`
  - `web/routes/image_translate.py`
  - `web/templates/_image_translate_scripts.html`
  - `appcore/image_translate_runtime.py`
- 迁移与验收脚本：
  - `appcore/local_storage_migration.py`
  - `scripts/migrate_local_storage_projects.py`
  - `scripts/migrate_local_storage_media_assets.py`
  - `scripts/verify_local_storage_references.py`
  - `docs/superpowers/notes/2026-04-22-local-server-acceptance-checklist.md`

### Task 1: 把仓库运行契约对齐到真实本地服务器状态

**Files:**
- Create: `tests/test_autopush_settings.py`
- Modify: `.env.example`
- Modify: `deploy/autovideosrt.service`
- Modify: `deploy/publish.sh`
- Modify: `deploy/setup.sh`
- Modify: `AutoPush/backend/settings.py`
- Modify: `AutoPush/README.md`
- Modify: `README.md`
- Modify: `readme_codex.md`

- [ ] **Step 1: 先用 grep 锁定仓库里仍然残留的旧入口假设**

```bash
git grep -n "14\\.103\\.220\\.208\\|:8888" -- deploy AutoPush README.md .env.example
```

Expected: 能看到 `deploy/publish.sh`、`deploy/autovideosrt.service`、`AutoPush/backend/settings.py`、`AutoPush/README.md` 等文件里仍有旧地址或旧端口。

- [ ] **Step 2: 先写一个失败测试，锁住 AutoPush 默认上游不能再指向旧服务器**

```python
from importlib import reload

from AutoPush.backend import settings as autopush_settings


def test_default_autovideo_base_url_points_to_local_server(monkeypatch):
    monkeypatch.delenv("AUTOVIDEO_BASE_URL", raising=False)
    reload(autopush_settings)
    autopush_settings.get_settings.cache_clear()

    assert autopush_settings.get_settings().autovideo_base_url == "http://172.30.254.14"
```

- [ ] **Step 3: 运行测试并确认先失败**

Run: `pytest tests/test_autopush_settings.py -q`

Expected: `FAIL`，因为 `AutoPush/backend/settings.py` 仍然默认指向 `http://14.103.220.208:8888`。

- [ ] **Step 4: 更新运行契约文件，统一到 `172.30.254.14 + 80 端口 + 单 worker + gthread`**

```ini
# deploy/autovideosrt.service
[Service]
User=root
WorkingDirectory=/opt/autovideosrt
Environment="PATH=/opt/autovideosrt/venv/bin:/usr/bin:/usr/local/bin:/bin"
ExecStart=/opt/autovideosrt/venv/bin/gunicorn -w 1 --threads 8 --worker-class gthread --bind 0.0.0.0:80 --timeout 300 main:app
Restart=always
RestartSec=5
```

```bash
# deploy/publish.sh
SERVER_HOST="172.30.254.14"
APP_DIR="/opt/autovideosrt"
SERVICE="autovideosrt"

ssh -i "$KEY" -p "$SERVER_PORT" -o StrictHostKeyChecking=accept-new \
  "$SERVER_USER@$SERVER_HOST" \
  "cd $APP_DIR && git pull && systemctl restart $SERVICE && systemctl status $SERVICE --no-pager | head -n 15"

ssh -i "$KEY" -p "$SERVER_PORT" "$SERVER_USER@$SERVER_HOST" \
  "curl -s -o /dev/null -w 'HTTP %{http_code}\n' http://127.0.0.1/ || true"
```

```bash
# deploy/setup.sh
python db/migrate.py
python db/create_admin.py
systemctl restart autovideosrt
curl -I http://127.0.0.1/
```

```env
# .env.example
LOCAL_SERVER_BASE_URL=http://172.30.254.14
DB_HOST=127.0.0.1
DB_PORT=3306
DB_NAME=auto_video
DB_USER=autovideosrt
DB_PASSWORD=change_me
FLASK_SECRET_KEY=change_me
```

```python
# AutoPush/backend/settings.py
self.autovideo_base_url = getenv(
    "AUTOVIDEO_BASE_URL",
    "http://172.30.254.14",
).rstrip("/")
```

- [ ] **Step 5: 回写 README 和部署说明，明确“环境底座已完成，仓库默认契约是本地服务器”**

```md
## 生产环境约定

- 目标生产机：`172.30.254.14`
- 对外入口：`http://172.30.254.14/`
- 不使用 nginx，`gunicorn` 直接监听 `80`
- MySQL：本机 `127.0.0.1:3306`，库名 `auto_video`
- 数据目录：`/data/autovideosrt/uploads`、`/data/autovideosrt/output`
```

- [ ] **Step 6: 重新跑测试和 grep 校验**

Run:

```bash
pytest tests/test_autopush_settings.py -q
git grep -n "14\\.103\\.220\\.208\\|:8888" -- deploy AutoPush README.md .env.example
```

Expected:

- `pytest` 通过
- grep 不再命中 `deploy/`、`AutoPush/`、`.env.example` 里的旧生产地址

- [ ] **Step 7: 提交这一组契约修正**

```bash
git add .env.example deploy/autovideosrt.service deploy/publish.sh deploy/setup.sh AutoPush/backend/settings.py AutoPush/README.md README.md readme_codex.md tests/test_autopush_settings.py
git commit -m "chore: align runtime contract with local production server"
```

### Task 2: 恢复翻译主链路的本地 `multipart` 上传，停止把新任务创建建立在 TOS 直传上

**Files:**
- Modify: `web/routes/task.py`
- Modify: `web/routes/de_translate.py`
- Modify: `web/routes/fr_translate.py`
- Modify: `web/routes/multi_translate.py`
- Modify: `web/templates/_task_workbench_scripts.html`
- Modify: `web/templates/de_translate_list.html`
- Modify: `web/templates/fr_translate_list.html`
- Modify: `web/templates/multi_translate_list.html`
- Modify: `tests/test_web_routes.py`
- Modify: `tests/test_multi_translate_routes.py`
- Modify: `tests/test_tos_upload_routes.py`

- [ ] **Step 1: 先写失败测试，锁定“主翻译任务创建必须接受本地文件上传”**

```python
import io
import os

from web import store


def test_upload_route_accepts_local_multipart(tmp_path, authed_client_no_db, monkeypatch):
    monkeypatch.setattr("web.routes.task.OUTPUT_DIR", str(tmp_path / "output"))
    monkeypatch.setattr("web.routes.task.UPLOAD_DIR", str(tmp_path / "uploads"))

    response = authed_client_no_db.post(
        "/api/tasks",
        data={"video": (io.BytesIO(b"fake mp4 bytes"), "demo.mp4")},
        content_type="multipart/form-data",
    )

    assert response.status_code == 201
    payload = response.get_json()
    saved = store.get(payload["task_id"])
    assert saved["delivery_mode"] == "local_primary"
    assert os.path.exists(saved["video_path"])
```

```python
def test_multi_translate_start_accepts_local_multipart(tmp_path, authed_client_no_db, monkeypatch):
    monkeypatch.setattr("web.routes.multi_translate.OUTPUT_DIR", str(tmp_path / "output"))
    monkeypatch.setattr("web.routes.multi_translate.UPLOAD_DIR", str(tmp_path / "uploads"))

    response = authed_client_no_db.post(
        "/api/multi-translate/start",
        data={
            "target_lang": "de",
            "video": (io.BytesIO(b"fake mp4 bytes"), "demo.mp4"),
        },
        content_type="multipart/form-data",
    )

    assert response.status_code == 201
    payload = response.get_json()
    assert payload["task_id"]
```

- [ ] **Step 2: 运行聚焦测试并确认先失败**

Run:

```bash
pytest tests/test_web_routes.py tests/test_multi_translate_routes.py tests/test_tos_upload_routes.py -q -k "multipart or upload or task_from_tos"
```

Expected: `FAIL`，因为当前 `/api/tasks`、`/api/de-translate/start`、`/api/fr-translate/start`、`/api/multi-translate/start` 仍然返回 `410`，前端模板仍然走 bootstrap/complete 签名上传。

- [ ] **Step 3: 改后端任务创建路由，直接保存本地源视频并写入本地优先状态**

```python
# web/routes/task.py
from web.upload_util import validate_video_extension, secure_filename_component


@bp.route("", methods=["POST"])
@login_required
def upload():
    file = request.files["video"]
    if not validate_video_extension(file.filename):
        return jsonify({"error": "不支持的视频格式"}), 400

    original_filename = secure_filename_component(file.filename)
    task_id = str(uuid.uuid4())
    ext = os.path.splitext(original_filename)[1].lower()
    task_dir = os.path.join(OUTPUT_DIR, task_id)
    video_path = os.path.join(UPLOAD_DIR, f"{task_id}{ext}")
    os.makedirs(task_dir, exist_ok=True)
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    file.save(video_path)

    store.create(task_id, video_path, task_dir, original_filename=original_filename, user_id=current_user.id)
    store.update(
        task_id,
        display_name=_resolve_name_conflict(current_user.id, _default_display_name(original_filename)),
        source_object_info={
            "original_filename": original_filename,
            "content_type": file.mimetype or "",
            "storage_backend": "local",
            "uploaded_at": datetime.now().isoformat(timespec="seconds"),
        },
        delivery_mode="local_primary",
        source_tos_key="",
    )
    return jsonify({"task_id": task_id}), 201
```

```python
# web/routes/de_translate.py / fr_translate.py / multi_translate.py
store.update(
    task_id,
    type="de_translate",  # 或 fr_translate / multi_translate
    delivery_mode="local_primary",
    source_tos_key="",
    source_object_info={
        "original_filename": original_filename,
        "content_type": file.mimetype or "",
        "storage_backend": "local",
        "uploaded_at": datetime.now().isoformat(timespec="seconds"),
    },
)
```

- [ ] **Step 4: 把前端上传脚本改回单次 `multipart/form-data`，不再先拿签名 URL**

```javascript
// web/templates/_task_workbench_scripts.html
async function submitLocalUpload(file) {
  const formData = new FormData();
  formData.append("video", file);

  const response = await fetch("/api/tasks", {
    method: "POST",
    body: formData,
  });
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.error || "上传失败");
  return payload.task_id;
}
```

```javascript
// web/templates/de_translate_list.html / fr_translate_list.html / multi_translate_list.html
const formData = new FormData();
formData.append("video", file);
if (targetLang) formData.append("target_lang", targetLang);

const response = await fetch("/api/multi-translate/start", {
  method: "POST",
  body: formData,
});
```

- [ ] **Step 5: 把通用 TOS 上传蓝图降级成兼容入口，不再作为主流程依赖**

```python
# web/routes/tos_upload.py
@bp.route("/bootstrap", methods=["POST"])
@login_required
def bootstrap_upload():
    return jsonify({"error": "新建翻译任务已改为本地 multipart 上传，禁止继续走通用 TOS 直传"}), 410
```

- [ ] **Step 6: 跑回归，确认新任务创建已经切到本地**

Run:

```bash
pytest tests/test_web_routes.py tests/test_multi_translate_routes.py tests/test_tos_upload_routes.py -q
```

Expected:

- 主翻译、本地德语、法语、多语种新建任务均返回 `201`
- `tests/test_tos_upload_routes.py` 只保留“旧接口被禁用/兼容”的断言

- [ ] **Step 7: 提交主链路本地上传改造**

```bash
git add web/routes/task.py web/routes/de_translate.py web/routes/fr_translate.py web/routes/multi_translate.py web/routes/tos_upload.py web/templates/_task_workbench_scripts.html web/templates/de_translate_list.html web/templates/fr_translate_list.html web/templates/multi_translate_list.html tests/test_web_routes.py tests/test_multi_translate_routes.py tests/test_tos_upload_routes.py
git commit -m "feat: switch translation task creation to local multipart uploads"
```

### Task 3: 把下载、恢复、清理、重跑统一改成“本地优先，旧 `pure_tos` 任务兼容”

**Files:**
- Modify: `appcore/source_video.py`
- Modify: `web/services/artifact_download.py`
- Modify: `appcore/cleanup.py`
- Modify: `web/services/task_restart.py`
- Modify: `web/routes/task.py`
- Modify: `tests/test_cleanup.py`
- Modify: `tests/test_task_restart.py`
- Modify: `tests/test_web_routes.py`

- [ ] **Step 1: 先写失败测试，锁定“本地任务下载必须优先走本地文件，不允许因为有旧 TOS 元数据就跳转”**

```python
def test_download_prefers_local_file_for_local_primary_task(tmp_path, authed_client_no_db):
    result_path = tmp_path / "hard.mp4"
    result_path.write_bytes(b"video")

    store.create("task-local", str(tmp_path / "source.mp4"), str(tmp_path), original_filename="demo.mp4", user_id=1)
    store.update(
        "task-local",
        delivery_mode="local_primary",
        result={"hard_video": str(result_path)},
        tos_uploads={"normal:hard_video": {"tos_key": "artifacts/1/task-local/normal/hard.mp4"}},
    )

    response = authed_client_no_db.get("/api/tasks/task-local/download/hard")

    assert response.status_code == 200
    assert response.data == b"video"
```

```python
def test_cleanup_keeps_local_primary_source_file(tmp_path, monkeypatch):
    upload_dir = tmp_path / "uploads"
    upload_dir.mkdir()
    video_path = upload_dir / "task-1.mp4"
    video_path.write_bytes(b"video")

    monkeypatch.setattr("appcore.cleanup.UPLOAD_DIR", str(upload_dir))
    monkeypatch.setattr("appcore.cleanup.query", lambda *args, **kwargs: [{
        "id": "task-1",
        "state_json": json.dumps({
            "delivery_mode": "local_primary",
            "source_tos_key": "uploads/1/task-1/source.mp4",
            "video_path": str(video_path),
        }, ensure_ascii=False),
    }])

    cleanup._trim_local_uploads_with_tos_backup()

    assert video_path.exists()
```

- [ ] **Step 2: 运行测试并确认先失败**

Run:

```bash
pytest tests/test_web_routes.py tests/test_cleanup.py tests/test_task_restart.py -q -k "local_primary or download or restart"
```

Expected: `FAIL`，因为当前 `serve_artifact_download()` 仍然优先看 TOS 记录，`_trim_local_uploads_with_tos_backup()` 仍会误删带 `source_tos_key` 的本地源文件。

- [ ] **Step 3: 调整下载逻辑，只有旧 `pure_tos` 任务才维持“TOS 优先”**

```python
# web/services/artifact_download.py
local_ready = bool(path and os.path.exists(path))
pure_tos = (task.get("delivery_mode") or "").strip() == "pure_tos"

if local_ready and not pure_tos:
    return send_file(os.path.abspath(path), as_attachment=True, download_name=download_name)

if uploaded_artifact:
    return redirect(tos_clients.generate_signed_download_url(uploaded_artifact["tos_key"]))

if local_ready:
    return send_file(os.path.abspath(path), as_attachment=True, download_name=download_name)
```

- [ ] **Step 4: 调整恢复和清理逻辑，禁止再因为“有 TOS 备份”删除本地源文件**

```python
# appcore/cleanup.py
delivery_mode = (state.get("delivery_mode") or "").strip()
if delivery_mode != "pure_tos":
    continue
```

```python
# appcore/source_video.py
if os.path.exists(video_path):
    return video_path

if not source_tos_key:
    raise RuntimeError(
        f"源视频文件丢失: {video_path} 不存在且 source_tos_key 为空，"
        f"当前任务为本地优先任务，不能假定 TOS 一定有备份。"
    )
```

- [ ] **Step 5: 调整重跑逻辑，保留本地源视频并只清理产物对象**

```python
# web/services/task_restart.py
_RESET_FIELDS = {
    "status": "uploaded",
    "result": {},
    "exports": {},
    "artifacts": {},
    "preview_files": {},
    "tos_uploads": {},
    "error": "",
}

# source_tos_key、video_path、task_dir 保持不动
ensure_local_source_video(task_id)
runner.start(task_id, user_id=user_id)
```

- [ ] **Step 6: 跑回归，确认旧 `pure_tos` 任务兼容、新任务本地优先**

Run:

```bash
pytest tests/test_web_routes.py tests/test_cleanup.py tests/test_task_restart.py tests/test_pipeline_runner.py -q
```

Expected:

- `delivery_mode == "local_primary"` 的任务优先本地下载
- `delivery_mode == "pure_tos"` 的历史任务仍可通过 TOS 回落
- 清理逻辑不再删掉新任务的本地源文件

- [ ] **Step 7: 提交本地优先生命周期改造**

```bash
git add appcore/source_video.py web/services/artifact_download.py appcore/cleanup.py web/services/task_restart.py web/routes/task.py tests/test_cleanup.py tests/test_task_restart.py tests/test_web_routes.py
git commit -m "fix: make task storage local-first with pure-tos compatibility"
```

### Task 4: 把字幕移除、视频生成、ASR 这类公网回拉链路改成“按需上 TOS”，而不是“默认所有文件都在 TOS”

**Files:**
- Modify: `pipeline/storage.py`
- Modify: `web/routes/subtitle_removal.py`
- Modify: `appcore/subtitle_removal_runtime_vod.py`
- Modify: `appcore/vod_erase_provider.py`
- Modify: `web/routes/video_creation.py`
- Modify: `tests/test_subtitle_removal_routes.py`
- Modify: `tests/test_subtitle_removal_runtime.py`
- Modify: `tests/test_pipeline_runner.py`

- [ ] **Step 1: 先写失败测试，锁定“字幕移除任务上传后不需要立即进入 TOS，但提交到外部 provider 时必须能按需拿到公网 URL”**

```python
def test_subtitle_removal_submit_stages_public_source_on_demand(tmp_path, authed_client_no_db, monkeypatch):
    source_video = tmp_path / "source.mp4"
    source_video.write_bytes(b"video")

    store.create_subtitle_removal("sr-1", str(source_video), str(tmp_path), original_filename="demo.mp4", user_id=1)
    store.update(
        "sr-1",
        status="ready",
        video_path=str(source_video),
        source_tos_key="",
        media_info={"width": 1280, "height": 720, "duration": 8.0, "resolution": "1280x720"},
    )

    monkeypatch.setattr("web.routes.subtitle_removal.tos_clients.upload_file", lambda local_path, object_key: None)
    monkeypatch.setattr(
        "web.routes.subtitle_removal.tos_clients.generate_signed_download_url",
        lambda object_key, expires=86400: f"https://example.com/{object_key}",
    )

    response = authed_client_no_db.post(
        "/api/subtitle-removal/sr-1/submit",
        json={"remove_mode": "full", "erase_text_type": "subtitle"},
    )

    assert response.status_code == 202
    assert store.get("sr-1")["source_tos_key"]
```

- [ ] **Step 2: 运行测试并确认先失败**

Run:

```bash
pytest tests/test_subtitle_removal_routes.py tests/test_subtitle_removal_runtime.py tests/test_pipeline_runner.py -q -k "source_tos_key or on_demand or public"
```

Expected: `FAIL`，因为当前字幕移除上传完成时就强依赖 TOS 直传，主翻译 ASR 注释和实现也仍把 TOS 视为默认主存储。

- [ ] **Step 3: 把 `pipeline/storage.py` 明确收敛成“公网交换层”**

```python
def upload_file(local_path: str, object_key: str = None, expires: int = 3600) -> str:
    """
    仅用于外部服务必须主动回拉文件的场景：
    - 豆包 ASR
    - Seedance
    - VOD / 字幕移除 provider
    """
    if object_key is None:
        filename = os.path.basename(local_path)
        object_key = TOS_PREFIX + filename

    tos_clients.upload_file(local_path, object_key)
    return tos_clients.generate_signed_download_url(object_key, expires=expires)
```

- [ ] **Step 4: 给字幕移除和视频生成补“按需上 TOS”的统一入口**

```python
# web/routes/subtitle_removal.py
def _ensure_public_source_url(task_id: str, task: dict) -> str:
    source_tos_key = (task.get("source_tos_key") or "").strip()
    if not source_tos_key:
        video_path = (task.get("video_path") or "").strip()
        source_tos_key = tos_clients.build_source_object_key(task.get("_user_id"), task_id, task.get("original_filename") or "source.mp4")
        tos_clients.upload_file(video_path, source_tos_key)
        store.update(task_id, source_tos_key=source_tos_key)
    return tos_clients.generate_signed_download_url(source_tos_key, expires=86400)
```

```python
# appcore/subtitle_removal_runtime_vod.py
source_url = _ensure_public_source_url(task_id, task)
```

```python
# web/routes/video_creation.py
public_video_url = tos_upload(video_path, source_key)
public_image_url = tos_upload(image_path, image_key)
```

- [ ] **Step 5: 跑回归，确认 TOS 只在必须公网回拉的地方被使用**

Run:

```bash
pytest tests/test_subtitle_removal_routes.py tests/test_subtitle_removal_runtime.py tests/test_pipeline_runner.py -q
```

Expected:

- 主翻译任务本地创建不再依赖 TOS
- 字幕移除在真正提交 provider 时才补 `source_tos_key`
- ASR 与视频生成仍能拿到签名 URL

- [ ] **Step 6: 提交公网交换层收敛**

```bash
git add pipeline/storage.py web/routes/subtitle_removal.py appcore/subtitle_removal_runtime_vod.py appcore/vod_erase_provider.py web/routes/video_creation.py tests/test_subtitle_removal_routes.py tests/test_subtitle_removal_runtime.py tests/test_pipeline_runner.py
git commit -m "feat: limit tos usage to public exchange flows"
```

### Task 5: 把素材库、图片翻译、明细图等长期业务资产切到本地文件存储

**Files:**
- Create: `appcore/local_media_storage.py`
- Modify: `appcore/medias.py`
- Modify: `web/routes/medias.py`
- Modify: `web/static/medias.js`
- Modify: `web/routes/image_translate.py`
- Modify: `web/templates/_image_translate_scripts.html`
- Modify: `appcore/image_translate_runtime.py`
- Modify: `tests/test_medias_routes.py`
- Modify: `tests/test_image_translate_routes.py`
- Modify: `tests/test_image_translate_runtime.py`

- [ ] **Step 1: 先写失败测试，锁定“媒体资产 object_key 继续存在，但底层必须能落本地路径”**

```python
from pathlib import Path

from appcore.local_media_storage import object_key_path


def test_object_key_path_resolves_under_local_media_root(tmp_path, monkeypatch):
    monkeypatch.setattr("appcore.local_media_storage.OUTPUT_DIR", str(tmp_path / "output"))

    resolved = object_key_path("1/medias/12/demo.mp4")

    assert resolved == Path(tmp_path / "output" / "media_store" / "1/medias/12/demo.mp4")
```

```python
def test_image_translate_downloads_from_local_media_store(tmp_path, monkeypatch):
    src = tmp_path / "output" / "media_store" / "1/medias/12/source.png"
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_bytes(b"png")

    monkeypatch.setattr("appcore.image_translate_runtime.object_key_path", lambda key: src)

    path = runtime._resolve_source_image_path({"src_tos_key": "1/medias/12/source.png", "source_bucket": "local"})

    assert path == str(src)
```

- [ ] **Step 2: 运行测试并确认先失败**

Run:

```bash
pytest tests/test_medias_routes.py tests/test_image_translate_routes.py tests/test_image_translate_runtime.py -q -k "local_media_store or source_bucket"
```

Expected: `FAIL`，因为当前媒体和图片翻译仍默认使用 media bucket / TOS signed URL。

- [ ] **Step 3: 新建本地媒体存储助手，保持 `object_key` 仍然是逻辑键**

```python
# appcore/local_media_storage.py
from pathlib import Path

from config import OUTPUT_DIR


def media_store_root() -> Path:
    return Path(OUTPUT_DIR).resolve() / "media_store"


def object_key_path(object_key: str) -> Path:
    return media_store_root() / object_key.lstrip("/")


def ensure_object_parent(object_key: str) -> Path:
    path = object_key_path(object_key)
    path.parent.mkdir(parents=True, exist_ok=True)
    return path
```

- [ ] **Step 4: 把 `medias` 和 `image_translate` 路由从 bootstrap/签名上传改成本地文件保存**

```python
# web/routes/medias.py
path = ensure_object_parent(object_key)
upload_file.save(path)
db_execute(
    "UPDATE media_items SET object_key=%s, thumbnail_path=%s WHERE id=%s",
    (object_key, relative_thumbnail_path, item_id),
)
```

```javascript
// web/static/medias.js
const formData = new FormData();
formData.append("file", file);
const response = await fetch(`/medias/api/products/${pid}/items`, {
  method: "POST",
  body: formData,
});
```

```javascript
// web/templates/_image_translate_scripts.html
var formData = new FormData();
files.forEach(function(file){ formData.append("files", file); });
var response = await fetch("/api/image-translate/upload", {
  method: "POST",
  body: formData,
});
```

- [ ] **Step 5: 把图片翻译 runtime 改成“先读本地媒体仓，再兼容历史 TOS 键”**

```python
# appcore/image_translate_runtime.py
from appcore.local_media_storage import object_key_path


def _resolve_source_image_path(item: dict) -> str:
    src_key = (item.get("src_tos_key") or "").strip()
    if src_key:
        local_path = object_key_path(src_key)
        if local_path.exists():
            return str(local_path)
    return tos_clients.download_file(src_key, download_path)
```

- [ ] **Step 6: 跑回归，确认素材与图片翻译已本地化**

Run:

```bash
pytest tests/test_medias_routes.py tests/test_image_translate_routes.py tests/test_image_translate_runtime.py tests/test_appcore_medias.py -q
```

Expected:

- 素材上传不再要求 signed URL
- `object_key` 继续能被复用，但底层文件落在 `output/media_store/`
- 图片翻译优先从本地媒体仓读源图

- [ ] **Step 7: 提交素材与图片翻译存储切换**

```bash
git add appcore/local_media_storage.py appcore/medias.py web/routes/medias.py web/static/medias.js web/routes/image_translate.py web/templates/_image_translate_scripts.html appcore/image_translate_runtime.py tests/test_medias_routes.py tests/test_image_translate_routes.py tests/test_image_translate_runtime.py
git commit -m "feat: move media and image assets to local storage"
```

### Task 6: 提供项目级与素材级迁移脚本，并把“引用一致性校验”做成可重复执行工具

**Files:**
- Create: `appcore/local_storage_migration.py`
- Create: `scripts/migrate_local_storage_projects.py`
- Create: `scripts/migrate_local_storage_media_assets.py`
- Create: `scripts/verify_local_storage_references.py`
- Create: `tests/test_local_storage_migration.py`

- [ ] **Step 1: 先写失败测试，锁住“项目引用遍历”和“媒体引用遍历”**

```python
from appcore.local_storage_migration import collect_project_refs, collect_media_refs


def test_collect_project_refs_includes_thumbnail_and_result_artifacts():
    state = {
        "video_path": "/data/autovideosrt/uploads/task-1.mp4",
        "thumbnail_path": "/data/autovideosrt/output/task-1/thumbnail.jpg",
        "result": {"hard_video": "/data/autovideosrt/output/task-1/hard.mp4"},
        "tos_uploads": {"normal:hard_video": {"tos_key": "artifacts/1/task-1/normal/hard.mp4"}},
    }

    refs = collect_project_refs("task-1", state)

    assert "/data/autovideosrt/uploads/task-1.mp4" in refs["local_paths"]
    assert "artifacts/1/task-1/normal/hard.mp4" in refs["logical_keys"]
```

```python
def test_collect_media_refs_includes_object_and_cover_keys():
    row = {
        "object_key": "1/medias/12/demo.mp4",
        "cover_object_key": "1/medias/12/demo.cover.jpg",
        "thumbnail_path": "media_store/1/medias/12/thumb.jpg",
    }

    refs = collect_media_refs(row)

    assert "1/medias/12/demo.mp4" in refs["logical_keys"]
    assert "1/medias/12/demo.cover.jpg" in refs["logical_keys"]
    assert "media_store/1/medias/12/thumb.jpg" in refs["relative_paths"]
```

- [ ] **Step 2: 运行测试并确认先失败**

Run: `pytest tests/test_local_storage_migration.py -q`

Expected: `FAIL`，因为相关迁移助手和脚本还不存在。

- [ ] **Step 3: 实现公共迁移助手，统一抽取项目和媒体引用**

```python
# appcore/local_storage_migration.py
def collect_project_refs(task_id: str, state: dict) -> dict:
    local_paths = set()
    logical_keys = set()

    for key in ("video_path", "thumbnail_path", "result_video_path", "srt_path"):
        value = (state.get(key) or "").strip()
        if value:
            local_paths.add(value)

    result = state.get("result") or {}
    for value in result.values():
        if isinstance(value, str) and value.strip():
            local_paths.add(value.strip())

    logical_keys.update(tos_clients.collect_task_tos_keys(state))
    return {"local_paths": sorted(local_paths), "logical_keys": sorted(logical_keys)}


def collect_media_refs(row: dict) -> dict:
    logical_keys = set()
    relative_paths = set()

    for key in ("object_key", "cover_object_key", "video_object_key"):
        value = (row.get(key) or "").strip()
        if value:
            logical_keys.add(value)

    thumbnail = (row.get("thumbnail_path") or "").strip()
    if thumbnail:
        relative_paths.add(thumbnail)

    return {"logical_keys": sorted(logical_keys), "relative_paths": sorted(relative_paths)}
```

- [ ] **Step 4: 写三个可重复执行脚本：项目迁移、媒体迁移、引用校验**

```python
# scripts/migrate_local_storage_projects.py
parser.add_argument("--dry-run", action="store_true")
parser.add_argument("--only-active", action="store_true")
parser.add_argument("--limit", type=int, default=0)

rows = db_query(
    "SELECT id, state_json FROM projects WHERE deleted_at IS NULL ORDER BY created_at DESC"
)
for row in rows:
    refs = collect_project_refs(row["id"], json.loads(row["state_json"] or "{}"))
    print(json.dumps({
        "task_id": row["id"],
        "local_paths": refs["local_paths"],
        "logical_keys": refs["logical_keys"],
    }, ensure_ascii=False))
```

```python
# scripts/migrate_local_storage_media_assets.py
rows = db_query("SELECT id, object_key, cover_object_key, thumbnail_path FROM media_items")
for row in rows:
    refs = collect_media_refs(row)
    print(json.dumps({
        "media_id": row["id"],
        "logical_keys": refs["logical_keys"],
        "relative_paths": refs["relative_paths"],
    }, ensure_ascii=False))
```

```python
# scripts/verify_local_storage_references.py
if missing_local_paths or missing_logical_keys:
    print(json.dumps(report, ensure_ascii=False, indent=2))
    raise SystemExit(1)
print(json.dumps({"ok": True, "checked": checked_count}, ensure_ascii=False))
```

- [ ] **Step 5: 跑单测和脚本 dry-run**

Run:

```bash
pytest tests/test_local_storage_migration.py -q
python scripts/migrate_local_storage_projects.py --dry-run --only-active
python scripts/migrate_local_storage_media_assets.py --dry-run
python scripts/verify_local_storage_references.py
```

Expected:

- 单测通过
- 两个迁移脚本 dry-run 只打印待处理对象，不直接写入
- 校验脚本输出 `{"ok": true, "checked": N}` 或明确列出缺失引用

- [ ] **Step 6: 提交迁移与校验工具**

```bash
git add appcore/local_storage_migration.py scripts/migrate_local_storage_projects.py scripts/migrate_local_storage_media_assets.py scripts/verify_local_storage_references.py tests/test_local_storage_migration.py
git commit -m "feat: add local storage migration and verification scripts"
```

### Task 7: 形成验收清单，在 `172.30.254.14` 做整仓验证、切换与回退演练

**Files:**
- Create: `docs/superpowers/notes/2026-04-22-local-server-acceptance-checklist.md`

- [ ] **Step 1: 先把整仓验收拆成“基线 / 主链路真跑 / 重点模块 smoke / 旁路服务”四组**

```md
# 本地生产迁移验收清单

## 1. 基线
- 登录
- 首页
- 项目列表
- 项目详情
- MySQL 读写
- WebSocket 进度

## 2. 主链路真跑
- 主翻译任务上传
- extract/asr/translate/tts/subtitle/compose/export
- 预览
- 下载
- 重跑

## 3. 重点模块 smoke
- de_translate
- fr_translate
- copywriting
- text_translate
- title_translate
- video_review
- video_creation
- subtitle_removal
- translate_lab
- image_translate
- medias
- openapi_materials
- pushes
- link_check
- bulk_translate
- multi_translate
- voice_library
- prompt_library
- settings
- admin_prompts
- admin_usage
- admin_ai_billing
- auth

## 4. 旁路服务
- AutoPush
- link_check_desktop
```

- [ ] **Step 2: 跑代码级回归集**

Run:

```bash
pytest tests/test_autopush_settings.py tests/test_config.py tests/test_web_routes.py tests/test_multi_translate_routes.py tests/test_subtitle_removal_routes.py tests/test_medias_routes.py tests/test_image_translate_routes.py tests/test_cleanup.py tests/test_task_restart.py tests/test_pipeline_runner.py tests/test_local_storage_migration.py -q
```

Expected: 全部 `PASS`

- [ ] **Step 3: 在目标机跑服务级验证**

Run:

```bash
systemctl daemon-reload
systemctl restart autovideosrt
systemctl status autovideosrt --no-pager
journalctl -u autovideosrt -n 100 --no-pager
ss -lntp | grep ":80"
curl -I http://127.0.0.1/
mysql -h 127.0.0.1 -P 3306 -u autovideosrt -p -e "SHOW TABLES FROM auto_video;"
python scripts/verify_local_storage_references.py
```

Expected:

- `autovideosrt` 为 `active (running)`
- `ss` 显示 `gunicorn` 监听 `:80`
- `curl` 返回 `HTTP 200` 或登录重定向 `302`
- MySQL 查询正常
- 本地引用校验通过

- [ ] **Step 4: 跑人工主链路和重点模块验收**

Run:

```text
1. 访问 http://172.30.254.14/
2. 登录管理员账号
3. 新建主翻译任务并上传一个最小视频
4. 确认任务可跑到 export，能预览、能下载、能重跑
5. 新建 subtitle_removal 任务并确认 provider 提交可用
6. 打开 de_translate、fr_translate、copywriting、text_translate、title_translate、video_review、video_creation 页面
7. 打开 medias、image_translate、bulk_translate、multi_translate、pushes、voice_library、prompt_library、openapi_materials 页面
8. 打开 settings、admin_prompts、admin_usage、admin_ai_billing、auth 相关页面或入口
9. 对每个模块至少完成一次“页面打开 + 参数提交 + 错误反馈正常”
```

Expected: 主链路真跑通过，重点模块至少 smoke 通过。

- [ ] **Step 5: 执行正式切换和回退演练**

Run:

```text
切换：
1. 明确团队开始统一使用 http://172.30.254.14/
2. 远程旧服务器不删除、不改库，仅停止作为正式写入入口
3. 连续观察 Cockpit、journalctl、MySQL 连接数、磁盘写入

回退：
1. 若本地服务出现阻断性故障，立即让团队回到旧服务器入口
2. 本地服务器保留现场日志与数据库，不做破坏性清理
3. 记录故障窗口、模块、日志片段，再回到当前分支修复
```

Expected: 切换和回退都能被明确执行，不依赖口头记忆。

- [ ] **Step 6: 提交验收清单文档**

```bash
git add docs/superpowers/notes/2026-04-22-local-server-acceptance-checklist.md
git commit -m "docs: add local server acceptance checklist"
```
