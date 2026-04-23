"""bulk_translate runtime 编排测试。

聚焦新版“父任务派发 + 轮询子任务 + 手工恢复”状态机。
"""
from __future__ import annotations

import json

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


def test_run_scheduler_failed_child_stops_parent(runtime_env, monkeypatch):
    mod, fake_db = runtime_env
    monkeypatch.setattr(
        mod,
        "generate_plan",
        lambda *args, **kwargs: [
            _item(0, kind="copywriting", ref={"source_copy_id": 11}),
            _item(1, kind="videos", dispatch_after_seconds=120),
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

    monkeypatch.setattr(
        mod,
        "_create_child_task",
        lambda parent_id, item, parent_state: ("copy-1", "copywriting_translate", "running"),
    )
    monkeypatch.setattr(
        mod,
        "_load_child_snapshot",
        lambda task_type, child_task_id: {"_project_status": "error", "last_error": "boom"},
    )
    monkeypatch.setattr(mod, "_sync_child_result", lambda *args, **kwargs: None)

    mod.run_scheduler(
        task_id,
        now_provider=lambda: 0,
        sleep_fn=lambda *_args, **_kwargs: None,
        max_loops=3,
    )

    assert fake_db.rows[task_id]["status"] == "failed"
    state = _load_state(fake_db, task_id)
    assert state["plan"][0]["status"] == "failed"
    assert state["plan"][0]["error"] == "boom"
    assert state["plan"][1]["status"] == "pending"


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
