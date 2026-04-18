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
