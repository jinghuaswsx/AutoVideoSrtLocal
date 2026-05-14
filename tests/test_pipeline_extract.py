from __future__ import annotations

from types import SimpleNamespace

from pipeline import extract


def test_extract_separation_audio_uses_44100hz_stereo_pcm_wav(tmp_path, monkeypatch):
    calls = []

    def fake_run(cmd, capture_output=False, text=False):
        calls.append(
            {
                "cmd": cmd,
                "capture_output": capture_output,
                "text": text,
            }
        )
        return SimpleNamespace(returncode=0, stderr="")

    monkeypatch.setattr("pipeline.extract.subprocess.run", fake_run)

    out = extract.extract_separation_audio("/videos/source.mp4", str(tmp_path))

    assert out == str(tmp_path / "source_separation.wav")
    assert calls, "ffmpeg should be invoked"
    cmd = calls[0]["cmd"]
    assert cmd[:2] == ["ffmpeg", "-y"]
    assert "-vn" in cmd
    assert cmd[cmd.index("-acodec") + 1] == "pcm_s16le"
    assert cmd[cmd.index("-ar") + 1] == "44100"
    assert cmd[cmd.index("-ac") + 1] == "2"
    assert cmd[-1] == str(tmp_path / "source_separation.wav")
