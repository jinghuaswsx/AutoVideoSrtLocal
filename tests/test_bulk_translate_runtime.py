"""bulk_translate_runtime Task 16-17-18-20 单元测试。

全部 mock DB / plan / estimator,不依赖真实数据库。
"""
import json

import pytest


# ============================================================
# 共用 fake DB:模拟 projects 表读写
# ============================================================
class _FakeProjectsDB:
    """模拟 appcore.db.execute / query_one(只关心 projects 表)。"""

    def __init__(self):
        self.rows = {}   # task_id -> {id, user_id, type, status, state_json, ...}
        self.insert_log = []
        self.update_log = []

    def execute(self, sql, args=None):
        s = sql.upper()
        if "INSERT INTO PROJECTS" in s:
            # args: (task_id, user_id, state_json)
            task_id, user_id, state_json = args
            self.rows[task_id] = {
                "id": task_id,
                "user_id": user_id,
                "type": "bulk_translate",
                "status": "planning",
                "state_json": state_json,
                "created_at": None,
                "updated_at": None,
            }
            self.insert_log.append(args)
            return 1
        if "UPDATE PROJECTS" in s:
            # 支持:
            #   UPDATE projects SET state_json=%s, status=%s WHERE id=%s
            #   UPDATE projects SET state_json=%s WHERE id=%s
            if "STATUS=" in s.replace(" ", ""):
                payload, status, task_id = args
                if task_id in self.rows:
                    self.rows[task_id]["state_json"] = payload
                    self.rows[task_id]["status"] = status
            else:
                payload, task_id = args
                if task_id in self.rows:
                    self.rows[task_id]["state_json"] = payload
            self.update_log.append((sql, args))
            return 1
        raise AssertionError(f"unexpected execute: {sql}")

    def query_one(self, sql, args=None):
        s = sql.upper()
        if "FROM PROJECTS" in s:
            task_id = args[0]
            return self.rows.get(task_id)
        raise AssertionError(f"unexpected query_one: {sql}")


@pytest.fixture
def fake_db(monkeypatch):
    fake = _FakeProjectsDB()
    from appcore import bulk_translate_runtime as mod
    monkeypatch.setattr(mod, "execute", fake.execute)
    monkeypatch.setattr(mod, "query_one", fake.query_one)
    # plan 生成器:返回 3 项
    monkeypatch.setattr(mod, "generate_plan", lambda *a, **kw: [
        {"idx": 0, "kind": "copy", "lang": "de",
         "ref": {"source_copy_id": 1}, "sub_task_id": None,
         "status": "pending", "error": None,
         "started_at": None, "finished_at": None},
        {"idx": 1, "kind": "copy", "lang": "fr",
         "ref": {"source_copy_id": 1}, "sub_task_id": None,
         "status": "pending", "error": None,
         "started_at": None, "finished_at": None},
        {"idx": 2, "kind": "video", "lang": "de",
         "ref": {"source_item_id": 9}, "sub_task_id": None,
         "status": "pending", "error": None,
         "started_at": None, "finished_at": None},
    ])
    # 预估:返回简化结构
    monkeypatch.setattr(mod, "do_estimate", lambda *a, **kw: {
        "copy_tokens": 500, "image_count": 0, "video_minutes": 1.0,
        "estimated_cost_cny": 1.25,
        "skipped": {"copy": 0, "cover": 0, "detail": 0, "video": 0},
        "breakdown": {"copy_cny": 0.3, "image_cny": 0, "video_cny": 0.95},
    })
    return fake


# ============================================================
# Task 16:create_bulk_translate_task
# ============================================================

def test_create_returns_uuid_and_stores_planning(fake_db):
    from appcore.bulk_translate_runtime import create_bulk_translate_task
    initiator = {"user_id": 1, "user_name": "Tester",
                 "ip": "1.2.3.4", "user_agent": "pytest"}

    tid = create_bulk_translate_task(
        user_id=1, product_id=77,
        target_langs=["de", "fr"],
        content_types=["copy", "video"],
        force_retranslate=False,
        video_params={"subtitle_size": 14},
        initiator=initiator,
    )

    assert isinstance(tid, str) and len(tid) >= 32
    row = fake_db.rows[tid]
    assert row["type"] == "bulk_translate"
    assert row["status"] == "planning"

    state = json.loads(row["state_json"])
    assert state["product_id"] == 77
    assert state["target_langs"] == ["de", "fr"]
    assert state["content_types"] == ["copy", "video"]
    assert state["force_retranslate"] is False
    assert state["video_params_snapshot"] == {"subtitle_size": 14}
    assert state["initiator"] == initiator
    assert len(state["plan"]) == 3
    assert state["current_idx"] == 0
    assert state["cancel_requested"] is False

    # audit_events 初始有一条 create
    assert len(state["audit_events"]) == 1
    evt = state["audit_events"][0]
    assert evt["action"] == "create"
    assert evt["user_id"] == 1
    assert evt["detail"]["estimated_cost_cny"] == 1.25

    # cost_tracking 完整
    assert state["cost_tracking"]["estimate"]["estimated_cost_cny"] == 1.25
    assert state["cost_tracking"]["actual"]["actual_cost_cny"] == 0.0

    # progress 计算正确:3 项全 pending
    assert state["progress"] == {
        "total": 3, "pending": 3,
        "done": 0, "running": 0, "failed": 0, "skipped": 0,
    }


def test_create_empty_plan_still_creates_task(fake_db, monkeypatch):
    """产品无素材 → plan 空 → 仍然可以 create,只是 total=0。"""
    from appcore import bulk_translate_runtime as mod
    monkeypatch.setattr(mod, "generate_plan", lambda *a, **kw: [])

    tid = mod.create_bulk_translate_task(
        user_id=1, product_id=77, target_langs=["de"],
        content_types=["copy"], force_retranslate=False,
        video_params={},
        initiator={"user_id": 1, "user_name": "",
                    "ip": "", "user_agent": ""},
    )
    state = json.loads(fake_db.rows[tid]["state_json"])
    assert state["progress"]["total"] == 0
    assert state["progress"]["pending"] == 0


def test_create_stores_raw_source_ids_and_passes_to_generate_plan(fake_db, monkeypatch):
    from appcore import bulk_translate_runtime as mod

    seen = {}

    def fake_generate_plan(user_id, product_id, target_langs, content_types, force_retranslate, raw_source_ids=None):
        seen["raw_source_ids"] = raw_source_ids
        return [{
            "idx": 0,
            "kind": "video",
            "lang": "de",
            "ref": {"source_raw_id": 301},
            "sub_task_id": None,
            "status": "pending",
            "error": None,
            "started_at": None,
            "finished_at": None,
        }]

    monkeypatch.setattr(mod, "generate_plan", fake_generate_plan)

    tid = mod.create_bulk_translate_task(
        user_id=1,
        product_id=77,
        target_langs=["de"],
        content_types=["video"],
        force_retranslate=False,
        video_params={"subtitle_size": 16},
        initiator={"user_id": 1, "user_name": "", "ip": "", "user_agent": ""},
        raw_source_ids=[301, 302],
    )

    assert seen["raw_source_ids"] == [301, 302]
    state = json.loads(fake_db.rows[tid]["state_json"])
    assert state["raw_source_ids"] == [301, 302]


# ============================================================
# Task 16:get_task
# ============================================================

def test_get_task_not_found(fake_db):
    from appcore.bulk_translate_runtime import get_task
    assert get_task("nonexistent") is None


def test_get_task_returns_parsed_state(fake_db):
    from appcore.bulk_translate_runtime import (
        create_bulk_translate_task, get_task,
    )
    tid = create_bulk_translate_task(
        user_id=1, product_id=77, target_langs=["de"],
        content_types=["copy"], force_retranslate=False,
        video_params={},
        initiator={"user_id": 1, "user_name": "", "ip": "", "user_agent": ""},
    )
    task = get_task(tid)
    assert task is not None
    assert task["id"] == tid
    assert task["user_id"] == 1
    assert task["status"] == "planning"
    assert task["state"]["product_id"] == 77


# ============================================================
# Task 16:start_task
# ============================================================

def test_start_transitions_planning_to_running(fake_db):
    from appcore.bulk_translate_runtime import (
        create_bulk_translate_task, start_task, get_task,
    )
    tid = create_bulk_translate_task(
        user_id=1, product_id=77, target_langs=["de"],
        content_types=["copy"], force_retranslate=False,
        video_params={},
        initiator={"user_id": 1, "user_name": "", "ip": "", "user_agent": ""},
    )

    start_task(tid, user_id=1)
    task = get_task(tid)
    assert task["status"] == "running"

    # 追加了一条 start 审计
    actions = [e["action"] for e in task["state"]["audit_events"]]
    assert actions == ["create", "start"]


def test_start_rejects_non_planning(fake_db):
    from appcore.bulk_translate_runtime import (
        create_bulk_translate_task, start_task,
    )
    tid = create_bulk_translate_task(
        user_id=1, product_id=77, target_langs=["de"],
        content_types=["copy"], force_retranslate=False,
        video_params={},
        initiator={"user_id": 1, "user_name": "", "ip": "", "user_agent": ""},
    )
    start_task(tid, user_id=1)
    # 第二次 start 应报错
    with pytest.raises(ValueError, match="Cannot start"):
        start_task(tid, user_id=1)


def test_start_missing_task_raises(fake_db):
    from appcore.bulk_translate_runtime import start_task
    with pytest.raises(ValueError, match="not found"):
        start_task("nonexistent_task", user_id=1)


# ============================================================
# compute_progress
# ============================================================

def test_compute_progress_mixed_statuses():
    from appcore.bulk_translate_runtime import compute_progress
    plan = [
        {"status": "done"},
        {"status": "done"},
        {"status": "error"},     # failed
        {"status": "running"},
        {"status": "skipped"},
        {"status": "pending"},
    ]
    p = compute_progress(plan)
    assert p == {
        "total": 6, "done": 2, "running": 1,
        "failed": 1, "skipped": 1, "pending": 1,
    }


# ============================================================
# Task 17-18-19:run_scheduler 调度器行为
# ============================================================

def _prepare_running_task(fake_db, monkeypatch, plan, force=False):
    """准备一个已经处于 running 状态的父任务,plan 自定义。"""
    from appcore import bulk_translate_runtime as mod
    monkeypatch.setattr(mod, "generate_plan", lambda *a, **kw: plan)

    tid = mod.create_bulk_translate_task(
        user_id=1, product_id=77, target_langs=["de"],
        content_types=["copy"], force_retranslate=force,
        video_params={},
        initiator={"user_id": 1, "user_name": "", "ip": "", "user_agent": ""},
    )
    mod.start_task(tid, user_id=1)
    return tid


def _mk_pending(idx, kind="copy", lang="de", ref=None):
    return {
        "idx": idx, "kind": kind, "lang": lang,
        "ref": ref or {"source_copy_id": 100 + idx},
        "sub_task_id": None, "status": "pending",
        "error": None, "started_at": None, "finished_at": None,
    }


def test_scheduler_runs_all_items_serially(fake_db, monkeypatch):
    """3 项全部成功 → 父任务 done。dispatch 被调 3 次,顺序 0-1-2。"""
    plan = [_mk_pending(0), _mk_pending(1), _mk_pending(2)]
    tid = _prepare_running_task(fake_db, monkeypatch, plan)

    call_order = []
    from appcore import bulk_translate_runtime as mod

    def fake_dispatch(parent_id, item, parent_state, bus=None):
        call_order.append(item["idx"])
        return mod.SubTaskResult(
            sub_task_id=f"sub_{item['idx']}", status="done",
            tokens_used=10,
        )

    monkeypatch.setattr(mod, "_dispatch_sub_task", fake_dispatch)
    monkeypatch.setattr(mod, "_translation_exists_for_item", lambda item: False)

    mod.run_scheduler(tid)

    final = mod.get_task(tid)
    assert final["status"] == "done"
    assert call_order == [0, 1, 2]
    for p in final["state"]["plan"]:
        assert p["status"] == "done"
    # cost 累加:3 × 10 tokens = 30 tokens
    assert final["state"]["cost_tracking"]["actual"]["copy_tokens_used"] == 30


def test_scheduler_stops_on_first_failure(fake_db, monkeypatch):
    """第 2 项失败 → 父任务 error,第 3 项仍 pending,调度器退出。"""
    plan = [_mk_pending(0), _mk_pending(1), _mk_pending(2)]
    tid = _prepare_running_task(fake_db, monkeypatch, plan)

    from appcore import bulk_translate_runtime as mod
    calls = []

    def fake_dispatch(parent_id, item, parent_state, bus=None):
        calls.append(item["idx"])
        if item["idx"] == 1:
            return mod.SubTaskResult(
                sub_task_id="sub_1", status="error",
                error="LLM timeout",
            )
        return mod.SubTaskResult(
            sub_task_id=f"sub_{item['idx']}", status="done",
            tokens_used=10,
        )

    monkeypatch.setattr(mod, "_dispatch_sub_task", fake_dispatch)
    monkeypatch.setattr(mod, "_translation_exists_for_item", lambda item: False)

    mod.run_scheduler(tid)

    final = mod.get_task(tid)
    assert final["status"] == "error"
    assert calls == [0, 1]        # 第 3 项未派发
    assert final["state"]["plan"][0]["status"] == "done"
    assert final["state"]["plan"][1]["status"] == "error"
    assert final["state"]["plan"][1]["error"] == "LLM timeout"
    assert final["state"]["plan"][2]["status"] == "pending"  # 铁律 2


def test_scheduler_skips_video_non_de_fr(fake_db, monkeypatch):
    """视频 × es/it 语种 → 自动 skipped,不调 dispatch。"""
    plan = [
        {**_mk_pending(0, kind="video", lang="es"),
         "ref": {"source_raw_id": 1}},
        {**_mk_pending(1, kind="video", lang="de"),
         "ref": {"source_raw_id": 1}},
    ]
    tid = _prepare_running_task(fake_db, monkeypatch, plan)

    from appcore import bulk_translate_runtime as mod
    calls = []

    def fake_dispatch(parent_id, item, parent_state, bus=None):
        calls.append(item["idx"])
        return mod.SubTaskResult(
            sub_task_id="sub_x", status="done",
            video_minutes=1.5,
        )

    monkeypatch.setattr(mod, "_dispatch_sub_task", fake_dispatch)
    monkeypatch.setattr(mod, "_translation_exists_for_item", lambda item: False)

    mod.run_scheduler(tid)

    final = mod.get_task(tid)
    assert final["status"] == "done"
    assert calls == [1]   # 只派发了 de,es 被 skipped
    assert final["state"]["plan"][0]["status"] == "skipped"
    assert final["state"]["plan"][0]["error"] == "video_lang_not_supported"
    assert final["state"]["plan"][1]["status"] == "done"
    # skipped 不计 cost,done 的 1.5 分钟计入
    assert final["state"]["cost_tracking"]["actual"]["video_minutes_processed"] == 1.5


def test_scheduler_skips_existing_when_not_force(fake_db, monkeypatch):
    """已存在译本且 force=False → skipped。"""
    plan = [_mk_pending(0), _mk_pending(1)]
    tid = _prepare_running_task(fake_db, monkeypatch, plan, force=False)

    from appcore import bulk_translate_runtime as mod
    calls = []

    def fake_dispatch(parent_id, item, parent_state, bus=None):
        calls.append(item["idx"])
        return mod.SubTaskResult(sub_task_id="sub_x", status="done",
                                    tokens_used=5)

    # 第一项已存在
    monkeypatch.setattr(mod, "_dispatch_sub_task", fake_dispatch)
    monkeypatch.setattr(
        mod, "_translation_exists_for_item",
        lambda item: item["idx"] == 0,
    )

    mod.run_scheduler(tid)

    final = mod.get_task(tid)
    assert final["state"]["plan"][0]["status"] == "skipped"
    assert final["state"]["plan"][0]["error"] == "already_exists"
    assert final["state"]["plan"][1]["status"] == "done"
    assert calls == [1]


def test_scheduler_force_ignores_existing(fake_db, monkeypatch):
    """force=True 时即使存在也重翻。"""
    plan = [_mk_pending(0)]
    tid = _prepare_running_task(fake_db, monkeypatch, plan, force=True)

    from appcore import bulk_translate_runtime as mod
    calls = []

    def fake_dispatch(parent_id, item, parent_state, bus=None):
        calls.append(item["idx"])
        return mod.SubTaskResult(sub_task_id="x", status="done",
                                    tokens_used=5)

    monkeypatch.setattr(mod, "_dispatch_sub_task", fake_dispatch)
    # 模拟译本已存在
    monkeypatch.setattr(mod, "_translation_exists_for_item", lambda item: True)

    mod.run_scheduler(tid)

    final = mod.get_task(tid)
    assert calls == [0]   # force=True 仍派发
    assert final["state"]["plan"][0]["status"] == "done"


def test_scheduler_cancel_exits(fake_db, monkeypatch):
    """cancel_requested=True 时,下次循环开始就转 cancelled。"""
    plan = [_mk_pending(0), _mk_pending(1)]
    tid = _prepare_running_task(fake_db, monkeypatch, plan)

    # 直接改 state 模拟取消
    from appcore import bulk_translate_runtime as mod
    task = mod.get_task(tid)
    task["state"]["cancel_requested"] = True
    import json as _j
    fake_db.rows[tid]["state_json"] = _j.dumps(task["state"])

    monkeypatch.setattr(mod, "_dispatch_sub_task",
                         lambda *a, **k: (_ for _ in ()).throw(RuntimeError("应不调")))
    monkeypatch.setattr(mod, "_translation_exists_for_item", lambda item: False)

    mod.run_scheduler(tid)

    final = mod.get_task(tid)
    assert final["status"] == "cancelled"


def test_scheduler_emits_events_when_bus_provided(fake_db, monkeypatch):
    """bus 提供时,每步至少发一条事件。"""
    plan = [_mk_pending(0)]
    tid = _prepare_running_task(fake_db, monkeypatch, plan)

    from appcore.events import EventBus, EVT_BT_DONE, EVT_BT_PROGRESS
    from appcore import bulk_translate_runtime as mod

    events = []
    bus = EventBus()
    bus.subscribe(lambda e: events.append(e))

    monkeypatch.setattr(
        mod, "_dispatch_sub_task",
        lambda *a, **kw: mod.SubTaskResult(
            sub_task_id="x", status="done", tokens_used=3,
        ),
    )
    monkeypatch.setattr(mod, "_translation_exists_for_item", lambda item: False)

    mod.run_scheduler(tid, bus=bus)

    types = [e.type for e in events]
    assert EVT_BT_PROGRESS in types
    assert EVT_BT_DONE in types
    # done event payload
    done_evt = next(e for e in events if e.type == EVT_BT_DONE)
    assert done_evt.task_id == tid
    assert done_evt.payload["status"] == "done"
    assert done_evt.payload["progress"]["done"] == 1


def test_scheduler_bus_none_does_not_crash(fake_db, monkeypatch):
    """bus=None 时调度器不发事件且不崩溃。"""
    plan = [_mk_pending(0)]
    tid = _prepare_running_task(fake_db, monkeypatch, plan)

    from appcore import bulk_translate_runtime as mod
    monkeypatch.setattr(
        mod, "_dispatch_sub_task",
        lambda *a, **kw: mod.SubTaskResult(
            sub_task_id="x", status="done", tokens_used=1,
        ),
    )
    monkeypatch.setattr(mod, "_translation_exists_for_item", lambda item: False)

    mod.run_scheduler(tid, bus=None)

    final = mod.get_task(tid)
    assert final["status"] == "done"


# ============================================================
# _translation_exists_for_item 每种 kind 的行为
# ============================================================

def test_translation_exists_for_copy(monkeypatch):
    from appcore import bulk_translate_runtime as mod
    captured = {}

    def fake_query_one(sql, args):
        captured["args"] = args
        return {"x": 1} if "media_copywritings" in sql else None

    monkeypatch.setattr(mod, "query_one", fake_query_one)

    item = {"kind": "copy", "lang": "de",
            "ref": {"source_copy_id": 123}}
    assert mod._translation_exists_for_item(item) is True
    assert captured["args"] == (123, "de")


def test_translation_exists_for_detail_any_match(monkeypatch):
    from appcore import bulk_translate_runtime as mod
    seen = []

    def fake_query_one(sql, args):
        seen.append(args)
        # 只有第 2 个 id 返回命中
        return {"x": 1} if args[0] == 102 else None

    monkeypatch.setattr(mod, "query_one", fake_query_one)

    item = {"kind": "detail", "lang": "de",
            "ref": {"source_detail_ids": [101, 102, 103]}}
    assert mod._translation_exists_for_item(item) is True
    # 找到 102 就短路,不必查 103
    assert [a[0] for a in seen] == [101, 102]


def test_translation_exists_for_video(monkeypatch):
    from appcore import bulk_translate_runtime as mod
    monkeypatch.setattr(mod, "query_one", lambda sql, args: None)

    item = {"kind": "video", "lang": "de",
            "ref": {"source_raw_id": 7}}
    assert mod._translation_exists_for_item(item) is False


def test_translation_exists_for_video_uses_source_raw_id(monkeypatch):
    from appcore import bulk_translate_runtime as mod

    seen = {}

    def fake_exists(table, **conds):
        seen["table"] = table
        seen["conds"] = conds
        return True

    monkeypatch.setattr(mod, "_exists_one", fake_exists)

    item = {"kind": "video", "lang": "de", "ref": {"source_raw_id": 17}}
    assert mod._translation_exists_for_item(item) is True
    assert seen["table"] == "media_items"
    assert seen["conds"] == {"source_raw_id": 17, "lang": "de"}


def test_dispatch_video_from_raw_source_creates_media_item_and_updates_source_raw_id(tmp_path, monkeypatch):
    from appcore import bulk_translate_runtime as mod

    raw_video = tmp_path / "raw.mp4"
    raw_video.write_bytes(b"raw-video")

    monkeypatch.setattr(
        mod.medias,
        "get_raw_source",
        lambda rid: {
            "id": rid,
            "product_id": 77,
            "user_id": 1,
            "video_object_key": "1/medias/77/raw_sources/src.mp4",
            "cover_object_key": "1/medias/77/raw_sources/src.cover.jpg",
            "duration_seconds": 90.0,
        },
    )
    monkeypatch.setattr(
        mod,
        "_download_media_to_tmp",
        lambda object_key, suffix=".mp4": str(raw_video),
        raising=False,
    )
    monkeypatch.setattr(
        mod,
        "_translate_video_to_media_key",
        lambda local_video, target_lang, product_id, user_id, parent_state: "1/medias/77/de_out.mp4",
        raising=False,
    )
    monkeypatch.setattr(
        mod,
        "_translate_cover_to_media_key",
        lambda source_cover_key, target_lang, product_id, user_id: "1/medias/77/de_cover.cover.jpg",
        raising=False,
    )

    created_items = []
    monkeypatch.setattr(
        mod.medias,
        "create_item",
        lambda **kwargs: created_items.append(kwargs) or 901,
    )

    executed = []
    monkeypatch.setattr(
        mod,
        "execute",
        lambda sql, args=None: executed.append((sql, args)) or 1,
    )

    result = mod._dispatch_video(
        "parent-task",
        1,
        77,
        "de",
        {"ref": {"source_raw_id": 321}},
        {"initiator": {"user_id": 1}, "video_params_snapshot": {}},
    )

    assert result.status == "done"
    assert created_items == [{
        "product_id": 77,
        "user_id": 1,
        "filename": "de_out.mp4",
        "object_key": "1/medias/77/de_out.mp4",
        "cover_object_key": "1/medias/77/de_cover.cover.jpg",
        "duration_seconds": 90.0,
        "file_size": None,
        "lang": "de",
    }]
    assert executed
    assert executed[0][1] == (321, 901)
    assert result.video_minutes == pytest.approx(1.5)


def test_download_media_to_tmp_prefers_local_media_store(tmp_path, monkeypatch):
    from appcore import bulk_translate_runtime as mod

    source_file = tmp_path / "source.mp4"
    source_file.write_bytes(b"local-media")
    downloaded = []

    monkeypatch.setattr(mod.local_media_storage, "exists", lambda object_key: object_key == "demo.mp4")

    def fake_download_to(object_key, destination):
        downloaded.append((object_key, destination))
        with open(destination, "wb") as fh:
            fh.write(source_file.read_bytes())
        return str(destination)

    monkeypatch.setattr(mod.local_media_storage, "download_to", fake_download_to)

    target = mod._download_media_to_tmp("demo.mp4", suffix=".mp4")

    with open(target, "rb") as fh:
        assert fh.read() == b"local-media"
    assert downloaded and downloaded[0][0] == "demo.mp4"


def test_translate_video_to_media_key_writes_local_media_store(tmp_path, monkeypatch):
    from appcore import bulk_translate_runtime as mod
    import appcore.task_state as task_state
    import appcore.tos_clients as tos_clients
    import appcore.runtime_v2 as runtime_v2

    local_video = tmp_path / "source.mp4"
    local_video.write_bytes(b"raw")
    result_video = tmp_path / "translated.mp4"
    result_video.write_bytes(b"translated-video")
    created = {}
    writes = []

    monkeypatch.setattr(
        task_state,
        "create_translate_lab",
        lambda task_id, source_file, task_dir, **kwargs: created.update(
            {"task_id": task_id, "source_file": source_file, "task_dir": task_dir, **kwargs}
        ),
    )

    class DummyRunner:
        def __init__(self, bus=None, user_id=None):
            self.bus = bus
            self.user_id = user_id

        def start(self, task_id):
            created["started_task_id"] = task_id

    monkeypatch.setattr(runtime_v2, "PipelineRunnerV2", DummyRunner)
    monkeypatch.setattr(mod, "_resolve_translated_video_path", lambda task: str(result_video))
    monkeypatch.setattr(task_state, "get", lambda task_id: {"id": task_id})
    monkeypatch.setattr(
        tos_clients,
        "build_media_object_key",
        lambda user_id, product_id, filename: f"{user_id}/medias/{product_id}/{filename}",
    )
    monkeypatch.setattr(
        mod.local_media_storage,
        "write_bytes",
        lambda object_key, payload: writes.append((object_key, bytes(payload))),
    )

    sub_id, object_key = mod._translate_video_to_media_key(
        str(local_video),
        "de",
        77,
        1,
        {"video_params_snapshot": {}},
    )

    assert sub_id
    assert object_key == "1/medias/77/de_source.mp4"
    assert writes == [("1/medias/77/de_source.mp4", b"translated-video")]


def test_translate_cover_to_media_key_writes_local_media_store(tmp_path, monkeypatch):
    from appcore import bulk_translate_runtime as mod
    import appcore.task_state as task_state
    import appcore.tos_clients as tos_clients
    import appcore.image_translate_runtime as image_translate_runtime
    import appcore.image_translate_settings as image_translate_settings

    translated_cover = tmp_path / "translated.png"
    translated_cover.write_bytes(b"cover-payload")
    writes = []

    monkeypatch.setattr(mod.medias, "get_language_name", lambda lang: "德语")
    monkeypatch.setattr(image_translate_settings, "get_prompt", lambda preset, lang: "translate")
    monkeypatch.setattr(
        task_state,
        "create_image_translate",
        lambda task_id, task_dir, **kwargs: {"id": task_id},
    )

    class DummyImageRuntime:
        def __init__(self, bus=None, user_id=None):
            self.bus = bus
            self.user_id = user_id

        def start(self, task_id):
            return None

    monkeypatch.setattr(image_translate_runtime, "ImageTranslateRuntime", DummyImageRuntime)
    monkeypatch.setattr(
        task_state,
        "get",
        lambda task_id: {
            "items": [{"status": "done", "dst_tos_key": "projects/demo/result.png"}],
        },
    )

    def fake_download_file(src, dest):
        with open(dest, "wb") as fh:
            fh.write(translated_cover.read_bytes())
        return str(dest)

    monkeypatch.setattr(tos_clients, "download_file", fake_download_file)
    monkeypatch.setattr(
        tos_clients,
        "build_media_object_key",
        lambda user_id, product_id, filename: f"{user_id}/medias/{product_id}/{filename}",
    )
    monkeypatch.setattr(
        mod.local_media_storage,
        "write_bytes",
        lambda object_key, payload: writes.append((object_key, bytes(payload))),
    )

    object_key = mod._translate_cover_to_media_key("1/medias/77/raw_sources/cover_demo.png", "de", 77, 1)

    assert object_key == "1/medias/77/cover_de_cover_demo.png"
    assert writes == [("1/medias/77/cover_de_cover_demo.png", b"cover-payload")]


# ============================================================
# Task 20:人工恢复三路径
# ============================================================

def _mk_plan_with(statuses):
    return [
        {"idx": i, "kind": "copy", "lang": "de",
         "ref": {"source_copy_id": 100 + i},
         "sub_task_id": (f"sub_{i}" if s != "pending" else None),
         "status": s, "error": ("old err" if s == "error" else None),
         "started_at": None, "finished_at": None}
        for i, s in enumerate(statuses)
    ]


def _create_task_with(fake_db, monkeypatch, plan, initial_status="error"):
    """直接在 fake DB 里植入一个指定 plan 的父任务。"""
    from appcore import bulk_translate_runtime as mod
    monkeypatch.setattr(mod, "generate_plan", lambda *a, **kw: plan)
    tid = mod.create_bulk_translate_task(
        user_id=1, product_id=77, target_langs=["de"],
        content_types=["copy"], force_retranslate=False,
        video_params={},
        initiator={"user_id": 1, "user_name": "", "ip": "", "user_agent": ""},
    )
    # 直接改 fake DB 的 status 绕过 state machine 校验
    fake_db.rows[tid]["status"] = initial_status
    return tid


def test_pause_task_sets_status_paused(fake_db, monkeypatch):
    plan = _mk_plan_with(["done", "running", "pending"])
    tid = _create_task_with(fake_db, monkeypatch, plan,
                             initial_status="running")

    from appcore.bulk_translate_runtime import pause_task, get_task
    pause_task(tid, user_id=1)

    task = get_task(tid)
    assert task["status"] == "paused"
    actions = [e["action"] for e in task["state"]["audit_events"]]
    assert "pause" in actions


def test_cancel_task_sets_cancel_requested_flag(fake_db, monkeypatch):
    plan = _mk_plan_with(["done", "running", "pending"])
    tid = _create_task_with(fake_db, monkeypatch, plan,
                             initial_status="running")

    from appcore.bulk_translate_runtime import cancel_task, get_task
    cancel_task(tid, user_id=1)

    task = get_task(tid)
    assert task["state"]["cancel_requested"] is True
    actions = [e["action"] for e in task["state"]["audit_events"]]
    assert "cancel" in actions


def test_resume_reconciles_running_and_keeps_error(fake_db, monkeypatch):
    """resume 对账:running→error 强制转;已有 error 保持 error。pending 不动。"""
    plan = _mk_plan_with(["done", "running", "error", "pending"])
    tid = _create_task_with(fake_db, monkeypatch, plan,
                             initial_status="error")

    from appcore.bulk_translate_runtime import resume_task, get_task
    resume_task(tid, user_id=9)

    task = get_task(tid)
    assert task["status"] == "running"

    statuses = [p["status"] for p in task["state"]["plan"]]
    assert statuses[0] == "done"
    assert statuses[1] == "error"      # running 对账 → error
    assert statuses[2] == "error"      # 已 error 保持
    assert statuses[3] == "pending"    # 不动

    # running 项的 error 字段被标上 "Reconciled: process lost"
    assert "Reconciled" in task["state"]["plan"][1]["error"]

    # 审计事件
    actions = [e["action"] for e in task["state"]["audit_events"]]
    assert "resume" in actions


def test_retry_failed_items_resets_all_errors(fake_db, monkeypatch):
    plan = _mk_plan_with(["done", "error", "error", "pending"])
    tid = _create_task_with(fake_db, monkeypatch, plan,
                             initial_status="error")

    from appcore.bulk_translate_runtime import retry_failed_items, get_task
    retry_failed_items(tid, user_id=9)

    task = get_task(tid)
    assert task["status"] == "running"

    statuses = [p["status"] for p in task["state"]["plan"]]
    assert statuses == ["done", "pending", "pending", "pending"]

    # 审计带 reset_count
    audit = [e for e in task["state"]["audit_events"]
              if e["action"] == "retry_failed"][0]
    assert audit["detail"]["reset_count"] == 2

    # error 字段被清
    for p in task["state"]["plan"][1:3]:
        assert p["error"] is None


def test_retry_item_single(fake_db, monkeypatch):
    """单项重跑 — idx=2 从 done → pending,其他不动。"""
    plan = _mk_plan_with(["done", "done", "done"])
    tid = _create_task_with(fake_db, monkeypatch, plan,
                             initial_status="done")

    from appcore.bulk_translate_runtime import retry_item, get_task
    retry_item(tid, idx=2, user_id=9)

    task = get_task(tid)
    assert task["status"] == "running"   # 父任务从 done 回到 running

    statuses = [p["status"] for p in task["state"]["plan"]]
    assert statuses == ["done", "done", "pending"]

    audit = [e for e in task["state"]["audit_events"]
              if e["action"] == "retry_item"][0]
    assert audit["detail"]["idx"] == 2


def test_retry_item_invalid_idx_raises(fake_db, monkeypatch):
    plan = _mk_plan_with(["done", "done"])
    tid = _create_task_with(fake_db, monkeypatch, plan,
                             initial_status="done")

    from appcore.bulk_translate_runtime import retry_item
    with pytest.raises(ValueError, match="Invalid idx"):
        retry_item(tid, idx=99, user_id=1)


def test_resume_clears_cancel_requested_flag(fake_db, monkeypatch):
    """用户曾取消然后又点 resume,应清掉 cancel_requested。"""
    plan = _mk_plan_with(["pending", "pending"])
    tid = _create_task_with(fake_db, monkeypatch, plan,
                             initial_status="error")

    from appcore.bulk_translate_runtime import get_task, resume_task
    task = get_task(tid)
    task["state"]["cancel_requested"] = True
    import json as _j
    fake_db.rows[tid]["state_json"] = _j.dumps(task["state"])

    resume_task(tid, user_id=9)

    task2 = get_task(tid)
    assert task2["state"]["cancel_requested"] is False


# ============================================================
# Task 21 铁律验证:绝不自动扫描/恢复
# ============================================================

def test_import_runtime_does_not_run_any_dispatch():
    """导入模块时不应执行任何 DB 扫描 / dispatch / scheduler。"""
    # 如果导入阶段触发了全表扫描,会连不上 DB 且崩溃。
    # 这里直接 reload,验证无副作用。
    import importlib
    import appcore.bulk_translate_runtime as mod
    importlib.reload(mod)
    # 没异常即 pass
    assert hasattr(mod, "run_scheduler")
    assert hasattr(mod, "resume_task")


def test_task_recovery_module_does_not_include_bulk_translate():
    """task_recovery.py 中不得包含 bulk_translate 自动恢复逻辑(铁律 1)。"""
    import appcore.task_recovery as tr
    with open(tr.__file__, encoding="utf-8") as f:
        source = f.read()
    # 允许单纯注释里写 "NO_AUTO: bulk_translate" 这种锁定标记,
    # 但不能出现会被 call 的代码里提到 bulk_translate。
    lines = source.splitlines()
    code_lines = [
        line for line in lines
        if "bulk_translate" in line
        and not line.strip().startswith("#")
        and "NO_AUTO" not in line
    ]
    assert not code_lines, (
        "task_recovery.py 不得包含 bulk_translate 自动恢复逻辑:\n"
        + "\n".join(code_lines)
    )


def test_no_module_level_scan_in_bulk_translate_runtime():
    """bulk_translate_runtime 模块顶层不能调 execute/query。"""
    import appcore.bulk_translate_runtime as mod
    with open(mod.__file__, encoding="utf-8") as f:
        source = f.read()

    # 查找模块顶层(零缩进)出现 execute(...) 或 query(...) 的行
    suspicious = []
    for i, line in enumerate(source.splitlines(), 1):
        if line.startswith(("execute(", "query(", "query_one(",
                             "run_scheduler(", "resume_task(")):
            suspicious.append(f"line {i}: {line}")
    assert not suspicious, (
        "bulk_translate_runtime.py 顶层不得调用 DB/调度函数:\n"
        + "\n".join(suspicious)
    )
