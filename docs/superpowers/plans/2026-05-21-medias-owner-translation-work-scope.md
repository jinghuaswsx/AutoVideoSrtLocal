# Medias Owner Translation Work Scope Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将素材管理负责人候选与负责人变更校验统一收敛到翻译工作用户范围，同时保持既有级联更新行为不变。

**Architecture:** 保留 `/medias/api/users/active` 与 owner PATCH 路径不变，只替换底层 service/data 校验依赖。读路径改为复用 `appcore.users.list_translation_work_users()`；写路径改为复用 `appcore.users.ensure_translation_work_user()`，前端仅调整文案语义与既有调用点测试。

**Tech Stack:** Python 3.12, Flask, pytest, vanilla JS

---

### Task 1: 先写失败测试锁定新语义

**Files:**
- Modify: `tests/test_media_pages_service.py`
- Modify: `tests/test_appcore_medias.py`
- Modify: `tests/test_medias_routes.py`
- Test: `pytest tests/test_media_pages_service.py tests/test_appcore_medias.py tests/test_medias_routes.py -q`

- [ ] **Step 1: 写 service 层失败测试**

```python
def test_build_active_users_response_wraps_translation_work_user_rows():
    from web.services.media_pages import build_active_users_response

    users = [{"id": 1, "display_name": "张三"}]

    assert build_active_users_response(list_translation_work_users_fn=lambda: users) == {
        "users": users,
    }
```

- [ ] **Step 2: 写 owner 校验失败测试**

```python
def test_update_product_owner_rejects_active_user_outside_translation_scope(ephemeral_users, monkeypatch):
    uid_a, uid_b = ephemeral_users
    pid = medias.create_product(uid_a, "翻译范围拒绝")
    monkeypatch.setattr("appcore.medias.ensure_translation_work_user", lambda _uid: (_ for _ in ()).throw(ValueError("该用户不在翻译工作范围")))
    with pytest.raises(ValueError, match="翻译工作范围"):
        medias.update_product_owner(pid, uid_b)
```
```

- [ ] **Step 3: 写路由层失败测试**

```python
def test_update_product_owner_maps_translation_scope_error_to_400(authed_client_no_db, monkeypatch):
    from web.routes import medias as r
    monkeypatch.setattr(r.medias, "get_product", lambda pid: {"id": pid, "user_id": 10, "deleted_at": None})
    monkeypatch.setattr(r.medias, "update_product_owner", lambda *_: (_ for _ in ()).throw(ValueError("该用户不在翻译工作范围")))
    resp = authed_client_no_db.patch("/medias/api/products/42/owner", json={"user_id": 7})
    assert resp.status_code == 400
```
```

- [ ] **Step 4: 运行测试确认失败**

Run: `pytest tests/test_media_pages_service.py tests/test_appcore_medias.py tests/test_medias_routes.py -q`
Expected: 至少有 1 个失败，原因是 service 参数名或实现仍指向 active users 旧语义。

### Task 2: 做最小实现切换读写依赖

**Files:**
- Modify: `appcore/medias.py`
- Modify: `web/services/media_pages.py`
- Modify: `web/static/medias.js`
- Modify: `web/static/pushes.js`
- Test: `pytest tests/test_media_pages_service.py tests/test_appcore_medias.py tests/test_medias_routes.py tests/test_medias_pages_routes.py tests/test_pushes_ui_assets.py -q`

- [ ] **Step 1: 读路径改为 translation-work users**

```python
from appcore import medias, product_roas, shopify_image_localizer_release, users as appusers

def build_active_users_response(*, list_translation_work_users_fn=None) -> dict[str, Any]:
    list_fn = list_translation_work_users_fn or appusers.list_translation_work_users
    return {"users": list_fn()}
```

- [ ] **Step 2: 写路径改为 ensure_translation_work_user**

```python
from appcore.users import ensure_translation_work_user

def update_product_owner(product_id: int, new_user_id: int) -> None:
    pid = int(product_id)
    uid = int(new_user_id)
    ensure_translation_work_user(uid)
    ...
```

- [ ] **Step 3: 收敛前端文案语义**

```javascript
select.setAttribute('aria-label', '指派素材负责人');
opt.textContent = originalName ? (originalName + '（当前负责人不在翻译工作范围）') : '(当前负责人)';
console.warn('load owner candidates failed', e);
```

- [ ] **Step 4: 运行测试确认通过**

Run: `pytest tests/test_media_pages_service.py tests/test_appcore_medias.py tests/test_medias_routes.py tests/test_medias_pages_routes.py tests/test_pushes_ui_assets.py -q`
Expected: PASS

### Task 3: 做收尾验证

**Files:**
- Modify: `docs/superpowers/specs/2026-05-21-medias-owner-translation-work-scope-design.md`
- Test: `git diff --check`

- [ ] **Step 1: 自查 spec 与实现一致**

检查 `docs/superpowers/specs/2026-05-21-medias-owner-translation-work-scope-design.md` 中是否明确写出：
- 覆盖旧 active users 规则
- 保留 route path
- 保留 tasks cascade

- [ ] **Step 2: 跑 diff 格式检查**

Run: `git diff --check`
Expected: 无输出
