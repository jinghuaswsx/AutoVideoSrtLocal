# TOS Backup Storage Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a TOS-backed disaster recovery layer for selected local business assets, plus daily MySQL dumps.

**Architecture:** Add focused appcore modules for deterministic path mapping, TOS backup client operations, protected-reference collection, reconciliation, DB dump upload, and scheduler registration. Existing local-media paths keep returning local filesystem paths, while the backup layer repairs the missing local or TOS side according to `FILE_STORAGE_MODE`.

**Tech Stack:** Python, Flask appcore modules, APScheduler, existing `tos` SDK, existing PyMySQL DB helpers, pytest with monkeypatch/fake clients.

---

### Task 1: Config And Path Mapping

**Files:**
- Modify: `config.py`
- Create: `appcore/tos_backup_storage.py`
- Test: `tests/test_tos_backup_storage.py`

- [ ] **Step 1: Write failing tests**

Create tests that assert:

```python
def test_backup_object_key_maps_posix_path(monkeypatch):
    monkeypatch.setenv("TOS_BACKUP_PREFIX", "FILES")
    monkeypatch.setenv("TOS_BACKUP_ENV", "test")
    from appcore import tos_backup_storage
    assert tos_backup_storage.backup_object_key_for_local_path(
        "/opt/autovideosrt-test/output/media_store/1/a.jpg"
    ) == "FILES/test/opt/autovideosrt-test/output/media_store/1/a.jpg"

def test_backup_object_key_maps_windows_path(monkeypatch):
    monkeypatch.setenv("TOS_BACKUP_PREFIX", "FILES")
    monkeypatch.setenv("TOS_BACKUP_ENV", "test")
    from appcore import tos_backup_storage
    assert tos_backup_storage.backup_object_key_for_local_path(
        r"G:\Code\AutoVideoSrtLocal\output\media_store\1\a.jpg"
    ) == "FILES/test/G/Code/AutoVideoSrtLocal/output/media_store/1/a.jpg"

def test_no_proxy_contains_tos_domains(monkeypatch):
    monkeypatch.setenv("NO_PROXY", "localhost")
    from appcore import tos_backup_storage
    tos_backup_storage.ensure_tos_direct_no_proxy()
    assert "localhost" in os.environ["NO_PROXY"]
    assert ".volces.com" in os.environ["NO_PROXY"]
    assert ".ivolces.com" in os.environ["NO_PROXY"]
```

- [ ] **Step 2: Run tests and verify RED**

Run: `python -m pytest tests/test_tos_backup_storage.py -q`
Expected: fail because module/functions do not exist.

- [ ] **Step 3: Implement minimal config and mapping**

Add config values for `FILE_STORAGE_MODE`, `TOS_BACKUP_*`, and implement `backup_object_key_for_local_path()` plus `ensure_tos_direct_no_proxy()`.

- [ ] **Step 4: Run tests and verify GREEN**

Run: `python -m pytest tests/test_tos_backup_storage.py -q`
Expected: pass.

### Task 2: Reconciliation Engine

**Files:**
- Modify: `appcore/tos_backup_storage.py`
- Test: `tests/test_tos_backup_storage.py`

- [ ] **Step 1: Write failing tests**

Add fake-client tests for:

```python
def test_reconcile_uploads_when_local_exists_and_tos_missing(tmp_path, monkeypatch):
    path = tmp_path / "a.jpg"
    path.write_bytes(b"img")
    fake = FakeBackupClient(existing=set())
    monkeypatch.setattr(tos_backup_storage, "get_backup_client", lambda: fake)
    result = tos_backup_storage.reconcile_local_file(path)
    assert result.action == "uploaded"
    assert result.object_key in fake.uploaded

def test_reconcile_downloads_when_tos_exists_and_local_missing(tmp_path, monkeypatch):
    path = tmp_path / "a.jpg"
    fake = FakeBackupClient(existing={tos_backup_storage.backup_object_key_for_local_path(path)})
    monkeypatch.setattr(tos_backup_storage, "get_backup_client", lambda: fake)
    result = tos_backup_storage.reconcile_local_file(path)
    assert result.action == "downloaded"
    assert path.read_bytes() == fake.payload

def test_reconcile_fails_when_both_missing(tmp_path, monkeypatch):
    path = tmp_path / "missing.jpg"
    fake = FakeBackupClient(existing=set())
    monkeypatch.setattr(tos_backup_storage, "get_backup_client", lambda: fake)
    result = tos_backup_storage.reconcile_local_file(path)
    assert result.action == "failed"
```

- [ ] **Step 2: Run tests and verify RED**

Run: `python -m pytest tests/test_tos_backup_storage.py -q`
Expected: fail because reconcile does not exist.

- [ ] **Step 3: Implement reconcile helpers**

Implement `SyncResult`, `object_exists()`, `upload_local_file()`, `download_to_file()`, `reconcile_local_file()`, and DB dump retention primitives.

- [ ] **Step 4: Run tests and verify GREEN**

Run: `python -m pytest tests/test_tos_backup_storage.py -q`
Expected: pass.

### Task 3: Local Media Storage Integration

**Files:**
- Modify: `appcore/local_media_storage.py`
- Test: `tests/test_local_media_storage_tos_backup.py`

- [ ] **Step 1: Write failing tests**

Test that `write_bytes()` triggers remote upload when backup is enabled, and `download_to()` restores from TOS when the local file is missing.

- [ ] **Step 2: Run tests and verify RED**

Run: `python -m pytest tests/test_local_media_storage_tos_backup.py -q`
Expected: fail because integration is absent.

- [ ] **Step 3: Implement local media hooks**

After atomic local writes, call `tos_backup_storage.ensure_remote_copy_for_local_path()`. Before `download_to()` copies, call `tos_backup_storage.ensure_local_copy_for_local_path()` when the source is missing.

- [ ] **Step 4: Run tests and verify GREEN**

Run: `python -m pytest tests/test_local_media_storage_tos_backup.py -q`
Expected: pass.

### Task 4: Protected Reference Collection

**Files:**
- Create: `appcore/tos_backup_references.py`
- Test: `tests/test_tos_backup_references.py`

- [ ] **Step 1: Write failing tests**

Monkeypatch `appcore.tos_backup_references.query()` and assert the collector returns references for project video paths, media item object keys, media item covers, product covers, product detail images, raw source videos, raw source covers, and raw source translation covers.

- [ ] **Step 2: Run tests and verify RED**

Run: `python -m pytest tests/test_tos_backup_references.py -q`
Expected: fail because module does not exist.

- [ ] **Step 3: Implement collector**

Implement `ProtectedFileRef`, `collect_active_references()`, and `collect_references_for_window(start, end)`.

- [ ] **Step 4: Run tests and verify GREEN**

Run: `python -m pytest tests/test_tos_backup_references.py -q`
Expected: pass.

### Task 5: Daily Backup Job And DB Dump

**Files:**
- Create: `appcore/tos_backup_job.py`
- Modify: `appcore/scheduler.py`
- Test: `tests/test_tos_backup_job.py`

- [ ] **Step 1: Write failing tests**

Test previous-day Beijing-time window calculation, summary counts from reconcile results, DB dump object key shape, 7-day retention deletion, and scheduler cron registration at 01:00.

- [ ] **Step 2: Run tests and verify RED**

Run: `python -m pytest tests/test_tos_backup_job.py -q`
Expected: fail because job module does not exist.

- [ ] **Step 3: Implement job**

Implement `run_daily_backup()`, `dump_mysql_to_gzip()`, `upload_db_dump()`, `cleanup_old_db_dumps()`, `run_network_check()`, and `register(scheduler)`.

- [ ] **Step 4: Run tests and verify GREEN**

Run: `python -m pytest tests/test_tos_backup_job.py -q`
Expected: pass.

### Task 6: Operations Script And Docs

**Files:**
- Create: `scripts/tos_backup_sync.py`
- Modify: `.env.example`
- Modify: `docs/server-environments.md`
- Test: `tests/test_tos_backup_script.py`

- [ ] **Step 1: Write failing tests**

Test CLI modes dispatch to full reconcile, incremental reconcile, DB dump, check, and network-check.

- [ ] **Step 2: Run tests and verify RED**

Run: `python -m pytest tests/test_tos_backup_script.py -q`
Expected: fail because script does not exist.

- [ ] **Step 3: Implement CLI and docs**

Add argparse CLI, `.env.example` settings, and server TUN DIRECT rule documentation.

- [ ] **Step 4: Run tests and verify GREEN**

Run: `python -m pytest tests/test_tos_backup_script.py -q`
Expected: pass.

### Task 7: Verification

**Files:**
- All touched files

- [ ] **Step 1: Run focused tests**

Run:

```powershell
python -m pytest tests/test_tos_backup_storage.py tests/test_local_media_storage_tos_backup.py tests/test_tos_backup_references.py tests/test_tos_backup_job.py tests/test_tos_backup_script.py -q
```

Expected: pass.

- [ ] **Step 2: Run related existing tests**

Run:

```powershell
python -m pytest tests/test_tos_clients.py tests/test_local_storage_migration.py tests/test_media_tos_bucket_migration.py tests/test_medias_link_check_routes.py -q
```

Expected: pass, or report pre-existing failures separately.

- [ ] **Step 3: Commit implementation**

Commit with:

```bash
git add config.py appcore scripts tests docs .env.example
git commit -m "feat: add tos backup storage"
```
