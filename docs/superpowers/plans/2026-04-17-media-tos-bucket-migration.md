# Media TOS Bucket Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Switch the material-management module to the dedicated TOS bucket `auto-video-srt-product-video-manage` and add a rerunnable migration command that can copy existing media objects, then clean old remote objects and local cache in a separate phase.

**Architecture:** Keep runtime reads and writes on the existing media-specific helper layer, but extend that layer so migration code can explicitly target the old bucket or the new bucket. Add a focused inventory query in `appcore.medias`, then build a standalone migration command that scans DB references, copies objects key-for-key, records results, and performs cleanup only for verified migrated objects.

**Tech Stack:** Python, Flask app helpers, existing TOS client wrapper, MySQL-backed DAO helpers, pytest, unittest.mock

---

### Task 1: Add migration-facing tests for TOS helpers and inventory enumeration

**Files:**
- Modify: `tests/test_openapi_materials_routes.py`
- Create: `tests/test_media_tos_bucket_migration.py`
- Test: `tests/test_media_tos_bucket_migration.py`

- [ ] **Step 1: Write the failing test**

```python
def test_generate_signed_media_download_url_uses_media_bucket(monkeypatch):
    monkeypatch.setattr("config.TOS_MEDIA_BUCKET", "auto-video-srt-product-video-manage")
    captured = {}

    def _fake_signed(bucket, key, expires):
        captured["bucket"] = bucket
        captured["key"] = key
        return "https://signed.example/url"

    monkeypatch.setattr(tos_clients, "generate_signed_download_url", _fake_signed)

    url = tos_clients.generate_signed_media_download_url("1/medias/2/demo.mp4")

    assert url == "https://signed.example/url"
    assert captured == {
        "bucket": "auto-video-srt-product-video-manage",
        "key": "1/medias/2/demo.mp4",
    }
```

```python
def test_collect_media_object_references_deduplicates_keys(monkeypatch):
    monkeypatch.setattr(
        medias,
        "query",
        lambda sql, args=None: [
            {"source": "item", "object_key": "1/medias/10/a.mp4"},
            {"source": "item", "object_key": "1/medias/10/a.mp4"},
            {"source": "item_cover", "object_key": "1/medias/10/a.jpg"},
            {"source": "product_cover", "object_key": ""},
        ],
    )

    refs = medias.collect_media_object_references()

    assert refs == [
        {"object_key": "1/medias/10/a.jpg", "sources": ["item_cover"]},
        {"object_key": "1/medias/10/a.mp4", "sources": ["item"]},
    ]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_media_tos_bucket_migration.py -q`
Expected: FAIL because `collect_media_object_references` and migration helpers do not exist yet.

- [ ] **Step 3: Write minimal implementation**

```python
def collect_media_object_references() -> list[dict[str, object]]:
    rows = query("...")
    grouped: dict[str, set[str]] = {}
    for row in rows:
        key = (row.get("object_key") or "").strip()
        if not key:
            continue
        grouped.setdefault(key, set()).add(row.get("source") or "unknown")
    return [
        {"object_key": key, "sources": sorted(sources)}
        for key, sources in sorted(grouped.items())
    ]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_media_tos_bucket_migration.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tests/test_openapi_materials_routes.py tests/test_media_tos_bucket_migration.py appcore/medias.py
git commit -m "test: cover media bucket migration helpers"
```

### Task 2: Extend media TOS helpers for explicit-bucket operations

**Files:**
- Modify: `config.py`
- Modify: `appcore/tos_clients.py`
- Test: `tests/test_media_tos_bucket_migration.py`

- [ ] **Step 1: Write the failing test**

```python
def test_download_media_file_can_target_override_bucket(tmp_path, monkeypatch):
    calls = {}

    class _Client:
        def get_object_to_file(self, bucket, object_key, local_path):
            calls["bucket"] = bucket
            calls["object_key"] = object_key
            Path(local_path).write_bytes(b"ok")

    monkeypatch.setattr(tos_clients, "get_server_client", lambda: _Client())

    dest = tmp_path / "demo.bin"
    tos_clients.download_media_file("a/b.mp4", dest, bucket="old-media-bucket")

    assert calls == {"bucket": "old-media-bucket", "object_key": "a/b.mp4"}
    assert dest.read_bytes() == b"ok"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_media_tos_bucket_migration.py -q`
Expected: FAIL because media helper methods do not accept an override bucket yet.

- [ ] **Step 3: Write minimal implementation**

```python
def get_media_bucket(bucket: str | None = None) -> str:
    return (bucket or config.TOS_MEDIA_BUCKET or "").strip()


def download_media_file(object_key: str, local_path: str | Path, bucket: str | None = None) -> str:
    destination = Path(local_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    get_server_client().get_object_to_file(get_media_bucket(bucket), object_key, str(destination))
    return str(destination)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_media_tos_bucket_migration.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add config.py appcore/tos_clients.py tests/test_media_tos_bucket_migration.py
git commit -m "feat: add explicit bucket support for media TOS helpers"
```

### Task 3: Add the migration command with dry-run, apply, and cleanup modes

**Files:**
- Modify: `appcore/medias.py`
- Create: `scripts/migrate_media_tos_bucket.py`
- Test: `tests/test_media_tos_bucket_migration.py`

- [ ] **Step 1: Write the failing test**

```python
def test_apply_mode_copies_missing_object_to_new_bucket(tmp_path, monkeypatch):
    monkeypatch.setattr(
        medias,
        "collect_media_object_references",
        lambda: [{"object_key": "1/medias/7/demo.mp4", "sources": ["item"]}],
    )
    events = []

    monkeypatch.setattr(
        migrate_media_tos_bucket,
        "_copy_object",
        lambda key, temp_dir, old_bucket, new_bucket: events.append((key, old_bucket, new_bucket)) or {"status": "migrated", "object_key": key},
    )

    summary = migrate_media_tos_bucket.run_apply(
        old_bucket="video-save",
        new_bucket="auto-video-srt-product-video-manage",
        temp_dir=tmp_path,
    )

    assert summary["migrated"] == 1
    assert events == [("1/medias/7/demo.mp4", "video-save", "auto-video-srt-product-video-manage")]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_media_tos_bucket_migration.py -q`
Expected: FAIL because the migration module does not exist yet.

- [ ] **Step 3: Write minimal implementation**

```python
def run_apply(old_bucket: str, new_bucket: str, temp_dir: Path) -> dict[str, int]:
    summary = {"total": 0, "migrated": 0, "skipped": 0, "missing": 0, "failed": 0}
    for ref in medias.collect_media_object_references():
        summary["total"] += 1
        result = _copy_object(ref["object_key"], temp_dir, old_bucket, new_bucket)
        summary[result["status"]] += 1
    return summary
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_media_tos_bucket_migration.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add appcore/medias.py scripts/migrate_media_tos_bucket.py tests/test_media_tos_bucket_migration.py
git commit -m "feat: add media TOS bucket migration command"
```

### Task 4: Wire the new bucket default and cleanup behavior through routes and docs-sensitive tests

**Files:**
- Modify: `config.py`
- Modify: `web/routes/medias.py`
- Modify: `web/routes/openapi_materials.py`
- Modify: `tests/test_openapi_materials_routes.py`
- Modify: `tests/test_media_tos_bucket_migration.py`

- [ ] **Step 1: Write the failing test**

```python
def test_cleanup_local_removes_media_thumb_cache(tmp_path):
    cache_file = tmp_path / "media_thumbs" / "7" / "thumb.jpg"
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    cache_file.write_bytes(b"x")

    removed = migrate_media_tos_bucket.cleanup_local_cache(tmp_path)

    assert removed == 1
    assert not cache_file.exists()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_media_tos_bucket_migration.py tests/test_openapi_materials_routes.py -q`
Expected: FAIL because cleanup helpers and route-facing config changes are incomplete.

- [ ] **Step 3: Write minimal implementation**

```python
def cleanup_local_cache(output_dir: Path) -> int:
    base = output_dir / "media_thumbs"
    removed = 0
    for path in sorted(base.rglob("*"), reverse=True):
        if path.is_file():
            path.unlink()
            removed += 1
    return removed
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_media_tos_bucket_migration.py tests/test_openapi_materials_routes.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add config.py web/routes/medias.py web/routes/openapi_materials.py tests/test_openapi_materials_routes.py tests/test_media_tos_bucket_migration.py
git commit -m "feat: switch materials to dedicated media TOS bucket"
```

### Task 5: Final verification and operator-facing usage notes

**Files:**
- Modify: `README.md`
- Modify: `docs/素材信息获取接口API.md`
- Test: `tests/test_media_tos_bucket_migration.py`

- [ ] **Step 1: Add operator usage notes**

```markdown
### Media bucket migration

1. `python -m scripts.migrate_media_tos_bucket --dry-run`
2. `python -m scripts.migrate_media_tos_bucket --apply`
3. Validate material playback and covers
4. `python -m scripts.migrate_media_tos_bucket --cleanup-remote`
5. `python -m scripts.migrate_media_tos_bucket --cleanup-local`
```

- [ ] **Step 2: Run targeted verification**

Run: `pytest tests/test_media_tos_bucket_migration.py tests/test_openapi_materials_routes.py -q`
Expected: PASS

Run: `pytest tests/test_appcore_medias.py tests/test_appcore_medias_multi_lang.py -q`
Expected: PASS when the local MySQL test dependency is available.

- [ ] **Step 3: Commit**

```bash
git add README.md docs/素材信息获取接口API.md tests/test_media_tos_bucket_migration.py
git commit -m "docs: document media bucket migration workflow"
```
