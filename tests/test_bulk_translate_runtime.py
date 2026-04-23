"""bulk_translate runtime 编排测试。

聚焦新版“父任务派发 + 轮询子任务 + 手工恢复”状态机。
"""
from __future__ import annotations

import json
import uuid

import pytest


class _FakeProjectsDB:
    def __init__(self):
        self.rows = {}

    def execute(self, sql, args=None):
        s = " ".join(str(sql).upper().split())
        if "INSERT INTO PROJECTS" in s:
            task_id, user_id, state_json = args
            self.rows[task_id] = {
                "id": task_id,
                "user_id": user_id,
                "type": "bulk_translate",
                "status": "planning",
                "state_json": state_json,
                "created_at": None,
            }
            return 1
        if "UPDATE PROJECTS SET STATE_JSON = %S, STATUS = %S WHERE ID = %S" in s:
            payload, status, task_id = args
            self.rows[task_id]["state_json"] = payload
            self.rows[task_id]["status"] = status
            return 1
        if "UPDATE PROJECTS SET STATUS = %S, STATE_JSON = %S WHERE ID = %S" in s:
            status, payload, task_id = args
            self.rows[task_id]["state_json"] = payload
            self.rows[task_id]["status"] = status
            return 1
        if "UPDATE PROJECTS SET STATE_JSON = %S WHERE ID = %S" in s:
            payload, task_id = args
            self.rows[task_id]["state_json"] = payload
            return 1
        raise AssertionError(f"unexpected execute: {sql}")

    def query_one(self, sql, args=None):
        s = " ".join(str(sql).upper().split())
        if "FROM PROJECTS" in s:
            assert "UPDATED_AT" not in s
            return self.rows.get(args[0])
        raise AssertionError(f"unexpected query_one: {sql}")


@pytest.fixture
def runtime_env(monkeypatch):
    fake_db = _FakeProjectsDB()
    from appcore import bulk_translate_runtime as mod

    monkeypatch.setattr(mod, "execute", fake_db.execute)
    monkeypatch.setattr(mod, "query_one", fake_db.query_one)
    monkeypatch.setattr(mod, "query", lambda *args, **kwargs: [])
    monkeypatch.setattr(
        mod,
        "do_estimate",
        lambda *args, **kwargs: {
            "copy_tokens": 100,
            "image_count": 2,
            "video_minutes": 3.5,
            "estimated_cost_cny": 4.2,
        },
    )
    return mod, fake_db


def _item(
    idx: int,
    *,
    kind: str = "videos",
    lang: str = "de",
    ref: dict | None = None,
    status: str = "pending",
    dispatch_after_seconds: int = 0,
):
    return {
        "idx": idx,
        "kind": kind,
        "lang": lang,
        "ref": ref or {"source_raw_id": 300 + idx},
        "child_task_id": None,
        "child_task_type": None,
        "status": status,
        "dispatch_after_seconds": dispatch_after_seconds,
        "result_synced": False,
        "error": None,
        "started_at": None,
        "finished_at": None,
    }


def _load_state(fake_db: _FakeProjectsDB, task_id: str) -> dict:
    return json.loads(fake_db.rows[task_id]["state_json"])


def _store_state(fake_db: _FakeProjectsDB, task_id: str, state: dict) -> None:
    fake_db.rows[task_id]["state_json"] = json.dumps(state, ensure_ascii=False)


def test_create_stores_plan_and_raw_source_ids(runtime_env, monkeypatch):
    mod, fake_db = runtime_env
    monkeypatch.setattr(
        mod,
        "generate_plan",
        lambda *args, **kwargs: [
            _item(0, kind="copywriting", ref={"source_copy_id": 11}),
            _item(1, kind="videos", ref={"source_raw_id": 301}, dispatch_after_seconds=120),
        ],
    )

    task_id = mod.create_bulk_translate_task(
        user_id=1,
        product_id=77,
        target_langs=["de", "fr"],
        content_types=["copywriting", "videos"],
        force_retranslate=False,
        video_params={"subtitle_size": 18},
        initiator={"user_id": 1, "user_name": "tester", "ip": "", "user_agent": ""},
        raw_source_ids=[301],
    )

    row = fake_db.rows[task_id]
    assert row["status"] == "planning"
    state = json.loads(row["state_json"])
    assert state["content_types"] == ["copywriting", "videos"]
    assert state["raw_source_ids"] == [301]
    assert state["video_params_snapshot"] == {"subtitle_size": 18}
    assert state["progress"]["pending"] == 2
    assert state["progress"]["dispatching"] == 0
    assert state["progress"]["awaiting_voice"] == 0
    assert state["progress"]["interrupted"] == 0


def test_compute_progress_counts_new_statuses(runtime_env):
    mod, _fake_db = runtime_env

    progress = mod.compute_progress(
        [
            {"status": "pending"},
            {"status": "dispatching"},
            {"status": "running"},
            {"status": "syncing_result"},
            {"status": "awaiting_voice"},
            {"status": "failed"},
            {"status": "interrupted"},
            {"status": "done"},
            {"status": "skipped"},
        ]
    )

    assert progress == {
        "total": 9,
        "pending": 1,
        "dispatching": 1,
        "running": 1,
        "syncing_result": 1,
        "awaiting_voice": 1,
        "failed": 1,
        "interrupted": 1,
        "done": 1,
        "skipped": 1,
    }


def test_run_scheduler_enters_waiting_manual_for_voice_selection(runtime_env, monkeypatch):
    mod, fake_db = runtime_env
    monkeypatch.setattr(mod, "generate_plan", lambda *args, **kwargs: [_item(0, kind="videos")])

    task_id = mod.create_bulk_translate_task(
        user_id=1,
        product_id=77,
        target_langs=["de"],
        content_types=["videos"],
        force_retranslate=False,
        video_params={},
        initiator={"user_id": 1, "user_name": "", "ip": "", "user_agent": ""},
        raw_source_ids=[301],
    )
    mod.start_task(task_id, user_id=1)

    monkeypatch.setattr(
        mod,
        "_create_child_task",
        lambda parent_id, item, parent_state: ("multi-1", "multi_translate", "running"),
    )
    monkeypatch.setattr(
        mod,
        "_load_child_snapshot",
        lambda task_type, child_task_id: {
            "_project_status": "running",
            "current_review_step": "voice_match",
            "steps": {"voice_match": "waiting"},
        },
    )
    monkeypatch.setattr(mod, "_sync_child_result", lambda *args, **kwargs: None)

    mod.run_scheduler(
        task_id,
        now_provider=lambda: 0,
        sleep_fn=lambda *_args, **_kwargs: None,
        max_loops=3,
    )

    assert fake_db.rows[task_id]["status"] == "waiting_manual"
    state = _load_state(fake_db, task_id)
    assert state["plan"][0]["child_task_id"] == "multi-1"
    assert state["plan"][0]["child_task_type"] == "multi_translate"
    assert state["plan"][0]["status"] == "awaiting_voice"


def test_run_scheduler_dispatches_due_items_without_waiting_for_active_children(runtime_env, monkeypatch):
    mod, fake_db = runtime_env
    monkeypatch.setattr(
        mod,
        "generate_plan",
        lambda *args, **kwargs: [
            _item(0, kind="copywriting", ref={"source_copy_id": 11}, dispatch_after_seconds=0),
            _item(1, kind="video_covers", ref={"source_raw_ids": [301]}, dispatch_after_seconds=0),
            _item(2, kind="videos", ref={"source_raw_id": 301}, dispatch_after_seconds=5),
            _item(3, kind="detail_images", ref={"source_detail_ids": [1, 2]}, dispatch_after_seconds=10),
        ],
    )

    task_id = mod.create_bulk_translate_task(
        user_id=1,
        product_id=77,
        target_langs=["de"],
        content_types=["copywriting", "video_covers", "videos", "detail_images"],
        force_retranslate=False,
        video_params={},
        initiator={"user_id": 1, "user_name": "", "ip": "", "user_agent": ""},
        raw_source_ids=[301],
    )
    mod.start_task(task_id, user_id=1)
    state = _load_state(fake_db, task_id)
    state["scheduler_anchor_ts"] = 0
    _store_state(fake_db, task_id, state)

    created = []

    def fake_create_child(parent_id, item, parent_state):
        child_id = f"child-{item['idx']}"
        created.append((item["idx"], item["kind"], item["dispatch_after_seconds"]))
        return child_id, "fake_child", "running"

    monkeypatch.setattr(mod, "_create_child_task", fake_create_child)
    monkeypatch.setattr(mod, "_load_child_snapshot", lambda task_type, child_task_id: {"_project_status": "running"})
    monkeypatch.setattr(mod, "_sync_child_result", lambda *args, **kwargs: None)

    mod.run_scheduler(
        task_id,
        now_provider=lambda: 0,
        sleep_fn=lambda *_args, **_kwargs: None,
        max_loops=4,
    )

    assert created == [
        (0, "copywriting", 0),
        (1, "video_covers", 0),
    ]
    state = _load_state(fake_db, task_id)
    assert [item["status"] for item in state["plan"]] == ["running", "running", "pending", "pending"]


def test_run_scheduler_dispatches_spaced_items_while_previous_children_still_run(runtime_env, monkeypatch):
    mod, fake_db = runtime_env
    monkeypatch.setattr(
        mod,
        "generate_plan",
        lambda *args, **kwargs: [
            _item(0, kind="videos", ref={"source_raw_id": 301}, dispatch_after_seconds=0),
            _item(1, kind="videos", ref={"source_raw_id": 302}, dispatch_after_seconds=5),
            _item(2, kind="detail_images", ref={"source_detail_ids": [1]}, dispatch_after_seconds=10),
        ],
    )

    task_id = mod.create_bulk_translate_task(
        user_id=1,
        product_id=77,
        target_langs=["de"],
        content_types=["videos", "detail_images"],
        force_retranslate=False,
        video_params={},
        initiator={"user_id": 1, "user_name": "", "ip": "", "user_agent": ""},
        raw_source_ids=[301, 302],
    )
    mod.start_task(task_id, user_id=1)
    state = _load_state(fake_db, task_id)
    state["scheduler_anchor_ts"] = 0
    _store_state(fake_db, task_id, state)

    created = []
    monkeypatch.setattr(
        mod,
        "_create_child_task",
        lambda parent_id, item, parent_state: (
            created.append(item["idx"]) or f"child-{item['idx']}",
            "fake_child",
            "running",
        ),
    )
    monkeypatch.setattr(mod, "_load_child_snapshot", lambda task_type, child_task_id: {"_project_status": "running"})
    monkeypatch.setattr(mod, "_sync_child_result", lambda *args, **kwargs: None)

    mod.run_scheduler(
        task_id,
        now_provider=lambda: 10,
        sleep_fn=lambda *_args, **_kwargs: None,
        max_loops=6,
    )

    assert created == [0, 1, 2]
    state = _load_state(fake_db, task_id)
    assert [item["status"] for item in state["plan"]] == ["running", "running", "running"]


def test_run_scheduler_waiting_voice_does_not_block_other_due_items(runtime_env, monkeypatch):
    mod, fake_db = runtime_env
    monkeypatch.setattr(
        mod,
        "generate_plan",
        lambda *args, **kwargs: [
            _item(0, kind="videos", ref={"source_raw_id": 301}, dispatch_after_seconds=0),
            _item(1, kind="video_covers", ref={"source_raw_ids": [301]}, dispatch_after_seconds=0),
        ],
    )

    task_id = mod.create_bulk_translate_task(
        user_id=1,
        product_id=77,
        target_langs=["de"],
        content_types=["videos", "video_covers"],
        force_retranslate=False,
        video_params={},
        initiator={"user_id": 1, "user_name": "", "ip": "", "user_agent": ""},
        raw_source_ids=[301],
    )
    mod.start_task(task_id, user_id=1)
    state = _load_state(fake_db, task_id)
    state["scheduler_anchor_ts"] = 0
    _store_state(fake_db, task_id, state)

    created = []

    def fake_create_child(parent_id, item, parent_state):
        created.append(item["kind"])
        if item["kind"] == "videos":
            return "multi-1", "multi_translate", "running"
        return "cover-1", "image_translate", "running"

    def fake_child_snapshot(task_type, child_task_id):
        if child_task_id == "multi-1":
            return {
                "_project_status": "uploaded",
                "current_review_step": "voice_match",
                "steps": {"voice_match": "waiting"},
            }
        return {"_project_status": "running"}

    monkeypatch.setattr(mod, "_create_child_task", fake_create_child)
    monkeypatch.setattr(mod, "_load_child_snapshot", fake_child_snapshot)
    monkeypatch.setattr(mod, "_sync_child_result", lambda *args, **kwargs: None)

    mod.run_scheduler(
        task_id,
        now_provider=lambda: 0,
        sleep_fn=lambda *_args, **_kwargs: None,
        max_loops=5,
    )

    assert created == ["videos", "video_covers"]
    assert fake_db.rows[task_id]["status"] == "running"
    state = _load_state(fake_db, task_id)
    assert [item["status"] for item in state["plan"]] == ["awaiting_voice", "running"]


def test_create_child_task_reuses_existing_deterministic_child(runtime_env, monkeypatch):
    mod, fake_db = runtime_env
    item = _item(0, kind="videos", ref={"source_raw_id": 301})
    expected_child_id = uuid.uuid5(uuid.NAMESPACE_URL, "bulk_translate:parent-1:0").hex
    fake_db.rows[expected_child_id] = {
        "id": expected_child_id,
        "user_id": 1,
        "type": "multi_translate",
        "status": "running",
        "state_json": "{}",
        "created_at": None,
    }
    monkeypatch.setattr(
        mod,
        "_create_video_child",
        lambda *args, **kwargs: pytest.fail("existing deterministic child should be reused"),
    )

    child_id, child_type, child_status = mod._create_child_task(
        "parent-1",
        item,
        {"product_id": 77, "initiator": {"user_id": 1}},
    )

    assert child_id == expected_child_id
    assert child_type == "multi_translate"
    assert child_status == "running"
    assert item["child_task_id"] == expected_child_id
    assert item["sub_task_id"] == expected_child_id


def test_create_video_child_materializes_media_raw_source_locally(runtime_env, monkeypatch, tmp_path):
    mod, _fake_db = runtime_env
    raw_key = "1/medias/77/raw_sources/raw-demo.mp4"
    created = {}
    updated = {}
    started = []

    monkeypatch.setattr(mod, "OUTPUT_DIR", str(tmp_path / "output"))
    monkeypatch.setattr(mod, "UPLOAD_DIR", str(tmp_path / "uploads"))
    monkeypatch.setattr(
        mod.medias,
        "get_raw_source",
        lambda rid: {
            "id": rid,
            "product_id": 77,
            "user_id": 1,
            "display_name": "raw-demo",
            "video_object_key": raw_key,
            "file_size": 1234,
        },
    )
    monkeypatch.setattr(mod, "execute", lambda *args, **kwargs: 1)

    import web.store as store
    from web.services import multi_pipeline_runner

    def fake_create(task_id, video_path, task_dir, original_filename, user_id):
        created.update({
            "task_id": task_id,
            "video_path": video_path,
            "task_dir": task_dir,
            "original_filename": original_filename,
            "user_id": user_id,
        })

    def fake_update(task_id, **fields):
        updated[task_id] = fields

    def fake_download_to(object_key, destination):
        created["download"] = (object_key, destination)
        with open(destination, "wb") as fh:
            fh.write(b"video")
        return destination

    monkeypatch.setattr(store, "create", fake_create)
    monkeypatch.setattr(store, "update", fake_update)
    monkeypatch.setattr(mod.local_media_storage, "download_to", fake_download_to)
    monkeypatch.setattr(multi_pipeline_runner, "start", lambda task_id, user_id: started.append((task_id, user_id)))

    child_task_id, child_type, status = mod._create_video_child(
        "parent-1",
        _item(0, kind="videos", lang="pt", ref={"source_raw_id": 301}),
        {
            "product_id": 77,
            "initiator": {"user_id": 1},
            "video_params_snapshot": {"subtitle_size": 18, "subtitle_position_y": 0.55},
        },
    )

    assert child_type == "multi_translate"
    assert status == "running"
    assert created["download"] == (raw_key, created["video_path"])
    assert started == [(child_task_id, 1)]
    assert updated[child_task_id]["source_tos_key"] == ""
    assert updated[child_task_id]["delivery_mode"] == "local_primary"
    assert updated[child_task_id]["source_object_info"]["storage_backend"] == "media_store"
    assert updated[child_task_id]["medias_context"]["source_media_object_key"] == raw_key
    assert updated[child_task_id]["subtitle_size"] == 18
    assert updated[child_task_id]["subtitle_position_y"] == 0.55


def test_run_scheduler_syncs_completed_child_and_finishes_parent(runtime_env, monkeypatch):
    mod, fake_db = runtime_env
    monkeypatch.setattr(
        mod,
        "generate_plan",
        lambda *args, **kwargs: [_item(0, kind="detail_images", ref={"source_detail_ids": [1, 2]})],
    )

    task_id = mod.create_bulk_translate_task(
        user_id=1,
        product_id=77,
        target_langs=["de"],
        content_types=["detail_images"],
        force_retranslate=False,
        video_params={},
        initiator={"user_id": 1, "user_name": "", "ip": "", "user_agent": ""},
    )
    mod.start_task(task_id, user_id=1)

    monkeypatch.setattr(
        mod,
        "_create_child_task",
        lambda parent_id, item, parent_state: ("img-1", "image_translate", "running"),
    )
    monkeypatch.setattr(
        mod,
        "_load_child_snapshot",
        lambda task_type, child_task_id: {"_project_status": "done"},
    )

    def fake_sync(parent_id, item, parent_state, child_state):
        item["result_synced"] = True
        item["status"] = "done"

    monkeypatch.setattr(mod, "_sync_child_result", fake_sync)

    mod.run_scheduler(
        task_id,
        now_provider=lambda: 0,
        sleep_fn=lambda *_args, **_kwargs: None,
        max_loops=3,
    )

    assert fake_db.rows[task_id]["status"] == "done"
    state = _load_state(fake_db, task_id)
    assert state["plan"][0]["status"] == "done"
    assert state["plan"][0]["result_synced"] is True


def test_run_scheduler_failed_child_does_not_block_due_dispatch(runtime_env, monkeypatch):
    mod, fake_db = runtime_env
    monkeypatch.setattr(
        mod,
        "generate_plan",
        lambda *args, **kwargs: [
            _item(0, kind="copywriting", ref={"source_copy_id": 11}),
            _item(1, kind="videos", dispatch_after_seconds=0),
        ],
    )

    task_id = mod.create_bulk_translate_task(
        user_id=1,
        product_id=77,
        target_langs=["de"],
        content_types=["copywriting", "videos"],
        force_retranslate=False,
        video_params={},
        initiator={"user_id": 1, "user_name": "", "ip": "", "user_agent": ""},
        raw_source_ids=[301],
    )
    mod.start_task(task_id, user_id=1)

    created = []

    def fake_create_child(parent_id, item, parent_state):
        created.append(item["kind"])
        if item["kind"] == "copywriting":
            return "copy-1", "copywriting_translate", "running"
        return "multi-1", "multi_translate", "running"

    def fake_child_snapshot(task_type, child_task_id):
        if child_task_id == "copy-1":
            return {"_project_status": "error", "last_error": "boom"}
        return {"_project_status": "running"}

    monkeypatch.setattr(mod, "_create_child_task", fake_create_child)
    monkeypatch.setattr(mod, "_load_child_snapshot", fake_child_snapshot)
    monkeypatch.setattr(mod, "_sync_child_result", lambda *args, **kwargs: None)

    mod.run_scheduler(
        task_id,
        now_provider=lambda: 0,
        sleep_fn=lambda *_args, **_kwargs: None,
        max_loops=3,
    )

    assert created == ["copywriting", "videos"]
    assert fake_db.rows[task_id]["status"] == "running"
    state = _load_state(fake_db, task_id)
    assert state["plan"][0]["status"] == "failed"
    assert state["plan"][0]["error"] == "boom"
    assert state["plan"][1]["status"] == "running"


def test_run_scheduler_fails_parent_when_completed_image_translate_has_failed_items(
    runtime_env,
    monkeypatch,
):
    mod, fake_db = runtime_env
    monkeypatch.setattr(
        mod,
        "generate_plan",
        lambda *args, **kwargs: [_item(0, kind="video_covers", ref={"source_raw_ids": [301]})],
    )

    task_id = mod.create_bulk_translate_task(
        user_id=1,
        product_id=77,
        target_langs=["de"],
        content_types=["video_covers"],
        force_retranslate=False,
        video_params={},
        initiator={"user_id": 1, "user_name": "", "ip": "", "user_agent": ""},
        raw_source_ids=[301],
    )
    mod.start_task(task_id, user_id=1)

    monkeypatch.setattr(
        mod,
        "_create_child_task",
        lambda parent_id, item, parent_state: ("img-1", "image_translate", "running"),
    )
    monkeypatch.setattr(
        mod,
        "_load_child_snapshot",
        lambda task_type, child_task_id: {
            "_project_status": "done",
            "items": [{"status": "failed", "error": "The specified key does not exist."}],
        },
    )
    monkeypatch.setattr(
        mod,
        "_sync_child_result",
        lambda *args, **kwargs: pytest.fail("should not sync failed image_translate child"),
    )

    mod.run_scheduler(
        task_id,
        now_provider=lambda: 0,
        sleep_fn=lambda *_args, **_kwargs: None,
        max_loops=3,
    )

    assert fake_db.rows[task_id]["status"] == "failed"
    state = _load_state(fake_db, task_id)
    assert state["plan"][0]["status"] == "failed"
    assert "The specified key does not exist." in state["plan"][0]["error"]


def test_run_scheduler_fails_parent_when_result_sync_raises(runtime_env, monkeypatch):
    mod, fake_db = runtime_env
    monkeypatch.setattr(
        mod,
        "generate_plan",
        lambda *args, **kwargs: [_item(0, kind="video_covers", ref={"source_raw_ids": [301]})],
    )

    task_id = mod.create_bulk_translate_task(
        user_id=1,
        product_id=77,
        target_langs=["de"],
        content_types=["video_covers"],
        force_retranslate=False,
        video_params={},
        initiator={"user_id": 1, "user_name": "", "ip": "", "user_agent": ""},
        raw_source_ids=[301],
    )
    mod.start_task(task_id, user_id=1)

    monkeypatch.setattr(
        mod,
        "_create_child_task",
        lambda parent_id, item, parent_state: ("img-1", "image_translate", "running"),
    )
    monkeypatch.setattr(
        mod,
        "_load_child_snapshot",
        lambda task_type, child_task_id: {
            "_project_status": "done",
            "items": [{"status": "done", "dst_tos_key": "artifacts/x.png"}],
        },
    )
    monkeypatch.setattr(
        mod,
        "_sync_child_result",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("sync exploded")),
    )

    mod.run_scheduler(
        task_id,
        now_provider=lambda: 0,
        sleep_fn=lambda *_args, **_kwargs: None,
        max_loops=3,
    )

    assert fake_db.rows[task_id]["status"] == "failed"
    state = _load_state(fake_db, task_id)
    assert state["plan"][0]["status"] == "failed"
    assert state["plan"][0]["error"] == "sync exploded"


def test_sync_marks_failed_image_child_without_starting_runner(runtime_env, monkeypatch):
    mod, fake_db = runtime_env
    started = []
    child_state = {
        "_project_status": "done",
        "items": [
            {"idx": 0, "status": "done", "dst_tos_key": "ok.png"},
            {"idx": 1, "status": "failed", "attempts": 3, "error": "upstream"},
        ],
        "progress": {"total": 2, "done": 1, "failed": 1, "running": 0},
    }

    monkeypatch.setattr(
        mod,
        "generate_plan",
        lambda *args, **kwargs: [
            _item(0, kind="detail_images", ref={"source_detail_ids": [1, 2]}),
        ],
    )
    task_id = mod.create_bulk_translate_task(
        user_id=1,
        product_id=77,
        target_langs=["it"],
        content_types=["detail_images"],
        force_retranslate=False,
        video_params={},
        initiator={"user_id": 1, "user_name": "", "ip": "", "user_agent": ""},
    )
    state = _load_state(fake_db, task_id)
    state["plan"][0]["child_task_id"] = "img-1"
    state["plan"][0]["child_task_type"] = "image_translate"
    state["plan"][0]["status"] = "running"
    fake_db.rows[task_id]["status"] = "failed"
    fake_db.rows[task_id]["state_json"] = json.dumps(state, ensure_ascii=False)

    monkeypatch.setattr(mod, "_load_child_snapshot", lambda task_type, child_task_id: child_state)
    monkeypatch.setattr(
        mod,
        "_start_image_runner",
        lambda child_task_id, uid: started.append((child_task_id, uid)),
        raising=False,
    )

    result = mod.sync_task_with_children_once(task_id, user_id=1)

    assert result["actions"] == []
    assert started == []
    assert child_state["_project_status"] == "done"
    assert child_state["items"][0]["status"] == "done"
    assert child_state["items"][1]["status"] == "failed"
    assert child_state["items"][1]["attempts"] == 3
    assert child_state["items"][1]["error"] == "upstream"
    state = _load_state(fake_db, task_id)
    assert fake_db.rows[task_id]["status"] == "failed"
    assert state["plan"][0]["status"] == "failed"
    assert state["plan"][0]["error"] == "upstream"
    assert "system_auto_retry_count" not in state["plan"][0]


def test_sync_does_not_auto_retry_image_child_twice(runtime_env, monkeypatch):
    mod, fake_db = runtime_env
    started = []
    child_state = {
        "_project_status": "done",
        "items": [{"idx": 0, "status": "failed", "attempts": 3, "error": "still bad"}],
        "progress": {"total": 1, "done": 0, "failed": 1, "running": 0},
    }

    monkeypatch.setattr(
        mod,
        "generate_plan",
        lambda *args, **kwargs: [_item(0, kind="video_covers", ref={"source_raw_ids": [301]})],
    )
    task_id = mod.create_bulk_translate_task(
        user_id=1,
        product_id=77,
        target_langs=["it"],
        content_types=["video_covers"],
        force_retranslate=False,
        video_params={},
        initiator={"user_id": 1, "user_name": "", "ip": "", "user_agent": ""},
        raw_source_ids=[301],
    )
    state = _load_state(fake_db, task_id)
    state["plan"][0]["child_task_id"] = "img-1"
    state["plan"][0]["child_task_type"] = "image_translate"
    state["plan"][0]["status"] = "running"
    state["plan"][0]["system_auto_retry_count"] = 1
    fake_db.rows[task_id]["status"] = "failed"
    fake_db.rows[task_id]["state_json"] = json.dumps(state, ensure_ascii=False)

    monkeypatch.setattr(mod, "_load_child_snapshot", lambda task_type, child_task_id: child_state)
    monkeypatch.setattr(
        mod,
        "_start_image_runner",
        lambda child_task_id, uid: started.append((child_task_id, uid)),
        raising=False,
    )

    result = mod.sync_task_with_children_once(task_id, user_id=1)

    assert result["actions"] == []
    assert started == []
    assert child_state["items"][0]["status"] == "failed"
    assert fake_db.rows[task_id]["status"] == "failed"
    state = _load_state(fake_db, task_id)
    assert state["plan"][0]["status"] == "failed"
    assert state["plan"][0]["system_auto_retry_count"] == 1


def test_sync_preserves_interrupted_item_until_manual_restart(runtime_env, monkeypatch):
    mod, fake_db = runtime_env
    child_state = {
        "_project_status": "done",
        "items": [{"idx": 0, "status": "failed", "attempts": 3, "error": "failed while server was down"}],
    }

    monkeypatch.setattr(
        mod,
        "generate_plan",
        lambda *args, **kwargs: [_item(0, kind="video_covers", ref={"source_raw_ids": [301]})],
    )
    task_id = mod.create_bulk_translate_task(
        user_id=1,
        product_id=77,
        target_langs=["it"],
        content_types=["video_covers"],
        force_retranslate=False,
        video_params={},
        initiator={"user_id": 1, "user_name": "", "ip": "", "user_agent": ""},
        raw_source_ids=[301],
    )
    state = _load_state(fake_db, task_id)
    state["plan"][0]["child_task_id"] = "img-1"
    state["plan"][0]["child_task_type"] = "image_translate"
    state["plan"][0]["status"] = "interrupted"
    fake_db.rows[task_id]["status"] = "interrupted"
    fake_db.rows[task_id]["state_json"] = json.dumps(state, ensure_ascii=False)

    monkeypatch.setattr(mod, "_load_child_snapshot", lambda task_type, child_task_id: child_state)

    result = mod.sync_task_with_children_once(task_id, user_id=1)

    assert result["actions"] == []
    assert fake_db.rows[task_id]["status"] == "interrupted"
    state = _load_state(fake_db, task_id)
    assert state["plan"][0]["status"] == "interrupted"
    assert state["plan"][0]["error"] is None


def test_sync_marks_failed_video_child_without_starting_runner(runtime_env, monkeypatch):
    mod, fake_db = runtime_env
    resumed = []
    child_state = {
        "_project_status": "error",
        "selected_voice_id": "voice-1",
        "current_review_step": "",
        "steps": {
            "extract": "done",
            "asr": "done",
            "voice_match": "done",
            "alignment": "done",
            "translate": "done",
            "tts": "error",
            "subtitle": "pending",
            "compose": "pending",
            "export": "pending",
        },
        "step_messages": {"tts": "boom"},
        "error": "boom",
    }

    monkeypatch.setattr(
        mod,
        "generate_plan",
        lambda *args, **kwargs: [_item(0, kind="videos", ref={"source_raw_id": 301})],
    )
    task_id = mod.create_bulk_translate_task(
        user_id=1,
        product_id=77,
        target_langs=["it"],
        content_types=["videos"],
        force_retranslate=False,
        video_params={},
        initiator={"user_id": 1, "user_name": "", "ip": "", "user_agent": ""},
        raw_source_ids=[301],
    )
    state = _load_state(fake_db, task_id)
    state["plan"][0]["child_task_id"] = "video-1"
    state["plan"][0]["child_task_type"] = "multi_translate"
    state["plan"][0]["status"] = "running"
    fake_db.rows[task_id]["status"] = "failed"
    fake_db.rows[task_id]["state_json"] = json.dumps(state, ensure_ascii=False)

    monkeypatch.setattr(mod, "_load_child_snapshot", lambda task_type, child_task_id: child_state)
    monkeypatch.setattr(
        mod,
        "_resume_video_runner",
        lambda child_task_id, start_step, uid: resumed.append((child_task_id, start_step, uid)),
        raising=False,
    )

    result = mod.sync_task_with_children_once(task_id, user_id=1)

    assert result["actions"] == []
    assert resumed == []
    assert child_state["_project_status"] == "error"
    assert child_state["steps"]["tts"] == "error"
    assert child_state["steps"]["subtitle"] == "pending"
    assert child_state["error"] == "boom"
    state = _load_state(fake_db, task_id)
    assert fake_db.rows[task_id]["status"] == "failed"
    assert state["plan"][0]["status"] == "failed"
    assert state["plan"][0]["error"] == "boom"
    assert "system_auto_retry_count" not in state["plan"][0]


def test_sync_completed_child_backfills_and_finishes_parent(runtime_env, monkeypatch):
    mod, fake_db = runtime_env
    synced = []

    monkeypatch.setattr(
        mod,
        "generate_plan",
        lambda *args, **kwargs: [_item(0, kind="detail_images", ref={"source_detail_ids": [1]})],
    )
    task_id = mod.create_bulk_translate_task(
        user_id=1,
        product_id=77,
        target_langs=["it"],
        content_types=["detail_images"],
        force_retranslate=False,
        video_params={},
        initiator={"user_id": 1, "user_name": "", "ip": "", "user_agent": ""},
    )
    state = _load_state(fake_db, task_id)
    state["plan"][0]["child_task_id"] = "img-1"
    state["plan"][0]["child_task_type"] = "image_translate"
    state["plan"][0]["status"] = "running"
    fake_db.rows[task_id]["status"] = "running"
    fake_db.rows[task_id]["state_json"] = json.dumps(state, ensure_ascii=False)

    monkeypatch.setattr(
        mod,
        "_load_child_snapshot",
        lambda task_type, child_task_id: {
            "_project_status": "done",
            "items": [{"idx": 0, "status": "done", "dst_tos_key": "ok.png"}],
        },
    )
    monkeypatch.setattr(
        mod,
        "_sync_child_result",
        lambda parent_id, item, parent_state, child_state: synced.append((parent_id, item["child_task_id"])),
    )

    result = mod.sync_task_with_children_once(task_id, user_id=1)

    assert result["actions"] == ["sync_child_result", "finish_parent"]
    assert synced == [(task_id, "img-1")]
    assert fake_db.rows[task_id]["status"] == "done"
    state = _load_state(fake_db, task_id)
    assert state["plan"][0]["status"] == "done"
    assert state["plan"][0]["result_synced"] is True


def test_resume_task_only_resets_interrupted_items(runtime_env, monkeypatch):
    mod, fake_db = runtime_env
    monkeypatch.setattr(
        mod,
        "generate_plan",
        lambda *args, **kwargs: [
            _item(0, status="done"),
            _item(1, status="interrupted"),
            _item(2, status="failed"),
        ],
    )

    task_id = mod.create_bulk_translate_task(
        user_id=1,
        product_id=77,
        target_langs=["de"],
        content_types=["videos"],
        force_retranslate=False,
        video_params={},
        initiator={"user_id": 1, "user_name": "", "ip": "", "user_agent": ""},
        raw_source_ids=[301],
    )
    fake_db.rows[task_id]["status"] = "interrupted"

    mod.resume_task(task_id, user_id=9)

    assert fake_db.rows[task_id]["status"] == "running"
    state = _load_state(fake_db, task_id)
    assert [item["status"] for item in state["plan"]] == ["done", "pending", "failed"]


def test_retry_failed_items_resets_failed_and_interrupted(runtime_env, monkeypatch):
    mod, fake_db = runtime_env
    monkeypatch.setattr(
        mod,
        "generate_plan",
        lambda *args, **kwargs: [
            _item(0, status="done"),
            _item(1, status="failed"),
            _item(2, status="interrupted"),
        ],
    )

    task_id = mod.create_bulk_translate_task(
        user_id=1,
        product_id=77,
        target_langs=["de"],
        content_types=["videos"],
        force_retranslate=False,
        video_params={},
        initiator={"user_id": 1, "user_name": "", "ip": "", "user_agent": ""},
        raw_source_ids=[301],
    )
    fake_db.rows[task_id]["status"] = "failed"

    mod.retry_failed_items(task_id, user_id=9)

    assert fake_db.rows[task_id]["status"] == "running"
    state = _load_state(fake_db, task_id)
    assert [item["status"] for item in state["plan"]] == ["done", "pending", "pending"]


def test_retry_failed_items_reuses_image_child_and_retries_only_failed_images(
    runtime_env,
    monkeypatch,
):
    mod, fake_db = runtime_env
    monkeypatch.setattr(
        mod,
        "generate_plan",
        lambda *args, **kwargs: [
            _item(
                0,
                kind="detail_images",
                status="failed",
                ref={"source_detail_ids": [11, 12, 13]},
            )
        ],
    )

    task_id = mod.create_bulk_translate_task(
        user_id=1,
        product_id=77,
        target_langs=["de"],
        content_types=["detail_images"],
        force_retranslate=False,
        video_params={},
        initiator={"user_id": 1, "user_name": "", "ip": "", "user_agent": ""},
    )
    state = _load_state(fake_db, task_id)
    state["plan"][0].update(
        {
            "child_task_id": "img-child-1",
            "sub_task_id": "img-child-1",
            "child_task_type": "image_translate",
            "error": "image_translate child failed (1 items): timeout",
            "result_synced": False,
            "finished_at": "2026-04-23T10:00:00+00:00",
        }
    )
    fake_db.rows[task_id]["state_json"] = json.dumps(state, ensure_ascii=False)
    fake_db.rows[task_id]["status"] = "failed"

    retried = []
    monkeypatch.setattr(
        mod,
        "_retry_failed_image_child_items",
        lambda item, user_id: retried.append((item["child_task_id"], user_id)) or 1,
        raising=False,
    )

    mod.retry_failed_items(task_id, user_id=9)

    assert retried == [("img-child-1", 9)]
    state = _load_state(fake_db, task_id)
    item = state["plan"][0]
    assert item["child_task_id"] == "img-child-1"
    assert item["sub_task_id"] == "img-child-1"
    assert item["child_task_type"] == "image_translate"
    assert item["status"] == "running"
    assert item["error"] is None
    assert item["finished_at"] is None


def test_refresh_task_from_children_syncs_recovered_image_child(runtime_env, monkeypatch):
    mod, fake_db = runtime_env
    monkeypatch.setattr(
        mod,
        "generate_plan",
        lambda *args, **kwargs: [
            _item(
                0,
                kind="detail_images",
                status="failed",
                ref={"source_detail_ids": [11, 12]},
            )
        ],
    )

    task_id = mod.create_bulk_translate_task(
        user_id=1,
        product_id=77,
        target_langs=["de"],
        content_types=["detail_images"],
        force_retranslate=False,
        video_params={},
        initiator={"user_id": 1, "user_name": "", "ip": "", "user_agent": ""},
    )
    state = _load_state(fake_db, task_id)
    state["plan"][0].update(
        {
            "child_task_id": "img-child-1",
            "sub_task_id": "img-child-1",
            "child_task_type": "image_translate",
            "error": "image_translate child failed (1 items): timeout",
            "finished_at": "2026-04-23T10:00:00+00:00",
        }
    )
    fake_db.rows[task_id]["state_json"] = json.dumps(state, ensure_ascii=False)
    fake_db.rows[task_id]["status"] = "failed"
    fake_db.rows["img-child-1"] = {
        "id": "img-child-1",
        "user_id": 1,
        "type": "image_translate",
        "status": "done",
        "state_json": json.dumps(
            {
                "items": [
                    {"idx": 0, "status": "done", "dst_tos_key": "out/0.png"},
                    {"idx": 1, "status": "done", "dst_tos_key": "out/1.png"},
                ]
            },
            ensure_ascii=False,
        ),
        "created_at": None,
    }

    def fake_sync(parent_id, item, parent_state, child_state):
        item["result_synced"] = True
        parent_state["synced_child"] = child_state["_project_status"]

    monkeypatch.setattr(mod, "_sync_child_result", fake_sync)

    refreshed = mod.refresh_task_from_children(task_id, user_id=1)

    assert refreshed["status"] == "done"
    assert fake_db.rows[task_id]["status"] == "done"
    state = _load_state(fake_db, task_id)
    assert state["synced_child"] == "done"
    assert state["plan"][0]["status"] == "done"
    assert state["plan"][0]["result_synced"] is True
    assert state["plan"][0]["error"] is None


def test_sync_task_with_children_does_not_auto_retry_image_child(runtime_env, monkeypatch):
    mod, fake_db = runtime_env
    monkeypatch.setattr(
        mod,
        "generate_plan",
        lambda *args, **kwargs: [
            _item(
                0,
                kind="detail_images",
                status="failed",
                ref={"source_detail_ids": [11, 12]},
            )
        ],
    )

    task_id = mod.create_bulk_translate_task(
        user_id=1,
        product_id=77,
        target_langs=["de"],
        content_types=["detail_images"],
        force_retranslate=False,
        video_params={},
        initiator={"user_id": 1, "user_name": "", "ip": "", "user_agent": ""},
    )
    state = _load_state(fake_db, task_id)
    state["plan"][0].update(
        {
            "child_task_id": "img-child-1",
            "sub_task_id": "img-child-1",
            "child_task_type": "image_translate",
            "error": "image_translate child failed (1 items): timeout",
            "finished_at": "2026-04-23T10:00:00+00:00",
        }
    )
    fake_db.rows[task_id]["state_json"] = json.dumps(state, ensure_ascii=False)
    fake_db.rows[task_id]["status"] = "failed"

    retried = []
    monkeypatch.setattr(
        mod,
        "_retry_failed_image_child_items",
        lambda item, user_id: retried.append((item["child_task_id"], user_id)) or 1,
        raising=False,
    )

    mod.sync_task_with_children_once(task_id, user_id=1)

    assert retried == []
    assert fake_db.rows[task_id]["status"] == "failed"
    item = _load_state(fake_db, task_id)["plan"][0]
    assert item["status"] == "failed"
    assert "system_auto_retry_count" not in item
    assert "system_auto_retry_reason" not in item
    assert "system_auto_retry_exhausted" not in item


def test_sync_task_with_children_does_not_auto_retry_image_twice(runtime_env, monkeypatch):
    mod, fake_db = runtime_env
    monkeypatch.setattr(
        mod,
        "generate_plan",
        lambda *args, **kwargs: [
            _item(
                0,
                kind="detail_images",
                status="failed",
                ref={"source_detail_ids": [11, 12]},
            )
        ],
    )

    task_id = mod.create_bulk_translate_task(
        user_id=1,
        product_id=77,
        target_langs=["de"],
        content_types=["detail_images"],
        force_retranslate=False,
        video_params={},
        initiator={"user_id": 1, "user_name": "", "ip": "", "user_agent": ""},
    )
    state = _load_state(fake_db, task_id)
    state["plan"][0].update(
        {
            "child_task_id": "img-child-1",
            "sub_task_id": "img-child-1",
            "child_task_type": "image_translate",
            "system_auto_retry_count": 1,
        }
    )
    fake_db.rows[task_id]["state_json"] = json.dumps(state, ensure_ascii=False)
    fake_db.rows[task_id]["status"] = "failed"

    monkeypatch.setattr(
        mod,
        "_retry_failed_image_child_items",
        lambda *args, **kwargs: pytest.fail("system auto retry must not run twice"),
        raising=False,
    )

    mod.sync_task_with_children_once(task_id, user_id=1)

    assert fake_db.rows[task_id]["status"] == "failed"
    item = _load_state(fake_db, task_id)["plan"][0]
    assert item["status"] == "failed"
    assert item["system_auto_retry_count"] == 1
    assert "system_auto_retry_exhausted" not in item


def test_sync_task_with_children_does_not_auto_resume_video_after_voice(runtime_env, monkeypatch):
    mod, fake_db = runtime_env
    monkeypatch.setattr(
        mod,
        "generate_plan",
        lambda *args, **kwargs: [
            _item(0, kind="videos", status="failed", ref={"source_raw_id": 301})
        ],
    )

    task_id = mod.create_bulk_translate_task(
        user_id=1,
        product_id=77,
        target_langs=["de"],
        content_types=["videos"],
        force_retranslate=False,
        video_params={},
        initiator={"user_id": 1, "user_name": "", "ip": "", "user_agent": ""},
        raw_source_ids=[301],
    )
    state = _load_state(fake_db, task_id)
    state["plan"][0].update(
        {
            "child_task_id": "multi-child-1",
            "sub_task_id": "multi-child-1",
            "child_task_type": "multi_translate",
            "error": "tts failed",
            "finished_at": "2026-04-23T10:00:00+00:00",
        }
    )
    fake_db.rows[task_id]["state_json"] = json.dumps(state, ensure_ascii=False)
    fake_db.rows[task_id]["status"] = "failed"
    fake_db.rows["multi-child-1"] = {
        "id": "multi-child-1",
        "user_id": 1,
        "type": "multi_translate",
        "status": "failed",
        "state_json": json.dumps(
            {
                "selected_voice_id": "voice-1",
                "steps": {"voice_match": "done", "alignment": "done", "translate": "done", "tts": "failed"},
                "error": "tts failed",
            },
            ensure_ascii=False,
        ),
        "created_at": None,
    }

    reset_calls = []
    resume_calls = []
    monkeypatch.setattr(
        mod,
        "_reset_multi_translate_child_for_resume",
        lambda child_task_id, start_step: reset_calls.append((child_task_id, start_step)),
        raising=False,
    )

    mod.sync_task_with_children_once(task_id, user_id=1)

    assert reset_calls == []
    assert resume_calls == []
    assert fake_db.rows[task_id]["status"] == "failed"
    item = _load_state(fake_db, task_id)["plan"][0]
    assert item["status"] == "failed"
    assert "system_auto_retry_count" not in item
    assert "system_auto_retry_reason" not in item


def test_retry_item_resets_requested_idx(runtime_env, monkeypatch):
    mod, fake_db = runtime_env
    monkeypatch.setattr(
        mod,
        "generate_plan",
        lambda *args, **kwargs: [
            _item(0, status="done"),
            _item(1, status="awaiting_voice"),
            _item(2, status="done"),
        ],
    )

    task_id = mod.create_bulk_translate_task(
        user_id=1,
        product_id=77,
        target_langs=["de"],
        content_types=["videos"],
        force_retranslate=False,
        video_params={},
        initiator={"user_id": 1, "user_name": "", "ip": "", "user_agent": ""},
        raw_source_ids=[301],
    )
    fake_db.rows[task_id]["status"] = "waiting_manual"

    mod.retry_item(task_id, idx=1, user_id=9)

    assert fake_db.rows[task_id]["status"] == "running"
    state = _load_state(fake_db, task_id)
    assert [item["status"] for item in state["plan"]] == ["done", "pending", "done"]


def test_retry_item_reuses_image_child_when_parent_item_failed(
    runtime_env,
    monkeypatch,
):
    mod, fake_db = runtime_env
    monkeypatch.setattr(
        mod,
        "generate_plan",
        lambda *args, **kwargs: [
            _item(
                0,
                kind="detail_images",
                status="failed",
                ref={"source_detail_ids": [11, 12]},
            )
        ],
    )

    task_id = mod.create_bulk_translate_task(
        user_id=1,
        product_id=77,
        target_langs=["de"],
        content_types=["detail_images"],
        force_retranslate=False,
        video_params={},
        initiator={"user_id": 1, "user_name": "", "ip": "", "user_agent": ""},
    )
    state = _load_state(fake_db, task_id)
    state["plan"][0].update(
        {
            "child_task_id": "img-child-1",
            "sub_task_id": "img-child-1",
            "child_task_type": "image_translate",
            "error": "image_translate child failed (1 items): timeout",
            "finished_at": "2026-04-23T10:00:00+00:00",
        }
    )
    fake_db.rows[task_id]["state_json"] = json.dumps(state, ensure_ascii=False)
    fake_db.rows[task_id]["status"] = "failed"

    retried = []
    monkeypatch.setattr(
        mod,
        "_retry_failed_image_child_items",
        lambda item, user_id: retried.append((item["child_task_id"], user_id)) or 1,
        raising=False,
    )

    mod.retry_item(task_id, idx=0, user_id=9)

    assert retried == [("img-child-1", 9)]
    state = _load_state(fake_db, task_id)
    item = state["plan"][0]
    assert item["child_task_id"] == "img-child-1"
    assert item["status"] == "running"
    assert item["error"] is None


def test_materialize_multi_translate_cover_prefers_existing_translated_cover(
    runtime_env,
    monkeypatch,
    tmp_path,
):
    mod, _fake_db = runtime_env
    thumbnail = tmp_path / "thumbnail.jpg"
    thumbnail.write_bytes(b"video-thumbnail")
    translated_cover_key = "artifacts/image_translate/33/cover-task/out_0.jpg"

    monkeypatch.setattr(
        mod.medias,
        "get_raw_source",
        lambda raw_id: {
            "id": raw_id,
            "user_id": 33,
            "cover_object_key": "33/medias/6/raw_sources/source.cover.jpg",
        },
    )
    monkeypatch.setattr(
        mod.medias,
        "get_raw_source_translation",
        lambda raw_id, lang: {"cover_object_key": translated_cover_key},
    )
    monkeypatch.setattr(
        mod.local_media_storage,
        "write_bytes",
        lambda *_args, **_kwargs: pytest.fail("should not overwrite translated cover with video thumbnail"),
    )

    result = mod._materialize_multi_translate_cover(
        product_id=6,
        lang="it",
        source_raw_id=19,
        child_task_id="video-task",
        child_state={"thumbnail_path": str(thumbnail)},
    )

    assert result == translated_cover_key
