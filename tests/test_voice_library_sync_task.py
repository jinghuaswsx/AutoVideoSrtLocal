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
    vlst._CURRENT["task"] = None
    vlst._CURRENT["summary"] = {}
    yield
    vlst._CURRENT["task"] = None
    vlst._CURRENT["summary"] = {}


class _FakeThread:
    """占位 Thread：不真正启动，避免测试调用到真实 pipeline。"""

    def __init__(self, *args, **kwargs):
        self.args = args
        self.kwargs = kwargs

    def start(self):
        pass


def test_start_when_idle(monkeypatch):
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


def test_start_raises_when_busy():
    vlst._CURRENT["task"] = {
        "sync_id": "x",
        "language": "de",
        "status": "running",
    }
    with pytest.raises(RuntimeError, match="another sync"):
        vlst.start_sync(language="fr")


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


def test_max_voices_per_language_constant():
    from appcore.voice_library_sync_task import MAX_VOICES_PER_LANGUAGE
    assert MAX_VOICES_PER_LANGUAGE == 1000
