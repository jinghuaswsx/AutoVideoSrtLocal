from pathlib import Path
import types
import sys
import json

from pipeline.capcut import _probe_media_duration, export_capcut_project


def test_capcut_export_creates_project_directory_and_archive(tmp_path):
    video = tmp_path / "sample.mp4"
    audio = tmp_path / "sample.mp3"
    srt = tmp_path / "sample.srt"
    video.write_bytes(b"video")
    audio.write_bytes(b"audio")
    srt.write_text("1\n00:00:00,000 --> 00:00:01,000\nHello\n", encoding="utf-8")

    export = export_capcut_project(
        video_path=str(video),
        tts_audio_path=str(audio),
        srt_path=str(srt),
        timeline_manifest={"segments": []},
        output_dir=str(tmp_path / "output"),
        subtitle_position="bottom",
    )

    assert Path(export["project_dir"]).exists()
    assert Path(export["archive_path"]).exists()
    assert Path(export["manifest_path"]).exists()
    manifest = json.loads(Path(export["manifest_path"]).read_text(encoding="utf-8"))
    assert manifest["backend"] in {"pyJianYingDraft", "template_scaffold"}


def test_capcut_export_prefers_pyjianyingdraft_backend_when_available(tmp_path, monkeypatch):
    calls = []

    class FakeScript:
        def add_track(self, track_type, name=None, **kwargs):
            calls.append(("add_track", track_type, name, kwargs))
            return self

        def add_segment(self, segment, **kwargs):
            calls.append(("add_segment", getattr(segment, "kind", type(segment).__name__), kwargs))
            return self

        def import_srt(self, path, **kwargs):
            calls.append(("import_srt", Path(path).name, kwargs))
            return self

        def save(self):
            calls.append(("save",))

    class FakeDraftFolder:
        def __init__(self, root):
            self.root = Path(root)
            self.root.mkdir(parents=True, exist_ok=True)
            calls.append(("DraftFolder", str(self.root)))

        def create_draft(self, name, width, height, allow_replace=True):
            calls.append(("create_draft", name, width, height, allow_replace))
            draft_dir = self.root / name
            draft_dir.mkdir(parents=True, exist_ok=True)
            return FakeScript()

    class FakeSegment:
        def __init__(self, *args, **kwargs):
            self.kind = self.__class__.__name__
            calls.append((self.kind, args, kwargs))

    fake_module = types.ModuleType("pyJianYingDraft")
    fake_module.DraftFolder = FakeDraftFolder
    fake_module.TrackType = types.SimpleNamespace(audio="audio", video="video", text="text")
    fake_module.AudioSegment = type("AudioSegment", (FakeSegment,), {})
    fake_module.VideoSegment = type("VideoSegment", (FakeSegment,), {})
    fake_module.TextStyle = lambda **kwargs: ("TextStyle", kwargs)
    fake_module.ClipSettings = lambda **kwargs: ("ClipSettings", kwargs)
    fake_module.trange = lambda start, duration: ("trange", start, duration)

    monkeypatch.setitem(sys.modules, "pyJianYingDraft", fake_module)
    monkeypatch.setattr("pipeline.capcut.time.strftime", lambda fmt: "26-03-31-18-20-58")

    video = tmp_path / "sample.mp4"
    audio = tmp_path / "sample.mp3"
    srt = tmp_path / "sample.srt"
    video.write_bytes(b"video")
    audio.write_bytes(b"audio")
    srt.write_text("1\n00:00:00,000 --> 00:00:01,000\nHello\n", encoding="utf-8")

    export = export_capcut_project(
        video_path=str(video),
        tts_audio_path=str(audio),
        srt_path=str(srt),
        timeline_manifest={
            "segments": [
                {
                    "timeline_start": 0.0,
                    "timeline_end": 1.0,
                    "translated": "Hello",
                }
            ]
        },
        output_dir=str(tmp_path / "output"),
        subtitle_position="top",
    )

    assert ("create_draft", "sample_26-03-31-18-20-58", 1080, 1920, True) in calls
    assert any(item[0] == "import_srt" for item in calls)
    assert any(item[0] == "save" for item in calls)
    assert Path(export["project_dir"]).exists()
    manifest = json.loads(Path(export["manifest_path"]).read_text(encoding="utf-8"))
    assert manifest["backend"] == "pyJianYingDraft"


def test_capcut_export_uses_video_filename_and_timestamp_for_draft_name(tmp_path, monkeypatch):
    fake_module = types.ModuleType("pyJianYingDraft")
    calls = []

    class FakeScript:
        def add_track(self, *args, **kwargs):
            return self

        def add_segment(self, *args, **kwargs):
            return self

        def import_srt(self, *args, **kwargs):
            return self

        def save(self):
            calls.append(("save",))

    class FakeDraftFolder:
        def __init__(self, root):
            self.root = Path(root)
            self.root.mkdir(parents=True, exist_ok=True)

        def create_draft(self, name, width, height, allow_replace=True):
            calls.append(("create_draft", name))
            draft_dir = self.root / name
            draft_dir.mkdir(parents=True, exist_ok=True)
            return FakeScript()

    class FakeSegment:
        def __init__(self, *args, **kwargs):
            pass

    fake_module.DraftFolder = FakeDraftFolder
    fake_module.TrackType = types.SimpleNamespace(audio="audio", video="video", text="text")
    fake_module.AudioSegment = type("AudioSegment", (FakeSegment,), {})
    fake_module.VideoSegment = type("VideoSegment", (FakeSegment,), {})
    fake_module.TextStyle = lambda **kwargs: ("TextStyle", kwargs)
    fake_module.ClipSettings = lambda **kwargs: ("ClipSettings", kwargs)
    fake_module.trange = lambda start, duration: ("trange", start, duration)

    monkeypatch.setitem(sys.modules, "pyJianYingDraft", fake_module)
    monkeypatch.setattr("pipeline.capcut.time.strftime", lambda fmt: "26-03-31-18-20-59")

    video = tmp_path / "my_video.mp4"
    audio = tmp_path / "sample.mp3"
    srt = tmp_path / "sample.srt"
    video.write_bytes(b"video")
    audio.write_bytes(b"audio")
    srt.write_text("1\n00:00:00,000 --> 00:00:01,000\nHello\n", encoding="utf-8")

    export = export_capcut_project(
        video_path=str(video),
        tts_audio_path=str(audio),
        srt_path=str(srt),
        timeline_manifest={"segments": []},
        output_dir=str(tmp_path / "output"),
        subtitle_position="bottom",
    )

    assert ("create_draft", "my_video_26-03-31-18-20-59") in calls
    assert Path(export["project_dir"]).name == "my_video_26-03-31-18-20-59"
    assert Path(export["archive_path"]).name == "my_video_26-03-31-18-20-59.zip"


def test_capcut_export_prefers_explicit_draft_title_over_storage_filename(tmp_path, monkeypatch):
    fake_module = types.ModuleType("pyJianYingDraft")
    calls = []

    class FakeScript:
        def add_track(self, *args, **kwargs):
            return self

        def add_segment(self, *args, **kwargs):
            return self

        def import_srt(self, *args, **kwargs):
            return self

        def save(self):
            calls.append(("save",))

    class FakeDraftFolder:
        def __init__(self, root):
            self.root = Path(root)
            self.root.mkdir(parents=True, exist_ok=True)

        def create_draft(self, name, width, height, allow_replace=True):
            calls.append(("create_draft", name))
            draft_dir = self.root / name
            draft_dir.mkdir(parents=True, exist_ok=True)
            return FakeScript()

    class FakeSegment:
        def __init__(self, *args, **kwargs):
            pass

    fake_module.DraftFolder = FakeDraftFolder
    fake_module.TrackType = types.SimpleNamespace(audio="audio", video="video", text="text")
    fake_module.AudioSegment = type("AudioSegment", (FakeSegment,), {})
    fake_module.VideoSegment = type("VideoSegment", (FakeSegment,), {})
    fake_module.TextStyle = lambda **kwargs: ("TextStyle", kwargs)
    fake_module.ClipSettings = lambda **kwargs: ("ClipSettings", kwargs)
    fake_module.trange = lambda start, duration: ("trange", start, duration)

    monkeypatch.setitem(sys.modules, "pyJianYingDraft", fake_module)
    monkeypatch.setattr("pipeline.capcut.time.strftime", lambda fmt: "26-03-31-18-21-30")

    video = tmp_path / "97d95d95.mp4"
    audio = tmp_path / "sample.mp3"
    srt = tmp_path / "sample.srt"
    video.write_bytes(b"video")
    audio.write_bytes(b"audio")
    srt.write_text("1\n00:00:00,000 --> 00:00:01,000\nHello\n", encoding="utf-8")

    export = export_capcut_project(
        video_path=str(video),
        tts_audio_path=str(audio),
        srt_path=str(srt),
        timeline_manifest={"segments": []},
        output_dir=str(tmp_path / "output"),
        subtitle_position="bottom",
        draft_title="我的视频.mp4",
    )

    assert ("create_draft", "我的视频_26-03-31-18-21-30") in calls
    assert Path(export["project_dir"]).name == "我的视频_26-03-31-18-21-30"


def test_capcut_export_clamps_source_ranges_before_pyjianyingdraft(tmp_path, monkeypatch):
    calls = []

    class FakeScript:
        def add_track(self, track_type, name=None, **kwargs):
            return self

        def add_segment(self, segment, **kwargs):
            calls.append(("add_segment", getattr(segment, "kind", type(segment).__name__), kwargs))
            return self

        def import_srt(self, path, **kwargs):
            return self

        def save(self):
            calls.append(("save",))

    class FakeDraftFolder:
        def __init__(self, root):
            self.root = Path(root)
            self.root.mkdir(parents=True, exist_ok=True)

        def create_draft(self, name, width, height, allow_replace=True):
            draft_dir = self.root / name
            draft_dir.mkdir(parents=True, exist_ok=True)
            return FakeScript()

    class FakeAudioSegment:
        kind = "AudioSegment"

        def __init__(self, *args, **kwargs):
            pass

    class FakeVideoSegment:
        kind = "VideoSegment"

        def __init__(self, path, timerange, source_timerange):
            source_start = float(str(source_timerange[1]).rstrip("s"))
            source_duration = float(str(source_timerange[2]).rstrip("s"))
            if source_start + source_duration > 29.567:
                raise RuntimeError("source range exceeds media duration")
            calls.append(("VideoSegment", path, timerange, source_timerange))

    fake_module = types.ModuleType("pyJianYingDraft")
    fake_module.DraftFolder = FakeDraftFolder
    fake_module.TrackType = types.SimpleNamespace(audio="audio", video="video", text="text")
    fake_module.AudioSegment = FakeAudioSegment
    fake_module.VideoSegment = FakeVideoSegment
    fake_module.TextStyle = lambda **kwargs: ("TextStyle", kwargs)
    fake_module.ClipSettings = lambda **kwargs: ("ClipSettings", kwargs)
    fake_module.trange = lambda start, duration: ("trange", start, duration)

    monkeypatch.setitem(sys.modules, "pyJianYingDraft", fake_module)
    monkeypatch.setattr("pipeline.capcut._probe_media_duration", lambda path: 29.567)
    monkeypatch.setattr("pipeline.capcut.time.strftime", lambda fmt: "26-03-31-18-21-00")

    video = tmp_path / "sample.mp4"
    audio = tmp_path / "sample.mp3"
    srt = tmp_path / "sample.srt"
    video.write_bytes(b"video")
    audio.write_bytes(b"audio")
    srt.write_text("1\n00:00:00,000 --> 00:00:01,000\nHello\n", encoding="utf-8")

    export = export_capcut_project(
        video_path=str(video),
        tts_audio_path=str(audio),
        srt_path=str(srt),
        timeline_manifest={
            "segments": [
                {
                    "timeline_start": 26.358,
                    "video_ranges": [{"start": 26.358, "end": 29.606}],
                }
            ],
            "total_tts_duration": 31.033,
        },
        output_dir=str(tmp_path / "output"),
        subtitle_position="bottom",
    )

    manifest = json.loads(Path(export["manifest_path"]).read_text(encoding="utf-8"))
    assert manifest["backend"] == "pyJianYingDraft"
    assert "fallback_reason" not in manifest
    last_video_segment = [item for item in calls if item[0] == "VideoSegment"][-1]
    assert last_video_segment[3] == ("trange", "26.358s", "3.209s")


def test_probe_media_duration_uses_pymediainfo_milliseconds(monkeypatch):
    fake_module = types.ModuleType("pymediainfo")

    class FakeTrack:
        duration = 29567

    class FakeInfo:
        video_tracks = [FakeTrack()]
        audio_tracks = []

    class FakeMediaInfo:
        @staticmethod
        def parse(path):
            return FakeInfo()

    fake_module.MediaInfo = FakeMediaInfo
    monkeypatch.setitem(sys.modules, "pymediainfo", fake_module)

    assert _probe_media_duration(Path("sample.mp4")) == 29.567
