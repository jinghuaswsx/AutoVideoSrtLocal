from pathlib import Path, PureWindowsPath
import types
import sys
import json

from appcore.api_keys import DEFAULT_JIANYING_PROJECT_ROOT
from pipeline.capcut import _probe_media_duration, deploy_capcut_project, export_capcut_project


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


def test_capcut_export_names_archives_by_variant(tmp_path, monkeypatch):
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
    monkeypatch.setattr("pipeline.capcut.time.strftime", lambda fmt: "26-03-31-20-01-00")

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
        variant="hook_cta",
    )

    assert ("create_draft", "sample_hook_cta_26-03-31-20-01-00") in calls
    assert Path(export["project_dir"]).name == "sample_hook_cta_26-03-31-20-01-00"
    assert Path(export["archive_path"]).name == "sample_hook_cta_26-03-31-20-01-00.zip"


def test_capcut_export_does_not_auto_copy_into_jianying_project_dir(tmp_path, monkeypatch):
    fake_module = types.ModuleType("pyJianYingDraft")

    class FakeScript:
        def add_track(self, *args, **kwargs):
            return self

        def add_segment(self, *args, **kwargs):
            return self

        def import_srt(self, *args, **kwargs):
            return self

        def save(self):
            return None

    class FakeDraftFolder:
        def __init__(self, root):
            self.root = Path(root)
            self.root.mkdir(parents=True, exist_ok=True)

        def create_draft(self, name, width, height, allow_replace=True):
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
    monkeypatch.setattr("pipeline.capcut.time.strftime", lambda fmt: "26-03-31-20-15-00")
    monkeypatch.setattr("pipeline.capcut.JIANYING_PROJECT_DIR", str(tmp_path / "jianying" / "com.lveditor.draft"))

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

    deployed_dir = Path(tmp_path / "jianying" / "com.lveditor.draft" / "sample_26-03-31-20-15-00")
    assert not deployed_dir.exists()
    assert export["jianying_project_dir"] == str(PureWindowsPath(DEFAULT_JIANYING_PROJECT_ROOT) / "sample_26-03-31-20-15-00")


def test_capcut_export_rewrites_material_paths_to_jianying_root(tmp_path, monkeypatch):
    fake_module = types.ModuleType("pyJianYingDraft")

    class FakeScript:
        def __init__(self, draft_dir):
            self.draft_dir = draft_dir

        def add_track(self, *args, **kwargs):
            return self

        def add_segment(self, *args, **kwargs):
            return self

        def import_srt(self, *args, **kwargs):
            return self

        def save(self):
            resources_dir = self.draft_dir / "Resources" / "auto_generated"
            video_path = resources_dir / "sample.mp4"
            audio_path = resources_dir / "sample.mp3"
            (self.draft_dir / "draft_content.json").write_text(
                json.dumps(
                    {
                        "materials": {
                            "audios": [{"name": "sample.mp3", "path": str(audio_path)}],
                            "videos": [{"path": str(video_path)}],
                        }
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            (self.draft_dir / "draft_meta_info.json").write_text(
                json.dumps({"draft_fold_path": "", "draft_name": ""}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            return None

    class FakeDraftFolder:
        def __init__(self, root):
            self.root = Path(root)
            self.root.mkdir(parents=True, exist_ok=True)

        def create_draft(self, name, width, height, allow_replace=True):
            draft_dir = self.root / name
            draft_dir.mkdir(parents=True, exist_ok=True)
            return FakeScript(draft_dir)

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
    monkeypatch.setattr("pipeline.capcut.time.strftime", lambda fmt: "26-04-01-18-00-00")

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
        timeline_manifest={"segments": [{"tts_path": str(tmp_path / "tts_segments" / "seg_0001.mp3")}]},
        output_dir=str(tmp_path / "output"),
        subtitle_position="bottom",
        variant="hook_cta",
        jianying_project_root=r"D:\JianyingDrafts",
    )

    draft_name = "sample_hook_cta_26-04-01-18-00-00"
    expected_dir = PureWindowsPath(r"D:\JianyingDrafts") / draft_name
    expected_audio = str(expected_dir / "Resources" / "auto_generated" / "sample.mp3")
    expected_video = str(expected_dir / "Resources" / "auto_generated" / "sample.mp4")

    draft_content = json.loads((Path(export["project_dir"]) / "draft_content.json").read_text(encoding="utf-8"))
    assert draft_content["materials"]["audios"][0]["path"] == expected_audio
    assert draft_content["materials"]["videos"][0]["path"] == expected_video

    meta = json.loads((Path(export["project_dir"]) / "draft_meta_info.json").read_text(encoding="utf-8"))
    assert meta["draft_fold_path"] == str(expected_dir)

    manifest = json.loads(Path(export["manifest_path"]).read_text(encoding="utf-8"))
    assert manifest["jianying_project_dir"] == str(expected_dir)
    assert manifest["timeline_manifest"]["segments"][0]["tts_path"] == ""


def test_deploy_capcut_project_copies_project_into_jianying_project_dir(tmp_path, monkeypatch):
    project_dir = tmp_path / "output" / "sample_26-03-31-20-15-00"
    project_dir.mkdir(parents=True)
    (project_dir / "draft_content.json").write_text("{}", encoding="utf-8")
    (project_dir / "codex_export_manifest.json").write_text('{"backend":"pyJianYingDraft"}', encoding="utf-8")
    monkeypatch.setattr("pipeline.capcut.JIANYING_PROJECT_DIR", str(tmp_path / "jianying" / "com.lveditor.draft"))

    deployed_dir = deploy_capcut_project(str(project_dir))

    deployed_path = Path(deployed_dir)
    assert deployed_path.exists()
    assert deployed_path.name == "sample_26-03-31-20-15-00"
    assert (deployed_path / "draft_content.json").exists()


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
