# Web Platform Plan B — TOS 上传、定时清理、用量统计、体验修复

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 Plan A 基础上完成 TOS 文件托管、24 小时过期清理、LLM 用量日志、管理员统计页，并修复每用户 API Key 未实际生效、上传后不跳转、详情页无实时进度等体验问题。

**Architecture:** Pipeline 完成后写 TOS → 下载走预签名 URL；APScheduler 每小时清理过期项目；pipeline 调用外部 API 时先读用户自己的 key，再 fallback 到系统 `.env`；usage_logs 表记录每次 API 调用。

**Tech Stack:** 已有 Flask + SocketIO + pymysql + APScheduler + tos-python-sdk，无新依赖。

---

## 当前问题（阻塞生产运行）

### 紧急：服务器 .env 缺 API Key

服务器 `/opt/autovideosrt/.env` 里没有任何 API Key，导致 ASR 阶段 TOS 上传 403。
需要手动补充以下内容后重启服务：

```
VOLC_API_KEY=<火山引擎豆包 ASR key>
VOLC_RESOURCE_ID=volc.seedasr.auc
TOS_ACCESS_KEY=<火山引擎 TOS Access Key>
TOS_SECRET_KEY=<火山引擎 TOS Secret Key>
OPENROUTER_API_KEY=<OpenRouter key>
ELEVENLABS_API_KEY=<ElevenLabs key>
```

命令：
```bash
ssh -i C:\Users\admin\.ssh\CC.pem root@172.30.254.14
nano /opt/autovideosrt/.env
systemctl restart autovideosrt
```

---

## File Map

**New files:**
- `appcore/usage_log.py` — `record(user_id, project_id, service, **kwargs)` 写 usage_logs，fire-and-forget
- `appcore/cleanup.py` — 扫描过期项目，删本地文件 + TOS 对象，更新 DB
- `appcore/scheduler.py` — APScheduler 实例 + hourly cleanup job
- `web/templates/admin_usage.html` — 管理员用量统计页
- `web/routes/admin_usage.py` — `/admin/usage` 路由

**Modified files:**
- `appcore/runtime.py` — pipeline 完成后触发 TOS 上传；各步骤调用外部 API 后写 usage_log
- `appcore/api_keys.py` — 新增 `resolve_key(user_id, service)` — 先查用户 key，fallback 到 env
- `pipeline/asr.py` — 用 `resolve_key` 替换直接读 config
- `pipeline/translate.py` — 用 `resolve_key` 替换直接读 config
- `pipeline/tts.py` — 用 `resolve_key` 替换直接读 config
- `web/app.py` — 注册 scheduler、admin_usage 蓝图
- `web/routes/admin.py` — 加 usage 入口链接
- `web/templates/layout.html` — 管理员导航加「用量统计」
- `main.py` — 启动 scheduler
- `web/templates/index.html` — 上传完成后跳转到项目列表（JS）

---

## Task E1: 每用户 API Key 实际生效

**Files:**
- Modify: `appcore/api_keys.py`
- Modify: `pipeline/asr.py`
- Modify: `pipeline/translate.py`
- Modify: `pipeline/tts.py`
- Modify: `appcore/runtime.py`

- [ ] **Step 1: 在 api_keys.py 增加 resolve_key()**

在 `appcore/api_keys.py` 末尾追加：
```python
import os

_SERVICE_ENV_MAP = {
    "doubao_asr":  ("VOLC_API_KEY", None),
    "elevenlabs":  ("ELEVENLABS_API_KEY", None),
    "openrouter":  ("OPENROUTER_API_KEY", None),
}


def resolve_key(user_id: int | None, service: str) -> str | None:
    """Return user's key if set, else fall back to system env."""
    if user_id is not None:
        val = get_key(user_id, service)
        if val:
            return val
    env_name, _ = _SERVICE_ENV_MAP.get(service, (None, None))
    return os.getenv(env_name) if env_name else None


def resolve_extra(user_id: int | None, service: str) -> dict:
    """Return extra_config for user's key, else empty dict."""
    if user_id is not None:
        rows = query(
            "SELECT extra_config FROM api_keys WHERE user_id = %s AND service = %s",
            (user_id, service),
        )
        if rows:
            import json as _json
            extra = rows[0]["extra_config"]
            if isinstance(extra, str):
                try:
                    extra = _json.loads(extra)
                except Exception:
                    extra = {}
            return extra or {}
    return {}
```

- [ ] **Step 2: runtime.py — 把 user_id 传进 PipelineRunner**

读 `appcore/runtime.py`，找 `PipelineRunner.__init__` 和 `start()`，把 `user_id` 存到 `self._user_id`：
```python
class PipelineRunner:
    def __init__(self, bus: EventBus, user_id: int | None = None):
        self.bus = bus
        self._user_id = user_id
```

在 `start(task_id)` 里把 `self._user_id` 写入 task state：
```python
task = task_state.get(task_id)
if task and self._user_id is not None:
    task["_user_id"] = self._user_id
```

- [ ] **Step 3: pipeline_runner.py — 把 user_id 传给 PipelineRunner**

在 `web/services/pipeline_runner.py`：
```python
def start(task_id: str, user_id: int | None = None):
    bus = EventBus()
    bus.subscribe(_make_socketio_handler(task_id))
    runner = PipelineRunner(bus=bus, user_id=user_id)
    thread = threading.Thread(target=runner.start, args=(task_id,), daemon=True)
    thread.start()
```

- [ ] **Step 4: pipeline 步骤读取 user key**

在 pipeline 里各步骤调用外部 API 前，通过 `task_state.get(task_id).get("_user_id")` 拿到 user_id，再调 `resolve_key`。

需要修改的函数（读对应文件再改）：
- `pipeline/asr.py` — doubao ASR 调用处，把 `config.VOLC_API_KEY` 替换为 `resolve_key(user_id, "doubao_asr")`，同理 app_id/cluster 用 `resolve_extra`
- `pipeline/tts.py` — ElevenLabs 初始化处，替换为 `resolve_key(user_id, "elevenlabs")`
- `pipeline/translate.py` 或调用 openrouter 的模块 — 替换为 `resolve_key(user_id, "openrouter")`

- [ ] **Step 5: 测试**

```bash
pytest tests/test_appcore_api_keys.py -v
python main.py
# 在设置页给当前用户填一个有效 key → 跑任务 → 确认用的是用户自己的 key
```

- [ ] **Step 6: Commit**
```bash
git add appcore/api_keys.py appcore/runtime.py web/services/pipeline_runner.py pipeline/
git commit -m "feat: per-user API key resolution in pipeline"
```

---

## Task E2: 上传完成后跳转到项目列表

**Files:**
- Modify: `web/templates/index.html`

- [ ] **Step 1: 找到上传成功的回调**

读 `web/templates/index.html`，找到 `fetch('/api/tasks', ...)` 上传成功的回调，在收到 `task_id` 之后跳转：
```javascript
// 在收到 task_id 后，等任务创建再跳转
window.location.href = '/projects/' + data.task_id;
```

- [ ] **Step 2: 测试**

```bash
python main.py
# 上传视频 → 确认跳转到 /projects/<task_id>
```

- [ ] **Step 3: Commit**
```bash
git add web/templates/index.html
git commit -m "feat: redirect to project detail page after upload"
```

---

## Task E3: 项目详情页实时进度

**Files:**
- Modify: `web/templates/project_detail.html`

- [ ] **Step 1: 加 SocketIO 监听**

在 `project_detail.html` 的 `{% block scripts %}` 里加：
```html
{% block scripts %}
<script>
const taskId = "{{ project.id }}";
const socket = io();
socket.emit('join_task', {task_id: taskId});

const STEP_LABELS = {
  extract: '音频提取', asr: '语音识别', alignment: '分段对齐',
  translate: '本土化翻译', tts: '英文配音', subtitle: '字幕生成',
  compose: '视频合成', export: 'CapCut 导出'
};

socket.on('step_update', (data) => {
  const el = document.getElementById('step-' + data.step);
  if (el) {
    el.className = 'step-card step-' + data.status;
    el.querySelector('h3').textContent = STEP_LABELS[data.step] + ' — ' + data.status;
  }
});

socket.on('pipeline_done', () => {
  document.getElementById('pipeline-status').textContent = '✅ 处理完成';
  setTimeout(() => location.reload(), 1500);
});

socket.on('pipeline_error', (data) => {
  document.getElementById('pipeline-status').textContent = '❌ 错误: ' + (data.message || '');
});
</script>
{% endblock %}
```

每个步骤 card 加 id：
```html
<div class="step-card" id="step-{{ step_id }}">
```

- [ ] **Step 2: 样式**

在 `{% block extra_style %}` 追加：
```css
.step-running { border-color: #fe2c55; }
.step-done { border-color: #4ade80; }
.step-error { border-color: #f87171; }
```

- [ ] **Step 3: Commit**
```bash
git add web/templates/project_detail.html
git commit -m "feat: realtime step progress on project detail page"
```

---

## Task B1: TOS 上传 + 预签名下载

**Files:**
- Modify: `appcore/runtime.py`
- Modify: `web/routes/projects.py` (download endpoint)
- Modify: `web/templates/project_detail.html` (download links)

- [ ] **Step 1: pipeline 完成后上传到 TOS**

在 `appcore/runtime.py`，EVT_PIPELINE_DONE 之前，加 TOS 上传逻辑：
```python
def _upload_artifacts_to_tos(task: dict) -> None:
    """Upload final artifacts to TOS. Errors are silently ignored."""
    try:
        import tos as tos_sdk
        import config
        client = tos_sdk.TosClientV2(
            ak=config.TOS_ACCESS_KEY, sk=config.TOS_SECRET_KEY,
            endpoint=config.TOS_ENDPOINT, region=config.TOS_REGION,
        )
        user_id = task.get("_user_id", "anon")
        task_id = task["id"]
        tos_uploads = {}
        upload_targets = []

        # soft_video normal + hook_cta, hard_video, srt
        for variant in ["normal", "hook_cta"]:
            vs = task.get("variants", {}).get(variant, {})
            for key in ["soft_video", "srt"]:
                path = vs.get("result", {}).get(key) or vs.get("preview_files", {}).get(key)
                if path and os.path.exists(path):
                    upload_targets.append((path, f"{user_id}/{task_id}/{variant}/{os.path.basename(path)}"))

        for local_path, tos_key in upload_targets:
            client.put_object_from_file(config.TOS_BUCKET, tos_key, local_path)
            tos_uploads[tos_key] = tos_key

        if tos_uploads:
            task["tos_uploads"] = tos_uploads
            from appcore.db import execute as db_exec
            import json
            db_exec("UPDATE projects SET state_json = %s WHERE id = %s",
                    (json.dumps(task, default=str), task_id))
    except Exception:
        pass  # TOS upload never blocks pipeline completion
```

- [ ] **Step 2: 下载路由走预签名 URL**

在 `web/routes/projects.py` 加下载端点：
```python
@bp.route("/projects/<task_id>/download/<path:tos_key>")
@login_required
def download(task_id: str, tos_key: str):
    row = query_one("SELECT state_json, deleted_at FROM projects WHERE id = %s AND user_id = %s",
                    (task_id, current_user.id))
    if not row:
        abort(404)
    if row.get("deleted_at"):
        return "项目已过期", 410
    import tos as tos_sdk, config
    client = tos_sdk.TosClientV2(
        ak=config.TOS_ACCESS_KEY, sk=config.TOS_SECRET_KEY,
        endpoint=config.TOS_ENDPOINT, region=config.TOS_REGION,
    )
    url = client.pre_signed_url("GET", config.TOS_BUCKET, tos_key, expires=3600).signed_url
    return redirect(url)
```

- [ ] **Step 3: 详情页下载链接改用预签名路由**

修改 `project_detail.html` 中下载链接：
```html
{% for tos_key, _ in state.get('tos_uploads', {}).items() %}
  <a class="download-link" href="{{ url_for('projects.download', task_id=project.id, tos_key=tos_key) }}">
    ⬇ {{ tos_key.split('/')[-1] }}
  </a>
{% endfor %}
```

- [ ] **Step 4: Commit**
```bash
git add appcore/runtime.py web/routes/projects.py web/templates/project_detail.html
git commit -m "feat: upload artifacts to TOS, presigned URL downloads"
```

---

## Task B2: 24 小时过期 + APScheduler 定时清理

**Files:**
- Create: `appcore/cleanup.py`
- Create: `appcore/scheduler.py`
- Modify: `main.py`

- [ ] **Step 1: 写 appcore/cleanup.py**

```python
"""Hourly cleanup: delete expired project files and TOS objects."""
from __future__ import annotations
import os
import shutil
import logging

from appcore.db import query, execute

log = logging.getLogger(__name__)


def run_cleanup() -> None:
    rows = query(
        "SELECT id, task_dir, user_id, state_json FROM projects "
        "WHERE expires_at < NOW() AND deleted_at IS NULL"
    )
    for row in rows:
        task_id = row["id"]
        task_dir = row.get("task_dir") or ""
        try:
            # Delete local files
            if task_dir and os.path.isdir(task_dir):
                shutil.rmtree(task_dir, ignore_errors=True)
            # Delete TOS objects
            _delete_tos_objects(row)
            # Mark deleted
            execute(
                "UPDATE projects SET deleted_at = NOW(), status = 'expired' WHERE id = %s",
                (task_id,),
            )
            log.info("Cleaned up expired project %s", task_id)
        except Exception as e:
            log.error("Cleanup failed for %s: %s", task_id, e)


def _delete_tos_objects(row: dict) -> None:
    try:
        import json, tos as tos_sdk, config
        state = json.loads(row["state_json"]) if row.get("state_json") else {}
        tos_uploads = state.get("tos_uploads", {})
        if not tos_uploads:
            return
        client = tos_sdk.TosClientV2(
            ak=config.TOS_ACCESS_KEY, sk=config.TOS_SECRET_KEY,
            endpoint=config.TOS_ENDPOINT, region=config.TOS_REGION,
        )
        for tos_key in tos_uploads:
            try:
                client.delete_object(config.TOS_BUCKET, tos_key)
            except Exception:
                pass
    except Exception:
        pass
```

- [ ] **Step 2: 写 appcore/scheduler.py**

```python
from apscheduler.schedulers.background import BackgroundScheduler

_scheduler: BackgroundScheduler | None = None


def get_scheduler() -> BackgroundScheduler:
    global _scheduler
    if _scheduler is None:
        _scheduler = BackgroundScheduler(timezone="Asia/Shanghai")
        from appcore.cleanup import run_cleanup
        _scheduler.add_job(run_cleanup, "interval", hours=1, id="cleanup")
    return _scheduler
```

- [ ] **Step 3: main.py 启动 scheduler**

读 `main.py`，在 `app = create_app()` 后追加：
```python
from appcore.scheduler import get_scheduler
scheduler = get_scheduler()
scheduler.start()
```

- [ ] **Step 4: Commit**
```bash
git add appcore/cleanup.py appcore/scheduler.py main.py
git commit -m "feat: add APScheduler hourly cleanup for expired projects"
```

---

## Task B3: LLM 用量日志 + 管理员统计页

**Files:**
- Create: `appcore/usage_log.py`
- Create: `web/routes/admin_usage.py`
- Create: `web/templates/admin_usage.html`
- Modify: `pipeline/asr.py`, `pipeline/tts.py`, `pipeline/translate.py`
- Modify: `web/app.py`, `web/templates/layout.html`

- [ ] **Step 1: 写 appcore/usage_log.py**

```python
"""Fire-and-forget usage logging. Never raises."""
from __future__ import annotations
import logging

log = logging.getLogger(__name__)


def record(
    user_id: int,
    project_id: str | None,
    service: str,
    *,
    model_name: str | None = None,
    success: bool = True,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    audio_duration_seconds: float | None = None,
    extra_data: dict | None = None,
) -> None:
    try:
        import json
        from appcore.db import execute
        execute(
            """INSERT INTO usage_logs
               (user_id, project_id, service, model_name, success,
                input_tokens, output_tokens, audio_duration_seconds, extra_data)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (user_id, project_id, service, model_name, int(success),
             input_tokens, output_tokens, audio_duration_seconds,
             json.dumps(extra_data) if extra_data else None),
        )
    except Exception as e:
        log.debug("usage_log.record failed: %s", e)
```

- [ ] **Step 2: pipeline 步骤调用后写 usage_log**

在 `pipeline/asr.py` ASR 完成后：
```python
from appcore.usage_log import record as log_usage
# after successful ASR:
log_usage(user_id, task_id, "doubao_asr",
          audio_duration_seconds=audio_duration_seconds, success=True)
```

在 `pipeline/tts.py` TTS 完成后：
```python
log_usage(user_id, task_id, "elevenlabs",
          audio_duration_seconds=generated_audio_seconds, success=True)
```

在 translate/openrouter 调用后：
```python
log_usage(user_id, task_id, "openrouter",
          model_name=model, input_tokens=usage.prompt_tokens,
          output_tokens=usage.completion_tokens, success=True)
```

- [ ] **Step 3: 写 web/routes/admin_usage.py**

```python
from flask import Blueprint, render_template, request
from flask_login import login_required
from web.auth import admin_required
from appcore.db import query

bp = Blueprint("admin_usage", __name__, url_prefix="/admin")


@bp.route("/usage")
@login_required
@admin_required
def usage():
    service = request.args.get("service", "")
    date_from = request.args.get("from", "")
    date_to = request.args.get("to", "")

    where = "WHERE 1=1"
    args = []
    if service:
        where += " AND ul.service = %s"; args.append(service)
    if date_from:
        where += " AND DATE(ul.called_at) >= %s"; args.append(date_from)
    if date_to:
        where += " AND DATE(ul.called_at) <= %s"; args.append(date_to)

    rows = query(f"""
        SELECT u.username, ul.service, DATE(ul.called_at) AS day,
               COUNT(*) AS calls,
               SUM(ul.input_tokens) AS input_tokens,
               SUM(ul.output_tokens) AS output_tokens,
               SUM(ul.audio_duration_seconds) AS audio_seconds
        FROM usage_logs ul
        JOIN users u ON u.id = ul.user_id
        {where}
        GROUP BY u.username, ul.service, day
        ORDER BY day DESC, u.username
    """, tuple(args))
    return render_template("admin_usage.html", rows=rows, service=service,
                           date_from=date_from, date_to=date_to)
```

- [ ] **Step 4: 写 web/templates/admin_usage.html**

```html
{% extends "layout.html" %}
{% block title %}用量统计 — AutoVideoSrt{% endblock %}
{% block extra_style %}
table { width:100%; border-collapse:collapse; }
th { text-align:left; color:#98a0af; font-size:12px; text-transform:uppercase; padding:8px 12px; border-bottom:1px solid #282c36; }
td { padding:10px 12px; border-bottom:1px solid #1f222b; font-size:14px; }
.filter-bar { display:flex; gap:12px; margin-bottom:20px; flex-wrap:wrap; }
.filter-bar input,.filter-bar select { background:#22262f; border:1px solid #343946; border-radius:8px; color:#eff2f8; padding:8px 10px; font-size:13px; }
.filter-bar button { background:#fe2c55; color:white; border:none; border-radius:8px; padding:8px 16px; font-size:13px; font-weight:700; cursor:pointer; }
{% endblock %}
{% block content %}
<h1 style="font-size:20px;margin-bottom:20px">用量统计</h1>
<form method="get" class="filter-bar">
  <select name="service">
    <option value="">全部服务</option>
    <option value="doubao_asr" {% if service=='doubao_asr' %}selected{% endif %}>豆包 ASR</option>
    <option value="elevenlabs" {% if service=='elevenlabs' %}selected{% endif %}>ElevenLabs</option>
    <option value="openrouter" {% if service=='openrouter' %}selected{% endif %}>OpenRouter</option>
  </select>
  <input type="date" name="from" value="{{ date_from }}">
  <input type="date" name="to" value="{{ date_to }}">
  <button type="submit">筛选</button>
</form>
<table>
  <thead><tr><th>日期</th><th>用户</th><th>服务</th><th>调用次数</th><th>输入 Tokens</th><th>输出 Tokens</th><th>音频时长(s)</th></tr></thead>
  <tbody>
    {% for r in rows %}
    <tr>
      <td>{{ r.day }}</td><td>{{ r.username }}</td><td>{{ r.service }}</td>
      <td>{{ r.calls }}</td>
      <td>{{ r.input_tokens or '-' }}</td>
      <td>{{ r.output_tokens or '-' }}</td>
      <td>{{ '%.1f'|format(r.audio_seconds) if r.audio_seconds else '-' }}</td>
    </tr>
    {% endfor %}
    {% if not rows %}<tr><td colspan="7" style="text-align:center;color:#3a3f4d;padding:40px">暂无数据</td></tr>{% endif %}
  </tbody>
</table>
{% endblock %}
```

- [ ] **Step 5: 注册蓝图，更新导航**

`web/app.py` 加：
```python
from web.routes.admin_usage import bp as admin_usage_bp
app.register_blueprint(admin_usage_bp)
```

`web/templates/layout.html` 管理员导航加：
```html
<a href="{{ url_for('admin_usage.usage') }}" ...>用量统计</a>
```

- [ ] **Step 6: Commit**
```bash
git add appcore/usage_log.py web/routes/admin_usage.py web/templates/admin_usage.html pipeline/ web/app.py web/templates/layout.html
git commit -m "feat: LLM usage logging and admin usage stats page"
```

---

## 部署顺序建议

```
E2 (跳转) → E1 (用户 key 生效) → E3 (实时进度) → B2 (清理) → B3 (用量) → B1 (TOS 下载)
```

E1/E2/E3 直接影响用户体验，优先做。B1 依赖 TOS 配置，B2/B3 可并行。

---

## 紧急操作（现在就要做）

服务器上跑的两个任务卡在 ASR 是因为 `.env` 缺 API Key，需要手动填写后重启：

```bash
ssh -i C:\Users\admin\.ssh\CC.pem root@172.30.254.14
nano /opt/autovideosrt/.env
# 填入 VOLC_API_KEY, TOS_ACCESS_KEY, TOS_SECRET_KEY, OPENROUTER_API_KEY, ELEVENLABS_API_KEY
systemctl restart autovideosrt
```

之后重新上传视频即可正常运行。
