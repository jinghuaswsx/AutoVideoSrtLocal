from pathlib import Path
import types
import sys
import json

from pipeline.capcut import export_capcut_project


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

    assert ("create_draft", "capcut_project", 1080, 1920, True) in calls
    assert any(item[0] == "import_srt" for item in calls)
    assert any(item[0] == "save" for item in calls)
    assert Path(export["project_dir"]).exists()
    manifest = json.loads(Path(export["manifest_path"]).read_text(encoding="utf-8"))
    assert manifest["backend"] == "pyJianYingDraft"
