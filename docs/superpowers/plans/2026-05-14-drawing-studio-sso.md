# Drawing Studio SSO Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a left-sidebar "画图工作室" entry in AutoVideoSrtLocal that sends the current logged-in user to Canvas Realm Studio on port 81 through a short-lived signed SSO redirect.

**Architecture:** AutoVideoSrtLocal owns only the SSO issuer side: permission registration, menu link, Flask redirect route, and HMAC URL builder. Canvas Realm owns the receiver side described in the spec and is implemented by a separate agent. The issuer never sends passwords or password hashes.

**Tech Stack:** Python 3.12, Flask, Flask-Login, Jinja2, pytest, HMAC-SHA256 via Python stdlib.

---

## File Structure

### Create

- `appcore/drawing_studio_sso.py` — pure helper for building signed Canvas Realm SSO URLs.
- `web/routes/drawing_studio.py` — Flask blueprint with `GET /drawing-studio/sso`.
- `tests/test_appcore_permissions_drawing_studio.py` — permission registry tests.
- `tests/test_drawing_studio_sso.py` — helper and route tests.

### Modify

- `appcore/permissions.py` — add `drawing_studio` menu permission, default enabled for admin and user.
- `web/app.py` — import and register drawing studio blueprint.
- `web/templates/layout.html` — add the left-sidebar "画图工作室" menu entry.
- `tests/test_tools_routes.py` — assert the menu entry is visible to a normal user.

### Docs Anchor

- `docs/superpowers/specs/2026-05-14-drawing-studio-sso-design.md`

---

## Task 1: Permission Registry

**Files:**
- Modify: `appcore/permissions.py`
- Create: `tests/test_appcore_permissions_drawing_studio.py`
- Test: `tests/test_appcore_permissions_drawing_studio.py`

- [ ] **Step 1: Write the failing permission tests**

Create `tests/test_appcore_permissions_drawing_studio.py`:

```python
from appcore.permissions import (
    PERMISSION_CODES,
    ROLE_ADMIN,
    ROLE_SUPERADMIN,
    ROLE_USER,
    default_permissions_for_role,
)


def test_drawing_studio_permission_is_registered():
    assert "drawing_studio" in PERMISSION_CODES


def test_drawing_studio_defaults_on_for_admin_and_user():
    assert default_permissions_for_role(ROLE_ADMIN)["drawing_studio"] is True
    assert default_permissions_for_role(ROLE_USER)["drawing_studio"] is True
    assert default_permissions_for_role(ROLE_SUPERADMIN)["drawing_studio"] is True
```

- [ ] **Step 2: Run the permission tests and verify RED**

Run:

```bash
pytest tests/test_appcore_permissions_drawing_studio.py -q
```

Expected: FAIL because `drawing_studio` is not in `PERMISSION_CODES`.

- [ ] **Step 3: Add the permission**

In `appcore/permissions.py`, add this tuple in the business permissions block after `image_translate`:

```python
("drawing_studio",       GROUP_BUSINESS,   "画图工作室",       True,  True),
```

- [ ] **Step 4: Run the permission tests and verify GREEN**

Run:

```bash
pytest tests/test_appcore_permissions_drawing_studio.py -q
```

Expected: PASS.

---

## Task 2: Signed SSO URL Helper

**Files:**
- Create: `appcore/drawing_studio_sso.py`
- Create/Modify: `tests/test_drawing_studio_sso.py`
- Test: `tests/test_drawing_studio_sso.py::test_build_drawing_studio_sso_url_signs_current_user_payload`

- [ ] **Step 1: Write the failing helper test**

Create `tests/test_drawing_studio_sso.py` with this first test:

```python
import hashlib
import hmac
from urllib.parse import parse_qs, urlparse


def _expected_sig(secret: str, params: dict[str, str]) -> str:
    canonical = "&".join(f"{key}={params[key]}" for key in sorted(params))
    return hmac.new(secret.encode("utf-8"), canonical.encode("utf-8"), hashlib.sha256).hexdigest()


def test_build_drawing_studio_sso_url_signs_current_user_payload(monkeypatch):
    from appcore.drawing_studio_sso import build_drawing_studio_sso_url

    monkeypatch.setenv("DRAWING_STUDIO_SSO_SECRET", "unit-test-secret")

    url = build_drawing_studio_sso_url(
        user_id=7,
        username="alice",
        role="admin",
        now=1_700_000_000,
        nonce="nonce-1",
    )

    parsed = urlparse(url)
    assert parsed.scheme == "http"
    assert parsed.netloc == "127.0.0.1:81"
    assert parsed.path == "/api/auth/autovideosrt-sso"

    query = {key: values[0] for key, values in parse_qs(parsed.query).items()}
    assert query["avs_user_id"] == "7"
    assert query["avs_username"] == "alice"
    assert query["avs_role"] == "admin"
    assert query["exp"] == "1700000120"
    assert query["nonce"] == "nonce-1"

    signed_params = {key: value for key, value in query.items() if key != "sig"}
    assert query["sig"] == _expected_sig("unit-test-secret", signed_params)
```

- [ ] **Step 2: Run the helper test and verify RED**

Run:

```bash
pytest tests/test_drawing_studio_sso.py::test_build_drawing_studio_sso_url_signs_current_user_payload -q
```

Expected: FAIL because `appcore.drawing_studio_sso` does not exist.

- [ ] **Step 3: Implement the helper**

Create `appcore/drawing_studio_sso.py`:

```python
from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import time
from urllib.parse import urlencode, urljoin


DEFAULT_DRAWING_STUDIO_BASE_URL = "http://127.0.0.1:81"
DRAWING_STUDIO_SSO_PATH = "/api/auth/autovideosrt-sso"
DRAWING_STUDIO_SSO_SECRET_ENV = "DRAWING_STUDIO_SSO_SECRET"
DRAWING_STUDIO_BASE_URL_ENV = "DRAWING_STUDIO_BASE_URL"
DEFAULT_SSO_TTL_SECONDS = 120


class DrawingStudioSsoConfigError(RuntimeError):
    pass


def _secret_from_env() -> str:
    secret = (os.getenv(DRAWING_STUDIO_SSO_SECRET_ENV) or "").strip()
    if not secret:
        raise DrawingStudioSsoConfigError("DRAWING_STUDIO_SSO_SECRET is not configured")
    return secret


def _base_url_from_env() -> str:
    return (os.getenv(DRAWING_STUDIO_BASE_URL_ENV) or DEFAULT_DRAWING_STUDIO_BASE_URL).strip().rstrip("/")


def _canonical_query(params: dict[str, str]) -> str:
    return urlencode([(key, params[key]) for key in sorted(params)])


def build_drawing_studio_sso_url(
    *,
    user_id: int | str,
    username: str,
    role: str,
    now: int | None = None,
    nonce: str | None = None,
    ttl_seconds: int = DEFAULT_SSO_TTL_SECONDS,
    base_url: str | None = None,
    secret: str | None = None,
) -> str:
    issued_at = int(time.time() if now is None else now)
    params = {
        "avs_user_id": str(user_id),
        "avs_username": str(username),
        "avs_role": str(role),
        "exp": str(issued_at + int(ttl_seconds)),
        "nonce": nonce or secrets.token_urlsafe(18),
    }
    signing_secret = secret if secret is not None else _secret_from_env()
    canonical = _canonical_query(params)
    params["sig"] = hmac.new(
        signing_secret.encode("utf-8"),
        canonical.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()

    root = (base_url or _base_url_from_env()).rstrip("/")
    return f"{urljoin(root + '/', DRAWING_STUDIO_SSO_PATH.lstrip('/'))}?{_canonical_query(params)}"
```

- [ ] **Step 4: Run the helper test and verify GREEN**

Run:

```bash
pytest tests/test_drawing_studio_sso.py::test_build_drawing_studio_sso_url_signs_current_user_payload -q
```

Expected: PASS.

---

## Task 3: Flask SSO Route

**Files:**
- Create: `web/routes/drawing_studio.py`
- Modify: `web/app.py`
- Modify: `tests/test_drawing_studio_sso.py`
- Test: `tests/test_drawing_studio_sso.py`

- [ ] **Step 1: Add failing route tests**

Append to `tests/test_drawing_studio_sso.py`:

```python
def test_drawing_studio_sso_requires_login(authed_client_no_db):
    client = authed_client_no_db.application.test_client()

    response = client.get("/drawing-studio/sso")

    assert response.status_code == 302
    assert "/login" in response.headers["Location"]


def test_drawing_studio_sso_requires_configured_secret(authed_client_no_db, monkeypatch):
    monkeypatch.delenv("DRAWING_STUDIO_SSO_SECRET", raising=False)

    response = authed_client_no_db.get("/drawing-studio/sso")

    assert response.status_code == 503
    assert "DRAWING_STUDIO_SSO_SECRET" in response.get_data(as_text=True)


def test_drawing_studio_sso_redirects_authenticated_user_to_canvas_realm(
    authed_client_no_db,
    monkeypatch,
):
    monkeypatch.setenv("DRAWING_STUDIO_SSO_SECRET", "unit-test-secret")

    response = authed_client_no_db.get("/drawing-studio/sso")

    assert response.status_code == 302
    location = response.headers["Location"]
    parsed = urlparse(location)
    assert parsed.scheme == "http"
    assert parsed.netloc == "127.0.0.1:81"
    assert parsed.path == "/api/auth/autovideosrt-sso"

    query = {key: values[0] for key, values in parse_qs(parsed.query).items()}
    assert query["avs_user_id"] == "1"
    assert query["avs_username"] == "admin"
    assert query["avs_role"] == "admin"
    signed_params = {key: value for key, value in query.items() if key != "sig"}
    assert query["sig"] == _expected_sig("unit-test-secret", signed_params)
```

- [ ] **Step 2: Run route tests and verify RED**

Run:

```bash
pytest tests/test_drawing_studio_sso.py -q
```

Expected: route tests FAIL with 404 or missing blueprint.

- [ ] **Step 3: Implement the blueprint**

Create `web/routes/drawing_studio.py`:

```python
from __future__ import annotations

from flask import Blueprint, redirect
from flask_login import current_user, login_required

from appcore.drawing_studio_sso import (
    DrawingStudioSsoConfigError,
    build_drawing_studio_sso_url,
)


bp = Blueprint("drawing_studio", __name__, url_prefix="/drawing-studio")


@bp.route("/sso")
@login_required
def sso():
    try:
        target = build_drawing_studio_sso_url(
            user_id=current_user.id,
            username=current_user.username,
            role=getattr(current_user, "role", "user"),
        )
    except DrawingStudioSsoConfigError as exc:
        return str(exc), 503
    return redirect(target)
```

- [ ] **Step 4: Register the blueprint**

In `web/app.py`, add import near other route imports:

```python
from web.routes.drawing_studio import bp as drawing_studio_bp
```

Register it in `create_app()` near other business blueprints:

```python
app.register_blueprint(drawing_studio_bp)
```

- [ ] **Step 5: Run route tests and verify GREEN**

Run:

```bash
pytest tests/test_drawing_studio_sso.py -q
```

Expected: PASS.

---

## Task 4: Sidebar Menu Entry

**Files:**
- Modify: `web/templates/layout.html`
- Modify: `tests/test_tools_routes.py`
- Test: `tests/test_tools_routes.py`

- [ ] **Step 1: Add failing menu visibility test**

Append to `tests/test_tools_routes.py`:

```python
def test_drawing_studio_menu_entry_is_visible_to_normal_users(authed_user_client_no_db):
    response = authed_user_client_no_db.get("/tools/")

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "画图工作室" in body
    assert 'href="/drawing-studio/sso"' in body
    assert '<span class="nav-icon">🎨</span> 画图工作室' in body
```

- [ ] **Step 2: Run the menu test and verify RED**

Run:

```bash
pytest tests/test_tools_routes.py::test_drawing_studio_menu_entry_is_visible_to_normal_users -q
```

Expected: FAIL because the menu entry is not rendered.

- [ ] **Step 3: Add the sidebar entry**

In `web/templates/layout.html`, add this block after the `image_translate` menu block:

```html
{% if has_permission('drawing_studio') %}
<a href="{{ url_for('drawing_studio.sso') }}" target="_blank" rel="noopener noreferrer" {% if request.path.startswith('/drawing-studio') %}class="active"{% endif %}>
  <span class="nav-icon">🎨</span> 画图工作室
</a>
{% endif %}
```

- [ ] **Step 4: Run the menu test and verify GREEN**

Run:

```bash
pytest tests/test_tools_routes.py::test_drawing_studio_menu_entry_is_visible_to_normal_users -q
```

Expected: PASS.

---

## Task 5: Focused Verification

**Files:**
- Verify only

- [ ] **Step 1: Run focused tests**

Run:

```bash
pytest tests/test_appcore_permissions_drawing_studio.py tests/test_drawing_studio_sso.py tests/test_tools_routes.py -q
```

Expected: PASS.

- [ ] **Step 2: Inspect final diff**

Run:

```bash
git status --short
git diff -- appcore/drawing_studio_sso.py appcore/permissions.py web/app.py web/routes/drawing_studio.py web/templates/layout.html tests/test_appcore_permissions_drawing_studio.py tests/test_drawing_studio_sso.py tests/test_tools_routes.py
```

Expected: only the planned files are changed, plus this plan document.

- [ ] **Step 3: Record Canvas Realm handoff status**

Report that AutoVideoSrtLocal issuer-side work is complete only after focused tests pass. Report separately that end-to-end login also requires Canvas Realm to implement `GET /api/auth/autovideosrt-sso` with the same `DRAWING_STUDIO_SSO_SECRET`.
