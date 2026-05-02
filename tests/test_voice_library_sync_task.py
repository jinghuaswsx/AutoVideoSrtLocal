"""
`appcore.voice_library_sync_task` 单元测试。

Mock 风格参考 `tests/test_voice_library_browse.py`：直接 patch
`appcore.voice_library_sync_task.query` / `appcore.medias.list_enabled_languages_kv`
来模拟 DB 与语种配置。
"""
from __future__ import annotations

import pytest
from unittest.mock import MagicMock, patch

from appcore import voice_library_sync_task as vlst


@pytest.fixture(autouse=True)
def reset():
    from appcore import active_tasks

    vlst._CURRENT["task"] = None
    vlst._CURRENT["summary"] = {}
    active_tasks.clear_active_tasks_for_tests()
    yield
    vlst._CURRENT["task"] = None
    vlst._CURRENT["summary"] = {}
    active_tasks.clear_active_tasks_for_tests()


class _FakeThread:
    """占位 Thread：不真正启动，避免测试调用到真实 pipeline。"""

    def __init__(self, *args, **kwargs):
        self.args = args
        self.kwargs = kwargs

    def start(self):
        pass


def test_start_when_idle_registers_active_task(monkeypatch):
    from appcore import active_tasks

    emit = MagicMock()
    monkeypatch.setattr(vlst, "_emit", emit)
    monkeypatch.setattr(vlst, "_get_api_key", lambda: "k")
    monkeypatch.setattr(vlst.threading, "Thread", _FakeThread)

    tid = vlst.start_sync(language="de")

    assert tid is not None
    assert tid.startswith("sync_")
    cur = vlst.get_current()
    assert cur is not None
    assert cur["language"] == "de"
    assert cur["status"] == "running"
    assert cur["phase"] == "pull_metadata"
    assert cur["sync_id"] == tid
    assert active_tasks.is_active("voice_library_sync", "global") is True
    task = active_tasks.list_active_tasks()[0]
    assert task.project_type == "voice_library_sync"
    assert task.task_id == "global"
    assert task.runner == "appcore.voice_library_sync_task._run_sync_sync"
    assert task.entrypoint == "voice_library_sync.start"
    assert task.stage == "pull_metadata"
    assert task.details["sync_id"] == tid
    assert task.details["language"] == "de"
    assert task.details["daemon"] is True


def test_emit_uses_registered_admin_realtime_emitter():
    from appcore import realtime_events

    emitted = []
    realtime_events.register_admin_emitter(
        lambda event, payload: emitted.append((event, payload))
    )
    try:
        vlst._emit("voice_library.sync.progress", {"done": 1})
    finally:
        realtime_events.clear_admin_emitter()

    assert emitted == [("voice_library.sync.progress", {"done": 1})]


def test_start_raises_when_busy(monkeypatch):
    monkeypatch.setattr(
        vlst,
        "_get_api_key",
        lambda: (_ for _ in ()).throw(AssertionError("api key lookup skipped when busy")),
    )
    vlst._CURRENT["task"] = {
        "sync_id": "x",
        "language": "de",
        "status": "running",
    }
    with pytest.raises(RuntimeError, match="another sync"):
        vlst.start_sync(language="fr")


def test_start_raises_when_global_active_task_exists(monkeypatch):
    from appcore import task_recovery

    started = []

    class FakeThread(_FakeThread):
        def start(self):
            started.append(self)

    monkeypatch.setattr(vlst, "_get_api_key", lambda: "k")
    monkeypatch.setattr(vlst.threading, "Thread", FakeThread)
    task_recovery.register_active_task("voice_library_sync", "global")

    with pytest.raises(RuntimeError, match="another sync"):
        vlst.start_sync(language="de")

    assert started == []
    assert vlst.get_current() is None


def test_start_does_not_mark_running_when_api_key_missing(monkeypatch):
    from appcore import active_tasks

    monkeypatch.setattr(
        vlst,
        "_get_api_key",
        lambda: (_ for _ in ()).throw(RuntimeError("api key missing")),
    )

    with pytest.raises(RuntimeError, match="api key missing"):
        vlst.start_sync(language="de")

    assert vlst.get_current() is None
    assert active_tasks.list_active_tasks() == []


def test_get_current_returns_none_when_idle():
    assert vlst.get_current() is None


def test_summary_counts_from_db():
    """summarize() 应合并 DB 统计与启用语种列表；未在 DB 出现的语种补 0。"""
    fake_rows = [
        {
            "language": "de",
            "total_rows": 2,
            "embedded_rows": 1,
            "last_synced_at": None,
        }
    ]
    with patch(
        "appcore.voice_library_sync_task.query",
        return_value=fake_rows,
    ), patch(
        "appcore.medias.list_enabled_languages_kv",
        return_value=[("de", "德语"), ("fr", "法语")],
    ):
        s = vlst.summarize()

    # de：来自 DB 的统计
    de = next(x for x in s if x["language"] == "de")
    assert de["total_rows"] == 2
    assert de["embedded_rows"] == 1
    assert de["name_zh"] == "德语"
    assert de["last_synced_at"] is None

    # fr：不在 DB 中 -> 计数为 0
    fr = next(x for x in s if x["language"] == "fr")
    assert fr["total_rows"] == 0
    assert fr["embedded_rows"] == 0
    assert fr["name_zh"] == "法语"
    assert fr["last_synced_at"] is None


def test_summarize_includes_total_available(monkeypatch):
    """summarize 应联表 elevenlabs_voice_library_stats 拿 total_available。"""
    from appcore import voice_library_sync_task as vlst
    voices_rows = [
        {"language": "en", "total_rows": 100, "embedded_rows": 14,
         "last_synced_at": None}
    ]
    stats_rows = [
        {"language": "en", "total_available": 6308, "last_counted_at": None}
    ]

    def fake_query(sql, *args):
        if "elevenlabs_voice_library_stats" in sql:
            return stats_rows
        return voices_rows

    monkeypatch.setattr(vlst, "query", fake_query)
    monkeypatch.setattr(
        "appcore.medias.list_enabled_languages_kv",
        lambda: [("en", "英语")],
    )
    out = vlst.summarize()
    assert out[0]["language"] == "en"
    assert out[0]["total_available"] == 6308
    assert out[0]["target_total"] == 1000
    assert out[0]["total_rows"] == 100


def test_summarize_prefers_voice_variants_when_available(monkeypatch):
    from appcore import voice_library_sync_task as vlst

    def fake_query(sql, *args):
        if "elevenlabs_voice_variants" in sql:
            return [
                {"language": "nl", "total_rows": 521, "embedded_rows": 521,
                 "last_synced_at": None}
            ]
        if "elevenlabs_voice_library_stats" in sql:
            return [{"language": "nl", "total_available": 521, "last_counted_at": None}]
        return [
            {"language": "nl", "total_rows": 1, "embedded_rows": 0,
             "last_synced_at": None}
        ]

    monkeypatch.setattr(vlst, "query", fake_query)
    monkeypatch.setattr(
        "appcore.medias.list_enabled_languages_kv",
        lambda: [("nl", "荷兰语")],
    )

    out = vlst.summarize()

    assert out[0]["total_rows"] == 521
    assert out[0]["embedded_rows"] == 521
    assert out[0]["total_available"] == 521
    assert out[0]["target_total"] == 521


def test_max_voices_per_language_constant():
    from appcore.voice_library_sync_task import MAX_VOICES_PER_LANGUAGE
    assert MAX_VOICES_PER_LANGUAGE == 1000
