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
        if "DELETE FROM PROJECTS WHERE ID = %S" in s:
            (task_id,) = args
            self.rows.pop(task_id, None)
            return 1
        if "UPDATE PROJECTS SET TYPE = %S WHERE ID = %S" in s:
            new_type, task_id = args
            if task_id in self.rows:
                self.rows[task_id]["type"] = new_type
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


def test_create_detail_images_child_skips_gif_sources(runtime_env, monkeypatch, tmp_path):
    mod, _fake_db = runtime_env
    created = {}
    started = []

    monkeypatch.setattr(mod, "OUTPUT_DIR", str(tmp_path))
    monkeypatch.setattr(mod, "_ensure_child_identity", lambda parent_id, item: "img-child-1")
    monkeypatch.setattr(mod.medias, "get_language_name", lambda lang: "德语")
    monkeypatch.setattr(
        mod.medias,
        "list_detail_images",
        lambda product_id, lang: [
            {"id": 11, "object_key": "1/medias/77/en_1.jpg", "content_type": "image/jpeg"},
            {"id": 12, "object_key": "1/medias/77/en_2.gif"},
            {"id": 13, "object_key": "1/medias/77/en_3.png", "content_type": "image/gif; charset=binary"},
            {"id": 14, "object_key": "1/medias/77/en_4.webp"},
        ],
    )

    import appcore.image_translate_settings as its
    import appcore.task_state as task_state
    from web.routes import image_translate as image_translate_routes

    monkeypatch.setattr(its, "get_prompt", lambda preset, lang: "翻成 {target_language_name}")
    monkeypatch.setattr(
        task_state,
        "create_image_translate",
        lambda task_id, task_dir, **kwargs: created.update(
            {"task_id": task_id, "task_dir": task_dir, **kwargs}
        ) or {"id": task_id},
    )
    monkeypatch.setattr(
        image_translate_routes,
        "start_image_translate_runner",
        lambda task_id, user_id: started.append((task_id, user_id)) or True,
    )

    child_task_id, child_type, child_status = mod._create_detail_images_child(
        "parent-1",
        _item(0, kind="detail_images", lang="de", ref={"source_detail_ids": [11, 12, 13, 14]}),
        {"product_id": 77, "initiator": {"user_id": 1}},
    )

    assert (child_task_id, child_type, child_status) == ("img-child-1", "image_translate", "running")
    assert [it["source_detail_image_id"] for it in created["items"]] == [11, 14]
    assert created["medias_context"]["source_detail_image_ids"] == [11, 14]
    assert started == [("img-child-1", 1)]


def test_create_stores_plan_and_raw_source_ids(runtime_env, monkeypatch):
    mod, fake_db = runtime_env
    monkeypatch.setattr(
        mod,
        "do_estimate",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("estimate should not run during task creation")),
    )
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
    assert state["cost_tracking"] == {
        "actual": {
            "copy_tokens_used": 0,
            "image_processed": 0,
            "video_minutes_processed": 0.0,
            "actual_cost_cny": 0.0,
        }
    }
    assert "estimated_cost_cny" not in state["audit_events"][0]["detail"]


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


def test_run_scheduler_marks_due_item_failed_when_child_creation_crashes(runtime_env, monkeypatch):
    mod, fake_db = runtime_env
    monkeypatch.setattr(
        mod,
        "generate_plan",
        lambda *args, **kwargs: [
            _item(0, kind="detail_images", ref={"source_detail_ids": [1]}, dispatch_after_seconds=0),
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
        raw_source_ids=[],
    )
    mod.start_task(task_id, user_id=1)
    state = _load_state(fake_db, task_id)
    state["scheduler_anchor_ts"] = 0
    _store_state(fake_db, task_id, state)

    def crash_create_child(parent_id, item, parent_state):
        raise RuntimeError("temporary child dispatch failure")

    monkeypatch.setattr(mod, "_create_child_task", crash_create_child)

    mod.run_scheduler(
        task_id,
        now_provider=lambda: 10,
        sleep_fn=lambda *_args, **_kwargs: None,
        max_loops=1,
    )

    assert fake_db.rows[task_id]["status"] == "failed"
    state = _load_state(fake_db, task_id)
    assert state["progress"]["failed"] == 1
    assert state["progress"]["pending"] == 0
    assert state["plan"][0]["status"] == "failed"
    assert "temporary child dispatch failure" in state["plan"][0]["error"]
    assert state["plan"][0]["finished_at"]


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


@pytest.mark.parametrize("failed_status", ["error", "failed", "interrupted", "cancelled"])
def test_create_child_task_rebuilds_terminal_failed_existing_child(
    runtime_env, monkeypatch, failed_status,
):
    """事故场景（2026-04-27 product 537 idx 12/13）：retry 之后 stable child id
    复用旧 row（status=interrupted/error）→ 旧逻辑直接 return existing 不启动 runner，
    sync 立刻把 item 标回 failed，无限死循环。新逻辑必须删旧 row + 走真实创建分支。
    """
    mod, fake_db = runtime_env
    item = _item(0, kind="videos", ref={"source_raw_id": 301})
    expected_child_id = uuid.uuid5(uuid.NAMESPACE_URL, "bulk_translate:parent-1:0").hex
    fake_db.rows[expected_child_id] = {
        "id": expected_child_id,
        "user_id": 1,
        "type": "multi_translate",
        "status": failed_status,
        "state_json": "{}",
        "created_at": None,
    }

    create_calls: list[tuple[str, str]] = []

    def fake_create_video(parent_id, child_item, parent_state):
        create_calls.append((parent_id, child_item.get("kind") or ""))
        # 模拟真实创建：插入新 row + 标 type
        fake_db.execute(
            "INSERT INTO projects (id, user_id, type, status, state_json) "
            "VALUES (%s, %s, 'bulk_translate', 'planning', %s)",
            (expected_child_id, 1, "{}"),
        )
        fake_db.execute(
            "UPDATE projects SET type = %s WHERE id = %s",
            ("multi_translate", expected_child_id),
        )
        return expected_child_id, "multi_translate", "running"

    monkeypatch.setattr(mod, "_create_video_child", fake_create_video)

    child_id, child_type, child_status = mod._create_child_task(
        "parent-1",
        item,
        {"product_id": 77, "initiator": {"user_id": 1}},
    )

    assert child_id == expected_child_id
    assert child_type == "multi_translate"
    assert child_status == "running"
    # 关键回归：必须真的走 _create_video_child（启动 runner），而不是 return existing
    assert create_calls == [("parent-1", "videos")]
    # 旧 row 已被替换，现在新 row 的 status 是 fake_create 写的 'planning' / 之后的更新
    assert fake_db.rows[expected_child_id]["type"] == "multi_translate"


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


def test_create_video_child_routes_ja_to_multi_translate(runtime_env, monkeypatch, tmp_path):
    mod, _fake_db = runtime_env
    raw_key = "1/medias/77/raw_sources/raw-ja-demo.mp4"
    created = {}
    updated = {}
    multi_started = []
    ja_started = []

    monkeypatch.setattr(mod, "OUTPUT_DIR", str(tmp_path / "output"))
    monkeypatch.setattr(mod, "UPLOAD_DIR", str(tmp_path / "uploads"))
    monkeypatch.setattr(
        mod.medias,
        "get_raw_source",
        lambda rid: {
            "id": rid,
            "product_id": 77,
            "user_id": 1,
            "display_name": "raw-ja-demo",
            "video_object_key": raw_key,
            "file_size": 2345,
        },
    )
    monkeypatch.setattr(mod, "execute", lambda *args, **kwargs: 1)

    import web.store as store
    from web.services import ja_pipeline_runner, multi_pipeline_runner

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
    monkeypatch.setattr(multi_pipeline_runner, "start", lambda task_id, user_id: multi_started.append((task_id, user_id)))
    monkeypatch.setattr(ja_pipeline_runner, "start", lambda task_id, user_id: ja_started.append((task_id, user_id)))

    child_task_id, child_type, status = mod._create_video_child(
        "parent-ja-1",
        _item(0, kind="videos", lang="ja", ref={"source_raw_id": 301}),
        {
            "product_id": 77,
            "initiator": {"user_id": 1},
            "video_params_snapshot": {"subtitle_size": 20, "subtitle_position_y": 0.6},
        },
    )

    assert child_type == "multi_translate"
    assert status == "running"
    assert created["download"] == (raw_key, created["video_path"])
    assert multi_started == [(child_task_id, 1)]
    assert ja_started == []
    assert updated[child_task_id]["type"] == "multi_translate"
    assert updated[child_task_id]["target_lang"] == "ja"


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


def test_force_backfill_item_marks_detail_image_item_done_and_rolls_up_cost(
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
                    {"idx": 1, "status": "failed", "error": "timeout"},
                ],
                "medias_context": {"apply_status": "pending"},
            },
            ensure_ascii=False,
        ),
        "created_at": None,
    }

    monkeypatch.setattr(
        mod,
        "_force_backfill_detail_image_child",
        lambda item, child_state, user_id: {
            "applied_ids": [901, 902],
            "skipped_failed_indices": [1],
            "apply_status": "applied_partial",
        },
        raising=False,
    )

    mod.force_backfill_item(task_id, idx=0, user_id=9)

    assert fake_db.rows[task_id]["status"] == "done"
    state = _load_state(fake_db, task_id)
    item = state["plan"][0]
    assert item["status"] == "done"
    assert item["result_synced"] is True
    assert item["error"] is None
    assert item["forced_backfill"] is True
    assert item["forced_backfill_applied_count"] == 2
    assert item["forced_backfill_skipped_failed_count"] == 1
    assert state["cost_tracking"]["actual"]["image_processed"] == 3
    assert state["audit_events"][-1]["action"] == "force_backfill_item"
    assert state["audit_events"][-1]["detail"]["idx"] == 0


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


def test_materialize_multi_translate_cover_never_uses_video_thumbnail_without_translated_cover(
    runtime_env,
    monkeypatch,
    tmp_path,
):
    mod, _fake_db = runtime_env
    thumbnail = tmp_path / "thumbnail.jpg"
    thumbnail.write_bytes(b"video-thumbnail")

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
        lambda raw_id, lang: None,
    )
    monkeypatch.setattr(
        mod.local_media_storage,
        "write_bytes",
        lambda *_args, **_kwargs: pytest.fail("video thumbnails must not become material covers"),
    )

    result = mod._materialize_multi_translate_cover(
        product_id=6,
        lang="it",
        source_raw_id=19,
        child_task_id="video-task",
        child_state={"thumbnail_path": str(thumbnail)},
    )

    assert result == ""


def test_sync_child_result_refreshes_video_covers_for_any_translation_kind(runtime_env, monkeypatch):
    mod, _fake_db = runtime_env
    refreshed = []
    monkeypatch.setattr(
        mod,
        "refresh_translated_video_item_covers_for_scope",
        lambda product_id, lang: refreshed.append((product_id, lang)) or 3,
    )

    mod._sync_child_result(
        "bt-1",
        {"kind": "copywriting", "lang": "de", "child_task_id": "copy-1"},
        {"product_id": 77},
        {"_project_status": "done"},
    )

    assert refreshed == [(77, "de")]


# ---------------------------------------------------------------------------
# Fail-fast 调度回归（事故：2026-04-27 product 537 6 个 pending 永远 stuck）
# ---------------------------------------------------------------------------


def test_derive_parent_status_keeps_running_when_pending_exists_alongside_failed():
    """1 项失败 + 多项未派发 pending → 父任务必须保留 running，
    否则下一轮 sync 会把 status 写成 failed，scheduler 顶部读到立即退出，
    pending 永远 stuck。"""
    from appcore import bulk_translate_runtime as mod

    plan = [
        {"status": "done"},
        {"status": "failed"},
        {"status": "pending"},
        {"status": "pending"},
    ]
    assert mod._derive_parent_status(plan, "running") == "running"


def test_derive_parent_status_marks_failed_only_when_no_pending_or_active():
    """plan 完全 terminal 且含 retryable → 父任务才标 failed。"""
    from appcore import bulk_translate_runtime as mod

    plan_all_terminal_with_failure = [
        {"status": "done"},
        {"status": "failed"},
        {"status": "skipped"},
    ]
    assert mod._derive_parent_status(plan_all_terminal_with_failure, "running") == "failed"


def test_derive_parent_status_running_when_active_item_with_failed_sibling():
    """有 active item（dispatching/running）+ failed 兄弟 → 不能标 failed。"""
    from appcore import bulk_translate_runtime as mod

    plan = [{"status": "running"}, {"status": "failed"}]
    assert mod._derive_parent_status(plan, "running") == "running"


def test_derive_parent_status_waiting_manual_takes_priority_over_failed():
    """awaiting_voice 子项 + failed 兄弟 → waiting_manual（仍是调度态，
    scheduler 会继续派发其它 pending）。"""
    from appcore import bulk_translate_runtime as mod

    plan = [{"status": "awaiting_voice"}, {"status": "failed"}]
    assert mod._derive_parent_status(plan, "running") == "waiting_manual"


def test_sync_does_not_mark_parent_failed_when_pending_items_remain(runtime_env, monkeypatch):
    """事故根因回归：sync 看到 1 项失败子任务 + 多项未派发 pending 时，
    若把父任务写成 failed，scheduler 下一轮 status not in {running, waiting_manual}
    立即 return，pending 永远不派发。这里固化"父级保持 running"。"""
    mod, fake_db = runtime_env

    monkeypatch.setattr(
        mod,
        "generate_plan",
        lambda *args, **kwargs: [
            _item(0, kind="videos", lang="fr", ref={"source_raw_id": 401}),
            _item(1, kind="videos", lang="de", ref={"source_raw_id": 402},
                  dispatch_after_seconds=10),
            _item(2, kind="videos", lang="es", ref={"source_raw_id": 403},
                  dispatch_after_seconds=20),
        ],
    )
    task_id = mod.create_bulk_translate_task(
        user_id=1,
        product_id=77,
        target_langs=["fr", "de", "es"],
        content_types=["videos"],
        force_retranslate=False,
        video_params={},
        initiator={"user_id": 1, "user_name": "", "ip": "", "user_agent": ""},
        raw_source_ids=[401, 402, 403],
    )
    state = _load_state(fake_db, task_id)
    # 模拟 idx=0 已派发并跑挂（child status='error'），idx=1/2 还是 pending
    state["plan"][0]["child_task_id"] = "video-fr"
    state["plan"][0]["child_task_type"] = "multi_translate"
    state["plan"][0]["status"] = "running"
    fake_db.rows[task_id]["status"] = "running"
    fake_db.rows[task_id]["state_json"] = json.dumps(state, ensure_ascii=False)

    monkeypatch.setattr(
        mod,
        "_load_child_snapshot",
        lambda task_type, child_task_id: {
            "_project_status": "error",
            "error": "list indices must be integers or slices, not str",
        },
    )

    mod.sync_task_with_children_once(task_id, user_id=1)

    state = _load_state(fake_db, task_id)
    assert state["plan"][0]["status"] == "failed"
    assert state["plan"][1]["status"] == "pending"
    assert state["plan"][2]["status"] == "pending"
    # 关键回归断言：父级不能因 1 项失败就被标 failed —— pending 还在等派发
    assert fake_db.rows[task_id]["status"] == "running"


def test_retry_item_keeps_parent_running_when_other_failed_items_remain(runtime_env, monkeypatch):
    """事故场景：用户 retry idx=0 时 idx=1 仍 failed。旧逻辑因 _derive_parent_status
    见 failed 直接返回 failed，路由后启动的 scheduler 第一轮就 return 退出，
    被 retry 的 idx=0 永远不派发。新逻辑保留 running。"""
    mod, fake_db = runtime_env

    monkeypatch.setattr(
        mod,
        "generate_plan",
        lambda *args, **kwargs: [
            _item(0, kind="videos", lang="de", ref={"source_raw_id": 501}),
            _item(1, kind="videos", lang="fr", ref={"source_raw_id": 502}),
        ],
    )
    task_id = mod.create_bulk_translate_task(
        user_id=1,
        product_id=77,
        target_langs=["de", "fr"],
        content_types=["videos"],
        force_retranslate=False,
        video_params={},
        initiator={"user_id": 1, "user_name": "", "ip": "", "user_agent": ""},
        raw_source_ids=[501, 502],
    )
    state = _load_state(fake_db, task_id)
    state["plan"][0]["status"] = "failed"
    state["plan"][0]["child_task_id"] = "child-de"
    state["plan"][0]["child_task_type"] = "multi_translate"
    state["plan"][0]["error"] = "boom"
    state["plan"][1]["status"] = "failed"
    state["plan"][1]["child_task_id"] = "child-fr"
    state["plan"][1]["child_task_type"] = "multi_translate"
    state["plan"][1]["error"] = "list indices must be integers or slices, not str"
    fake_db.rows[task_id]["status"] = "failed"
    fake_db.rows[task_id]["state_json"] = json.dumps(state, ensure_ascii=False)

    mod.retry_item(task_id, idx=0, user_id=1)

    state = _load_state(fake_db, task_id)
    assert state["plan"][0]["status"] == "pending"
    assert state["plan"][1]["status"] == "failed"
    # 关键回归：retry 之后父级必须是 running，否则 spawn 出来的 scheduler 立即 return
    assert fake_db.rows[task_id]["status"] == "running"


def test_spawn_scheduler_logs_exception_instead_of_swallowing(monkeypatch, caplog):
    """事故诊断盲区：原 _spawn_scheduler 用 try/except: pass 吞掉所有异常，
    导致 scheduler greenthread 死亡时 journal 完全无 traceback。改为 log.exception
    保留堆栈。"""
    import logging as _logging

    from web.routes import bulk_translate as routes_mod

    class _DummySocketIO:
        def emit(self, *_a, **_kw):
            pass

    class _DummyExtensions:
        socketio = _DummySocketIO()

    import sys as _sys
    monkeypatch.setitem(_sys.modules, "web.extensions", _DummyExtensions())

    def boom(*_args, **_kwargs):
        raise RuntimeError("scheduler exploded")

    monkeypatch.setattr(routes_mod, "run_scheduler", boom)

    with caplog.at_level(_logging.ERROR, logger=routes_mod.log.name):
        routes_mod._spawn_scheduler("task-xyz")

    assert any(
        record.levelno == _logging.ERROR
        and "scheduler crashed" in record.getMessage()
        and "task-xyz" in record.getMessage()
        and record.exc_info is not None
        for record in caplog.records
    ), [r.getMessage() for r in caplog.records]
