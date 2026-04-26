# E 子系统：task-medias-bridge 实施计划

**Goal:** 把任务中心和素材管理页深度打通：子任务详情显示 readiness 状态 + /medias/ 检测 from_task query 自动定位产品。

**Spec:** [docs/superpowers/specs/2026-04-26-task-medias-bridge-design.md](../specs/2026-04-26-task-medias-bridge-design.md)

---

## File Structure

### Modified
| 路径 | 修改 |
|---|---|
| `web/routes/tasks.py` | 加 `GET /tasks/api/child/<id>/readiness` |
| `web/templates/tasks_list.html` | 子任务详情抽屉加"翻译产物状态"面板 |
| `web/templates/medias_list.html` | DOMContentLoaded 检 from_task query → banner + 自动定位 |
| `tests/test_tasks_routes.py` | 加 readiness 端点 smoke test |

无新文件。

---

## Task 索引

| # | 标题 | Phase |
|---|---|---|
| 1 | tasks.py 加 readiness endpoint + tests | API |
| 2 | tasks_list.html 子任务详情加 readiness 面板 | Frontend |
| 3 | medias_list.html 检 from_task query + banner + 自动定位产品 | Frontend |
| 4 | 最终验收 + 生产部署 | Verify |

---

## Conventions

- worktree：`g:/Code/AutoVideoSrtLocal/.worktrees/task-medias-bridge`，分支 `feature/task-medias-bridge`
- commit：`<type>(task-medias-bridge): <subject>` + Co-Authored-By
- service 测试在 server 跑；route 测试用 authed_client_no_db

---

## Task 1: readiness endpoint

### Step 1: 加 endpoint test

```python
# tests/test_tasks_routes.py 末尾
def test_child_readiness_endpoint_smoke(authed_client_no_db):
    rsp = authed_client_no_db.get("/tasks/api/child/9999/readiness")
    assert rsp.status_code in (200, 404, 500)
```

### Step 2: 加 endpoint to web/routes/tasks.py 末尾

```python
@bp.route("/api/child/<int:tid>/readiness", methods=["GET"])
@login_required
def api_child_readiness(tid: int):
    """返回子任务对应语种 media_item 的 readiness 状态。"""
    from appcore.db import query_one
    from appcore import pushes
    row = query_one(
        "SELECT t.media_product_id, t.country_code "
        "FROM tasks t WHERE t.id=%s AND t.parent_task_id IS NOT NULL",
        (tid,)
    )
    if not row:
        return jsonify({"error": "child task not found"}), 404
    item = tasks_svc._find_target_lang_item(row["media_product_id"], row["country_code"])
    if not item:
        return jsonify({
            "ready": False, "missing": ["lang_item_missing"],
            "country_code": row["country_code"],
            "readiness": {},
        })
    product = tasks_svc._find_product(row["media_product_id"])
    readiness = pushes.compute_readiness(item, product)
    is_ready = pushes.is_ready(readiness)
    missing = [k for k, v in readiness.items()
               if not str(k).endswith("_reason") and not v]
    return jsonify({
        "ready": is_ready,
        "missing": missing,
        "readiness": {k: bool(v) for k, v in readiness.items()
                      if not str(k).endswith("_reason")},
        "country_code": row["country_code"],
        "media_item_id": item["id"],
    })
```

### Step 3-5: commit + push + restart server + verify

```bash
git -C g:/Code/AutoVideoSrtLocal/.worktrees/task-medias-bridge add web/routes/tasks.py tests/test_tasks_routes.py
git -C g:/Code/AutoVideoSrtLocal/.worktrees/task-medias-bridge commit -m "$(cat <<'EOF'
feat(task-medias-bridge): GET /tasks/api/child/<id>/readiness

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
git -C g:/Code/AutoVideoSrtLocal/.worktrees/task-medias-bridge push -u origin feature/task-medias-bridge

ssh -i C:/Users/admin/.ssh/CC.pem -o StrictHostKeyChecking=no root@172.30.254.14 \
  'cd /opt/autovideosrt-test && git fetch && git checkout feature/task-medias-bridge && git pull && systemctl restart autovideosrt-test && sleep 3 && /opt/autovideosrt/venv/bin/python -m pytest tests/test_tasks_routes.py -q 2>&1 | tail -5'

curl -sS -o /dev/null -w "/tasks/api/child/1/readiness  HTTP %{http_code}\n" http://172.30.254.14:8080/tasks/api/child/1/readiness
```

---

## Task 2: tasks_list.html readiness 面板

In `tcRenderDetail()` function, when rendering a child task (isParent=false), append a readiness section between the badges and the buttons.

Locate the `else` branch in `tcDetailButtons` or directly in `tcRenderDetail`'s child rendering. Add async fetch + render:

```javascript
// In tcRenderDetail, after the existing child header info:
if (!isParent) {
  // Insert placeholder, fetch async, render
  ...
}
```

The cleanest is to render placeholder synchronously, then async-fill.

In `tcRenderDetail()`, add for child (after `${task.last_reason ? ...}` line):

```javascript
${!isParent ? `<div id="tcReadinessPanel" style="background:var(--tc-bg-subtle); padding:10px 12px; border-radius:6px; margin-bottom:12px; font-size:12px;">
  <div style="font-weight:600; color:var(--tc-fg-muted); margin-bottom:6px;">翻译产物状态 (${tcEsc(task.country_code)})</div>
  <div id="tcReadinessRows">加载中...</div>
</div>` : ''}
```

After `body.innerHTML = ...` is set, kick off fetch:

```javascript
// At end of tcOpenDetail, after rendering:
if (!task.parent_task_id) {
  fetch('/tasks/api/child/' + id + '/readiness').then(r => r.json()).then(data => {
    const wrap = document.getElementById('tcReadinessRows');
    if (!wrap) return;
    if (data.error) { wrap.textContent = data.error; return; }
    const labels = {
      has_video: '视频', has_cover: '封面',
      has_copywriting: '文案', has_push_texts: '推送文案',
      is_listed: '商品在架',
    };
    const items = Object.entries(data.readiness || {}).map(([k, v]) => {
      const label = labels[k] || k;
      const icon = v ? '✓' : '✗';
      const color = v ? 'oklch(38% 0.09 165)' : 'oklch(58% 0.18 25)';
      return `<span style="margin-right:14px; color:${color};"><strong>${icon}</strong> ${label}</span>`;
    }).join('');
    wrap.innerHTML = items || '<span style="color:var(--tc-fg-muted);">无数据</span>';
  }).catch(e => {
    const wrap = document.getElementById('tcReadinessRows');
    if (wrap) wrap.textContent = '加载失败';
  });
}
```

(integrate into existing tcOpenDetail — find the right spot)

commit + push + restart + verify with curl

---

## Task 3: medias_list.html — from_task banner + 自动定位

⚠️ FIRST READ medias_list.html to find:
- The product list rendering (where each row is built)
- The product edit modal opener function (likely `openEditModal(productId)` or similar)
- Whether there's a way to set lang tab inside the edit modal

### Add at end of script block in medias_list.html:

```javascript
// task-medias-bridge: detect from_task query, auto-locate product + open edit modal + show banner
(function() {
  const params = new URLSearchParams(window.location.search);
  const fromTask = params.get('from_task');
  if (!fromTask) return;
  const productId = parseInt(params.get('product') || '0');
  const itemId = parseInt(params.get('item') || '0');
  const lang = params.get('lang');
  const action = params.get('action');

  // Insert banner at top of main content
  const banner = document.createElement('div');
  banner.id = 'mbridgeBanner';
  banner.style.cssText = 'background:var(--oc-accent-subtle); border-left:4px solid var(--oc-accent); padding:12px 16px; margin-bottom:12px; border-radius:6px;';
  banner.innerHTML = `
    <strong>来自任务中心 任务 #${fromTask}</strong>
    ${productId ? ' · 产品 #' + productId : ''}
    ${lang ? ' · 语种 <code>' + lang.toUpperCase() + '</code>' : ''}
    ${action ? ' · 动作 <code>' + action + '</code>' : ''}
    <a href="/tasks/" target="_blank" style="float:right;">返回任务中心 →</a>
  `;
  // Insert at top of #app or main container — find appropriate parent
  const container = document.querySelector('.oc') || document.body.firstElementChild;
  if (container) container.insertBefore(banner, container.firstChild);

  // Wait for product list to load, then auto-open the product
  const tryOpen = () => {
    if (!productId) return;
    // Look for the row's edit button by data attribute or product id
    const targetBtn = document.querySelector(`[data-product-id="${productId}"] .open-edit, [data-edit-product-id="${productId}"], a[href*="product=${productId}"]`);
    if (targetBtn) {
      targetBtn.click();
      // Best effort: switch to lang tab inside the modal
      if (lang) {
        setTimeout(() => {
          const langTab = document.querySelector(`[data-lang-tab="${lang.toLowerCase()}"], [data-lang="${lang.toLowerCase()}"]`);
          if (langTab) langTab.click();
        }, 600);
      }
      return true;
    }
    return false;
  };

  // Retry with timeout (product list loads async)
  let tries = 0;
  const interval = setInterval(() => {
    if (tryOpen() || ++tries > 20) clearInterval(interval);
  }, 300);
})();
```

This is BEST-EFFORT. Real medias_list.html may have different DOM. Open it first to find:
- product row data-attribute names
- edit button class
- modal lang tab data-attribute

If the patterns differ significantly, adapt.

commit + push + restart + manual verify

---

## Task 4: 最终回归 + 生产部署

- 全测试 on server (test_tasks_routes.py 应该 +1 = 17 → 18 passed)
- curl 验证测试环境
- merge feature/task-medias-bridge → master + push
- SSH /opt/autovideosrt git pull + restart autovideosrt
- curl 80 端口验证
- CronDelete
