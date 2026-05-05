"""Manifest serialization tests."""
from __future__ import annotations

import json
from pathlib import Path

from appcore.vace_subtitle.manifest import (
    ChunkRecord,
    Manifest,
    manifest_path_for,
)


def test_manifest_path_for():
    assert manifest_path_for(Path("a/b/out.mp4")) == Path("a/b/out.mp4.vace.json")


def test_manifest_round_trip(tmp_path):
    m = Manifest(
        input_video="in.mp4",
        output_video="out.mp4",
        input_width=1920, input_height=1080, input_fps=30.0, input_duration=10.0,
        bbox_original=(0, 778, 1920, 1026),
        crop_bbox_original=(0, 650, 1920, 1074),
        mode="roi_1080", profile="rtx3060_safe",
        model_name="vace-1.3B", size="480p",
        frame_num=41, sample_steps=20,
        prompt="clean", vace_repo_dir="C:/AI/VACE",
        vace_python_exe="C:/AI/VACE/.venv/Scripts/python.exe",
        model_dir="C:/AI/VACE/models",
    )
    m.chunks.append(ChunkRecord(
        index=0, start_seconds=0.0, duration_seconds=2.7,
        original_chunk_path="orig.mp4", crop_chunk_path="crop.mp4",
        vace_input_path="crop.mp4", vace_output_path="vace.mp4",
        composited_chunk_path="comp.mp4",
        command=["python", "vace_pipeline.py"],
        returncode=0, elapsed_seconds=12.3, status="done",
    ))
    out = tmp_path / "x.mp4.vace.json"
    m.write(out)

    parsed = json.loads(out.read_text(encoding="utf-8"))
    assert parsed["input_video"] == "in.mp4"
    assert parsed["bbox_original"] == [0, 778, 1920, 1026]
    assert len(parsed["chunks"]) == 1
    assert parsed["chunks"][0]["status"] == "done"

    reread = Manifest.read(out)
    assert reread.input_video == "in.mp4"
    assert reread.chunks[0].command == ["python", "vace_pipeline.py"]


def test_manifest_failure_is_serializable(tmp_path):
    m = Manifest(input_video="in.mp4", output_video="out.mp4")
    m.status = "failed"
    m.errors.append("VaceSubprocessError: rc=1 oom=True")
    p = tmp_path / "fail.vace.json"
    m.write(p)
    data = json.loads(p.read_text(encoding="utf-8"))
    assert data["status"] == "failed"
    assert "VaceSubprocessError" in data["errors"][0]
