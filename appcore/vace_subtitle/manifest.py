"""Job manifest: a JSON sidecar capturing every decision and intermediate path.

A manifest file is written next to the output video as ``<output>.vace.json``.
It includes input metadata, geometry decisions, profile, per-chunk records,
the exact commands executed, and final status — enough to reproduce a run
or to skip already-finished chunks on retry.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class ChunkRecord:
    """One chunk's lifecycle data."""

    index: int
    start_seconds: float
    duration_seconds: float
    original_chunk_path: str | None = None
    crop_chunk_path: str | None = None
    vace_input_path: str | None = None
    vace_output_path: str | None = None
    composited_chunk_path: str | None = None
    command: list[str] = field(default_factory=list)
    returncode: int | None = None
    elapsed_seconds: float | None = None
    status: str = "pending"               # pending / running / done / failed / skipped
    error: str | None = None


@dataclass
class Manifest:
    """Top-level manifest for one ``remove_subtitles`` invocation."""

    input_video: str
    output_video: str
    input_width: int = 0
    input_height: int = 0
    input_fps: float = 0.0
    input_duration: float = 0.0
    bbox_original: tuple[int, int, int, int] | None = None
    crop_bbox_original: tuple[int, int, int, int] | None = None
    mode: str = "roi_1080"
    profile: str = ""
    model_name: str = ""
    size: str = ""
    frame_num: int = 0
    sample_steps: int = 0
    offload_model: bool = True
    t5_cpu: bool = True
    prompt: str = ""
    vace_repo_dir: str = ""
    vace_python_exe: str = ""
    model_dir: str = ""
    chunks: list[ChunkRecord] = field(default_factory=list)
    final_mux_command: list[str] = field(default_factory=list)
    started_at: str = field(default_factory=_now_iso)
    finished_at: str | None = None
    status: str = "pending"               # pending / running / done / failed / dry-run
    errors: list[str] = field(default_factory=list)

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False, indent=2, default=_default)

    def write(self, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.to_json(), encoding="utf-8")
        return path

    @classmethod
    def read(cls, path: Path) -> "Manifest":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        chunks = [ChunkRecord(**c) for c in data.pop("chunks", [])]
        m = cls(**data)
        m.chunks = chunks
        return m


def _default(o: Any) -> Any:
    if isinstance(o, Path):
        return str(o)
    if isinstance(o, tuple):
        return list(o)
    raise TypeError(f"unserializable {type(o).__name__}: {o!r}")


def manifest_path_for(output_video: Path) -> Path:
    """Convention: ``<output>.vace.json`` next to the output video."""
    return output_video.with_suffix(output_video.suffix + ".vace.json")
