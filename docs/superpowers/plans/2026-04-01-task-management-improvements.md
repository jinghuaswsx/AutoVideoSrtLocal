# Task Management Improvements Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为任务管理增加三项功能：① 删除 + 重命名（含冲突处理）；② 从任意步骤继续运行；③ 历史项目详情页还原所有已完成步骤的状态。

**Architecture:**
- 后端新增 `PATCH /api/tasks/<task_id>`（重命名）、`DELETE /api/tasks/<task_id>`（软删除）、`POST /api/tasks/<task_id>/resume`（从指定步骤继续）路由；
- `projects` 表新增 `display_name` 列存储用户自定义名称；
- 前端在项目列表卡片加操作菜单，在详情页每个已完成步骤旁加"从此步继续"按钮，并在页面加载时用 `initial_task` 还原全部步骤预览。

**Tech Stack:** Flask, Jinja2, Vanilla JS, MySQL, Socket.IO

---

## 文件清单

| 文件 | 变更类型 | 职责 |
|---|---|---|
| `db/schema.sql` | 修改 | 新增 `display_name` 列 |
| `db/migrate.py` | 无需改 | 已支持幂等执行 |
| `web/routes/projects.py` | 修改 | 查询改用 `display_name`，列表页传数据 |
| `web/routes/task.py` | 修改 | 新增 PATCH / DELETE / resume 路由 |
| `web/templates/projects.html` | 修改 | 卡片加操作菜单（重命名/删除） |
| `web/templates/project_detail.html` | 修改 | 无需改（逻辑在工作台） |
| `web/templates/_task_workbench.html` | 修改 | 每个 done 步骤加"从此步继续"按钮 |
| `web/templates/_task_workbench_scripts.html` | 修改 | 页面加载时还原预览；resume 按钮逻辑 |
| `web/templates/_task_workbench_styles.html` | 修改 | resume 按钮和操作菜单样式 |

---

## Task 1：数据库加 display_name 列

**Files:**
- Modify: `db/schema.sql`

- [ ] **Step 1: 在 schema.sql 的 projects 表加列**

在 `original_filename` 行之后加一行：

```sql
    display_name     VARCHAR(255),
```

完整 projects 表变为：
```sql
CREATE TABLE IF NOT EXISTS projects (
    id               VARCHAR(36) PRIMARY KEY,
    user_id          INT NOT NULL,
    original_filename VARCHAR(255),
    display_name     VARCHAR(255),
    thumbnail_path   VARCHAR(512),
    status           VARCHAR(32) NOT NULL DEFAULT 'uploaded',
    task_dir         VARCHAR(512),
    state_json       LONGTEXT,
    created_at       DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    expires_at       DATETIME NOT NULL,
    deleted_at       DATETIME,
    FOREIGN KEY (user_id) REFERENCES users(id)
);
```

- [ ] **Step 2: 在服务器执行 ALTER TABLE 补列（schema.sql 幂等，不会重复建表，但已有列不会自动补）**

SSH 到服务器手动加列：
```bash
ssh -i "C:\Users\admin\.ssh\CC.pem" root@172.30.254.14 \
  "mysql auto_video -e \"ALTER TABLE projects ADD COLUMN IF NOT EXISTS display_name VARCHAR(255) AFTER original_filename;\""
```

- [ ] **Step 3: 本地验证**

```bash
# 本地 mysql 也执行（如有本地 DB）
mysql auto_video -e "ALTER TABLE projects ADD COLUMN IF NOT EXISTS display_name VARCHAR(255) AFTER original_filename;"
# 查看结果
mysql auto_video -e "DESCRIBE projects;" | grep display_name
```

Expected: 有一行 `display_name | varchar(255) | YES | ...`

- [ ] **Step 4: 提交**

```bash
git add db/schema.sql
git commit -m "feat: add display_name column to projects table"
```

---

## Task 2：命名规则 + 冲突处理工具函数

**Files:**
- Modify: `web/routes/task.py`

**规则：**
- 默认名 = `original_filename` 去掉扩展名后取前 10 个字符
- 重名时在末尾加 ` (2)`、` (3)` … 直到不冲突为止
- 重命名时检查同用户下是否已有相同 `display_name`（排除自身）

- [ ] **Step 1: 在 `web/routes/task.py` 顶部 import 区加 DB 引用**

找到文件顶部已有的：
```python
from web import store
```
在其下方加：
```python
from appcore.db import query_one as db_query_one, execute as db_execute, query as db_query
```

（若已有部分 import 则合并，不重复）

- [ ] **Step 2: 在 `web/routes/task.py` 的 `_parse_bool` 函数之后加工具函数**

```python
def _default_display_name(original_filename: str) -> str:
    """取文件名（去扩展名）前10个字符作为默认展示名。"""
    name = os.path.splitext(original_filename)[0] if original_filename else ""
    return name[:10] or "未命名"


def _resolve_name_conflict(user_id: int, desired_name: str, exclude_task_id: str | None = None) -> str:
    """
    检查 desired_name 是否已被同用户其他项目占用。
    若冲突则在末尾追加 (2)、(3)… 直到不冲突。
    exclude_task_id: 重命名时排除自身。
    """
    base = desired_name
    candidate = base
    n = 2
    while True:
        if exclude_task_id:
            row = db_query_one(
                "SELECT id FROM projects WHERE user_id=%s AND display_name=%s AND id!=%s AND deleted_at IS NULL",
                (user_id, candidate, exclude_task_id),
            )
        else:
            row = db_query_one(
                "SELECT id FROM projects WHERE user_id=%s AND display_name=%s AND deleted_at IS NULL",
                (user_id, candidate),
            )
        if not row:
            return candidate
        candidate = f"{base} ({n})"
        n += 1
```

- [ ] **Step 3: 在 `upload()` 路由保存任务后，计算并写入 display_name**

找到 `upload()` 函数里的：
```python
    store.create(task_id, video_path, task_dir,
                 original_filename=os.path.basename(file.filename),
                 user_id=user_id)
```

在其下方（在 `thumb = ...` 之前）插入：
```python
    if user_id is not None:
        default_name = _default_display_name(os.path.basename(file.filename))
        display_name = _resolve_name_conflict(user_id, default_name)
        db_execute("UPDATE projects SET display_name=%s WHERE id=%s", (display_name, task_id))
```

- [ ] **Step 4: 提交**

```bash
git add web/routes/task.py
git commit -m "feat: auto-generate display_name with conflict resolution on upload"
```

---

## Task 3：重命名 + 删除 API 路由

**Files:**
- Modify: `web/routes/task.py`

- [ ] **Step 1: 在 `web/routes/task.py` 末尾加 PATCH 路由（重命名）**

```python
@bp.route("/<task_id>", methods=["PATCH"])
@login_required
def rename_task(task_id):
    """重命名任务展示名称"""
    row = db_query_one(
        "SELECT id, user_id FROM projects WHERE id=%s AND user_id=%s AND deleted_at IS NULL",
        (task_id, current_user.id),
    )
    if not row:
        return jsonify({"error": "Task not found"}), 404

    body = request.get_json(silent=True) or {}
    new_name = (body.get("display_name") or "").strip()
    if not new_name:
        return jsonify({"error": "display_name required"}), 400
    if len(new_name) > 50:
        return jsonify({"error": "名称不超过50个字符"}), 400

    resolved = _resolve_name_conflict(current_user.id, new_name, exclude_task_id=task_id)
    db_execute("UPDATE projects SET display_name=%s WHERE id=%s", (resolved, task_id))
    return jsonify({"status": "ok", "display_name": resolved})
```

- [ ] **Step 2: 在 `web/routes/task.py` 末尾加 DELETE 路由（软删除）**

```python
@bp.route("/<task_id>", methods=["DELETE"])
@login_required
def delete_task(task_id):
    """软删除任务（设置 deleted_at）"""
    row = db_query_one(
        "SELECT id FROM projects WHERE id=%s AND user_id=%s AND deleted_at IS NULL",
        (task_id, current_user.id),
    )
    if not row:
        return jsonify({"error": "Task not found"}), 404

    from datetime import datetime
    db_execute(
        "UPDATE projects SET deleted_at=%s WHERE id=%s",
        (datetime.utcnow(), task_id),
    )
    # 同步清理内存中的任务状态
    store.update(task_id, status="deleted")
    return jsonify({"status": "ok"})
```

- [ ] **Step 3: 提交**

```bash
git add web/routes/task.py
git commit -m "feat: add PATCH rename and DELETE soft-delete API for tasks"
```

---

## Task 4：从指定步骤继续 API 路由

**Files:**
- Modify: `web/routes/task.py`

- [ ] **Step 1: 在 `web/routes/task.py` 末尾加 resume 路由**

```python
RESUMABLE_STEPS = ["extract", "asr", "alignment", "translate", "tts", "subtitle", "compose", "export"]

@bp.route("/<task_id>/resume", methods=["POST"])
@login_required
def resume_from_step(task_id):
    """从指定步骤重新开始流水线，该步骤之前已完成的结果保留不动。"""
    task = store.get(task_id)
    if not task:
        return jsonify({"error": "Task not found"}), 404

    row = db_query_one(
        "SELECT id FROM projects WHERE id=%s AND user_id=%s AND deleted_at IS NULL",
        (task_id, current_user.id),
    )
    if not row:
        return jsonify({"error": "Task not found"}), 404

    body = request.get_json(silent=True) or {}
    start_step = body.get("start_step", "")
    if start_step not in RESUMABLE_STEPS:
        return jsonify({"error": f"start_step must be one of {RESUMABLE_STEPS}"}), 400

    # 把 start_step 及之后的步骤状态重置为 pending
    steps = task.get("steps", {})
    started = False
    for s in RESUMABLE_STEPS:
        if s == start_step:
            started = True
        if started:
            store.set_step(task_id, s, "pending")
            store.set_step_message(task_id, s, "等待中...")

    store.update(task_id, status="running", current_review_step="")

    # 保留 voice_id / subtitle_position / interactive_review 等配置不变
    user_id = current_user.id if current_user.is_authenticated else None
    pipeline_runner.resume(task_id, start_step, user_id=user_id)
    return jsonify({"status": "started", "start_step": start_step})
```

- [ ] **Step 2: 确认 `pipeline_runner.resume()` 接受 `user_id` 参数**

读取 `web/services/pipeline_runner.py`，找到 `resume()` 函数签名。若不接受 `user_id`，则改调用为：
```python
pipeline_runner.resume(task_id, start_step)
```
（去掉 `user_id=user_id`，保持现有签名不变）

- [ ] **Step 3: 提交**

```bash
git add web/routes/task.py
git commit -m "feat: add POST /api/tasks/<id>/resume to restart pipeline from any step"
```

---

## Task 5：项目列表页 — 展示 display_name + 操作菜单

**Files:**
- Modify: `web/routes/projects.py`
- Modify: `web/templates/projects.html`
- Modify: `web/templates/_task_workbench_styles.html`

- [ ] **Step 1: 更新 `projects.py` 的 index 查询，多取 display_name**

将：
```python
    rows = query(
        """SELECT id, original_filename, thumbnail_path, status, created_at, expires_at, deleted_at
           FROM projects WHERE user_id = %s ORDER BY created_at DESC""",
        (current_user.id,),
    )
```
改为：
```python
    rows = query(
        """SELECT id, original_filename, display_name, thumbnail_path, status, created_at, expires_at, deleted_at
           FROM projects WHERE user_id = %s AND deleted_at IS NULL ORDER BY created_at DESC""",
        (current_user.id,),
    )
```

注意加了 `AND deleted_at IS NULL`，已软删除的不再展示。

- [ ] **Step 2: 更新 `projects.html`，展示 display_name，并在卡片上加操作菜单**

将 `{% block extra_style %}` 内的 `.project-card` 部分和 `{% block content %}` 完整替换为：

```html
{% block extra_style %}
.page-header { display: flex; align-items: center; justify-content: space-between; margin-bottom: 24px; }
.page-header h1 { font-size: 20px; font-weight: 700; color: #111827; }
.grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(240px, 1fr)); gap: 16px; }
.project-card {
  background: #fff;
  border: 1.5px solid #e5e7eb;
  border-radius: 14px;
  overflow: hidden;
  text-decoration: none;
  color: inherit;
  display: block;
  position: relative;
  transition: border-color .15s, box-shadow .15s;
}
.project-card:hover { border-color: #7c6fe0; box-shadow: 0 4px 16px rgba(124,111,224,0.12); }
.project-card .thumb { width: 100%; height: 140px; background: #f3f4f6; display: flex; align-items: center; justify-content: center; color: #d1d5db; font-size: 32px; overflow: hidden; }
.project-card .thumb img { width: 100%; height: 140px; object-fit: cover; }
.project-card .info { padding: 14px; }
.project-card .filename { font-size: 14px; font-weight: 600; color: #111827; margin-bottom: 8px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.project-card .meta { font-size: 12px; color: #9ca3af; display: flex; justify-content: space-between; align-items: center; }
.badge { display: inline-block; padding: 3px 10px; border-radius: 999px; font-size: 11px; font-weight: 700; }
.badge-done { background: #dcfce7; color: #16a34a; }
.badge-running { background: #ede9fe; color: #7c3aed; }
.badge-expired { background: #f3f4f6; color: #9ca3af; }
.badge-uploaded { background: #dbeafe; color: #2563eb; }
.badge-error { background: #fee2e2; color: #dc2626; }
.empty { text-align: center; padding: 80px 0; color: #9ca3af; }
/* 操作菜单 */
.card-menu-btn {
  position: absolute; top: 10px; right: 10px;
  width: 28px; height: 28px;
  background: rgba(255,255,255,0.85);
  border: 1px solid #e5e7eb;
  border-radius: 8px;
  display: flex; align-items: center; justify-content: center;
  cursor: pointer; font-size: 14px; z-index: 2;
  backdrop-filter: blur(4px);
}
.card-menu-btn:hover { background: #fff; border-color: #7c6fe0; }
.card-menu {
  display: none; position: absolute; top: 44px; right: 10px;
  background: #fff; border: 1.5px solid #e5e7eb; border-radius: 10px;
  box-shadow: 0 4px 16px rgba(0,0,0,0.1);
  z-index: 10; min-width: 130px; overflow: hidden;
}
.card-menu.open { display: block; }
.card-menu a, .card-menu button {
  display: block; width: 100%; text-align: left;
  padding: 10px 14px; font-size: 13px; color: #374151;
  background: none; border: none; cursor: pointer; text-decoration: none;
  font-family: inherit;
}
.card-menu a:hover, .card-menu button:hover { background: #f3f4f6; }
.card-menu button.danger { color: #dc2626; }
.card-menu button.danger:hover { background: #fef2f2; }
/* 重命名弹窗 */
.rename-modal-overlay {
  display: none; position: fixed; inset: 0;
  background: rgba(0,0,0,0.3); z-index: 100;
  align-items: center; justify-content: center;
}
.rename-modal-overlay.open { display: flex; }
.rename-modal {
  background: #fff; border-radius: 16px; padding: 28px;
  width: 360px; box-shadow: 0 20px 60px rgba(0,0,0,0.15);
}
.rename-modal h3 { font-size: 16px; font-weight: 700; margin-bottom: 16px; }
.rename-modal input {
  width: 100%; background: #f9fafb; border: 1.5px solid #e5e7eb;
  border-radius: 10px; color: #111827; padding: 10px 12px; font-size: 14px;
  font-family: inherit; outline: none; margin-bottom: 16px;
}
.rename-modal input:focus { border-color: #7c6fe0; background: #fff; }
.rename-modal .modal-actions { display: flex; gap: 10px; justify-content: flex-end; }
{% endblock %}
{% block content %}
<div class="page-header">
  <h1>我的项目</h1>
  <a href="{{ url_for('task.upload_page') }}" class="btn btn-primary">+ 新建项目</a>
</div>
{% if projects %}
<div class="grid">
  {% for p in projects %}
  <div class="project-card-wrap" style="position:relative">
    <a class="project-card" href="{{ url_for('projects.detail', task_id=p.id) }}">
      <div class="thumb">
        {% if p.thumbnail_path %}
          <img src="/api/tasks/{{ p.id }}/thumbnail" alt="">
        {% else %}
          🎬
        {% endif %}
      </div>
      <div class="info">
        <div class="filename">{{ p.display_name or (p.original_filename or p.id)[:10] }}</div>
        <div class="meta">
          <span class="badge badge-{{ p.status }}">{{ p.status }}</span>
          <span>{{ p.created_at.strftime('%m-%d %H:%M') if p.created_at else '' }}</span>
        </div>
      </div>
    </a>
    <button class="card-menu-btn" onclick="toggleMenu(event,'menu-{{ p.id }}')">⋯</button>
    <div class="card-menu" id="menu-{{ p.id }}">
      <button onclick="openRename('{{ p.id }}','{{ (p.display_name or (p.original_filename or '')[:10])|e }}')">重命名</button>
      <button class="danger" onclick="deleteTask(event,'{{ p.id }}')">删除</button>
    </div>
  </div>
  {% endfor %}
</div>
{% else %}
<div class="empty">
  <p style="font-size:48px;margin-bottom:16px">🎬</p>
  <p>还没有项目，点击右上角新建</p>
</div>
{% endif %}

<!-- 重命名弹窗 -->
<div class="rename-modal-overlay" id="renameOverlay">
  <div class="rename-modal">
    <h3>重命名项目</h3>
    <input type="text" id="renameInput" maxlength="50" placeholder="输入新名称（最多50字符）">
    <div class="modal-actions">
      <button class="btn btn-ghost btn-sm" onclick="closeRename()">取消</button>
      <button class="btn btn-primary btn-sm" onclick="confirmRename()">确认</button>
    </div>
  </div>
</div>

<script>
let _renameTaskId = null;

function toggleMenu(e, menuId) {
  e.preventDefault(); e.stopPropagation();
  document.querySelectorAll('.card-menu.open').forEach(m => {
    if (m.id !== menuId) m.classList.remove('open');
  });
  document.getElementById(menuId).classList.toggle('open');
}
document.addEventListener('click', () => {
  document.querySelectorAll('.card-menu.open').forEach(m => m.classList.remove('open'));
});

function openRename(taskId, currentName) {
  _renameTaskId = taskId;
  document.getElementById('renameInput').value = currentName;
  document.getElementById('renameOverlay').classList.add('open');
  setTimeout(() => document.getElementById('renameInput').focus(), 50);
  document.querySelectorAll('.card-menu.open').forEach(m => m.classList.remove('open'));
}
function closeRename() {
  document.getElementById('renameOverlay').classList.remove('open');
  _renameTaskId = null;
}
async function confirmRename() {
  const name = document.getElementById('renameInput').value.trim();
  if (!name || !_renameTaskId) return;
  const res = await fetch(`/api/tasks/${_renameTaskId}`, {
    method: 'PATCH',
    headers: {'Content-Type':'application/json'},
    body: JSON.stringify({display_name: name})
  });
  const data = await res.json();
  if (res.ok) {
    location.reload();
  } else {
    alert(data.error || '重命名失败');
  }
}
document.getElementById('renameInput').addEventListener('keydown', e => {
  if (e.key === 'Enter') confirmRename();
  if (e.key === 'Escape') closeRename();
});

async function deleteTask(e, taskId) {
  e.preventDefault(); e.stopPropagation();
  document.querySelectorAll('.card-menu.open').forEach(m => m.classList.remove('open'));
  if (!confirm('确定删除这个项目吗？此操作不可恢复。')) return;
  const res = await fetch(`/api/tasks/${taskId}`, {method: 'DELETE'});
  if (res.ok) {
    location.reload();
  } else {
    const data = await res.json();
    alert(data.error || '删除失败');
  }
}
</script>
{% endblock %}
```

- [ ] **Step 3: 提交**

```bash
git add web/routes/projects.py web/templates/projects.html
git commit -m "feat: show display_name on project cards, add rename/delete menu"
```

---

## Task 6：详情页 — 还原步骤预览 + 从此步继续按钮

**Files:**
- Modify: `web/templates/_task_workbench.html`
- Modify: `web/templates/_task_workbench_scripts.html`
- Modify: `web/templates/_task_workbench_styles.html`

这一步是核心逻辑，分三个小步完成。

### 6a：HTML — 每步加"从此步继续"按钮

- [ ] **Step 1: 在 `_task_workbench.html` 的每个 `.step` 的 `step-main` div 内，step-name 旁边加 resume 按钮**

将 8 个 step 行修改，在 `step-name` div 之后各加一个 resume 按钮。以 extract 为例，从：
```html
<div class="step" id="step-extract"><div class="step-main"><div class="step-icon" id="icon-extract">1</div><div><div class="step-name">音频提取</div><div class="step-msg" id="msg-extract">等待中...</div></div></div><div class="step-preview" id="preview-extract"></div></div>
```
改为（展开写法，便于理解）：

```html
<div class="step" id="step-extract">
  <div class="step-main">
    <div class="step-icon" id="icon-extract">1</div>
    <div style="flex:1">
      <div class="step-name-row">
        <span class="step-name">音频提取</span>
        <button class="resume-btn hidden" id="resume-extract" data-step="extract">从此步继续</button>
      </div>
      <div class="step-msg" id="msg-extract">等待中...</div>
    </div>
  </div>
  <div class="step-preview" id="preview-extract"></div>
</div>
```

对所有 8 个步骤做同样修改（asr/alignment/translate/tts/subtitle/compose/export），data-step 对应步骤名，id 对应 `resume-{step}`。

- [ ] **Step 2: 提交 HTML 改动**

```bash
git add web/templates/_task_workbench.html
git commit -m "feat: add resume button placeholder to each pipeline step"
```

### 6b：CSS — resume 按钮和 step-name-row 样式

- [ ] **Step 3: 在 `_task_workbench_styles.html` 末尾加样式**

```css
.step-name-row { display: flex; align-items: center; gap: 10px; margin-bottom: 2px; }
.resume-btn {
  padding: 3px 10px; border-radius: 999px; font-size: 11px; font-weight: 700;
  border: 1.5px solid #7c6fe0; background: #fff; color: #7c6fe0;
  cursor: pointer; font-family: inherit; transition: background .12s;
}
.resume-btn:hover { background: #ede9fe; }
```

- [ ] **Step 4: 提交 CSS 改动**

```bash
git add web/templates/_task_workbench_styles.html
git commit -m "feat: add styles for resume button and step-name-row"
```

### 6c：JS — 页面加载还原预览 + resume 按钮显示逻辑

- [ ] **Step 5: 在 `_task_workbench_scripts.html` 中找到 `renderTaskState()` 函数（或初始化逻辑），加入还原预览 + resume 按钮显示逻辑**

找到现有的 `renderTaskState()` 或 `applyTaskState()` 函数，在其中处理每步状态时：

**① 步骤状态还原（已有逻辑，确认即可）：** 每个步骤的 `done`/`error`/`running`/`waiting` 状态应已通过 `currentTask.steps` 渲染对应 CSS 类。

**② resume 按钮显示逻辑：** 在更新每步 class 的同时，控制 resume 按钮可见性：

```javascript
const STEP_ORDER = ["extract", "asr", "alignment", "translate", "tts", "subtitle", "compose", "export"];

function updateResumeButtons(task) {
  const steps = task.steps || {};
  const isRunning = task.status === "running";
  STEP_ORDER.forEach(step => {
    const btn = document.getElementById(`resume-${step}`);
    if (!btn) return;
    const stepStatus = steps[step];
    // 只在任务非运行中时，对 done/error/waiting 步骤显示 resume 按钮
    if (!isRunning && (stepStatus === "done" || stepStatus === "error" || stepStatus === "waiting")) {
      btn.classList.remove("hidden");
    } else {
      btn.classList.add("hidden");
    }
  });
}
```

在现有的 `renderTaskState()` 末尾调用：
```javascript
updateResumeButtons(currentTask);
```

**③ resume 按钮点击事件（在初始化代码中绑定一次）：**

```javascript
document.querySelectorAll(".resume-btn").forEach(btn => {
  btn.addEventListener("click", async (e) => {
    e.stopPropagation();
    const step = btn.dataset.step;
    if (!confirm(`从「${btn.closest('.step').querySelector('.step-name').textContent}」步骤重新开始？前面已完成的结果保留不变。`)) return;
    btn.disabled = true;
    btn.textContent = "启动中…";
    const res = await fetch(`/api/tasks/${currentTaskId}/resume`, {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({start_step: step})
    });
    if (res.ok) {
      socket.emit("join_task", {task_id: currentTaskId});
      scheduleRefreshTaskState(200);
    } else {
      const data = await res.json();
      alert(data.error || "启动失败");
      btn.disabled = false;
      btn.textContent = "从此步继续";
    }
  });
});
```

注意：`currentTaskId` 是现有代码中的任务 ID 变量（检查脚本中实际变量名，可能是 `taskId` 或 `TASK_WORKBENCH_CONFIG.taskId`）。

**④ 页面加载时的预览还原：** 找到现有的初始化代码（处理 `TASK_WORKBENCH_CONFIG.initialTask` 的部分），确认在页面加载时调用了 `renderTaskState()` 或等效函数，这样所有已完成步骤的 artifact 预览会自动渲染。如果没有，在 DOMContentLoaded 的末尾加：

```javascript
if (currentTask && Object.keys(currentTask).length > 0) {
  renderTaskState(currentTask);
}
```

- [ ] **Step 6: 提交 JS 改动**

```bash
git add web/templates/_task_workbench_scripts.html
git commit -m "feat: restore step previews on load and show resume buttons for done/error steps"
```

---

## Task 7：发布到服务器

- [ ] **Step 1: 推送代码**

```bash
git push origin master
```

- [ ] **Step 2: SSH 部署**

```bash
ssh -i "C:\Users\admin\.ssh\CC.pem" root@172.30.254.14 \
  "cd /opt/autovideosrt && git pull && mysql auto_video -e \"ALTER TABLE projects ADD COLUMN IF NOT EXISTS display_name VARCHAR(255) AFTER original_filename;\" && systemctl restart autovideosrt && sleep 2 && systemctl status autovideosrt --no-pager"
```

- [ ] **Step 3: 验证**

浏览器访问 `http://172.30.254.14`：
1. 项目列表卡片显示 display_name（前10字）
2. 卡片右上角 ⋯ 按钮 → 重命名弹窗 → 改名后刷新确认
3. ⋯ → 删除 → 确认后卡片消失
4. 进入已完成的项目详情页 → 已完成步骤右侧显示"从此步继续"按钮
5. 点击"从此步继续" → 确认 → 该步骤及之后步骤重新运行

---

## 冲突处理方案说明

| 场景 | 处理方式 |
|---|---|
| 上传同名文件 | 自动追加 `(2)`, `(3)`… |
| 手动重命名为已存在名称 | 服务端自动追加数字后缀，返回实际保存的名称，前端刷新展示 |
| 已删除项目的名称 | `deleted_at IS NULL` 过滤，已删除名称不占用命名空间 |

---

## 自检

**Spec 覆盖：**
- [x] 任务删除 → Task 3 DELETE 路由 + Task 5 前端菜单
- [x] 任务重命名 → Task 2 命名规则 + Task 3 PATCH 路由 + Task 5 前端弹窗
- [x] 重名冲突处理 → Task 2 `_resolve_name_conflict()`
- [x] 默认名前10字 → Task 2 `_default_display_name()`
- [x] 从中断步骤继续 → Task 4 resume 路由 + Task 6 resume 按钮
- [x] 历史项目详情页状态还原 → Task 6c ④ 初始化时 renderTaskState
- [x] 已完成步骤状态保留 → resume 路由只重置 start_step 及之后的步骤

**Placeholder 扫描：** 无 TBD / TODO / 留空。

**类型一致性：** `display_name` 在 SQL / Python / JS 三层使用同一名称；`start_step` 在路由和前端保持一致；`RESUMABLE_STEPS` 与 `STEP_ORDER` 内容一致。
