"""
FastAPI wrapper for video-subtitle-remover (VSR)
GPU-accelerated hardcoded subtitle / watermark removal service.

Architecture:
  - asyncio.Lock: serialized GPU access, concurrent requests queue automatically
  - MD5 cache: same file + same algorithm + same sub_area within 1h returns cached result
  - Client sets timeout=1800s for long videos, no polling needed

Routing layout (since 2026-05-05 multi-service consolidation):
  - This service runs on internal port 8082.
  - All routes are mounted under the `/subtitle` URL prefix so the upstream
    Caddy (port 80) can reverse-proxy `/subtitle/*` here without rewriting.
"""

import asyncio
import hashlib
import logging
import os
import platform
import shutil
import sys
import time
import uuid
from pathlib import Path
from typing import Optional

import uvicorn
import torch
from fastapi import APIRouter, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse

# ── Config ─────────────────────────────────────────────────────────────────
# Allow override via env so the same code can be used in non-default install paths.
_BASE = os.path.realpath(os.environ.get("VSR_BASE", r"G:\subtitle"))
_RESOURCES = os.path.join(_BASE, "resources")
INPUT_DIR = os.path.join(_BASE, "inputs")
OUTPUT_DIR = os.path.join(_BASE, "output")
LOG_DIR = os.path.join(_BASE, "logs")
CACHE_DIR = os.path.join(_BASE, "cache")
DEFAULT_ALGORITHM = "sttn"
CACHE_TTL_SEC = 3600  # 1 hour
URL_PREFIX = "/subtitle"
SERVICE_PORT = 8082

# Reserve ~50% GPU memory for ourselves so a co-tenant service (audio_separator,
# vace) can fit too. 12GB card → 6GB ceiling for VSR alone.
GPU_MEMORY_FRACTION = 0.5
# 12GB cap for ProPainter when sharing GPU with audio_separator (was 40 standalone).
PROPAINTER_MAX_LOAD_NUM_SHARED = 25

for d in (INPUT_DIR, OUTPUT_DIR, LOG_DIR, CACHE_DIR):
    os.makedirs(d, exist_ok=True)

# VSR backend lives under resources/. Prepend so `from backend import ...` works.
if _RESOURCES not in sys.path:
    sys.path.insert(0, _RESOURCES)
# backend.config uses relative paths internally — chdir into resources before import
os.chdir(_RESOURCES)

# ── Resource Limits ────────────────────────────────────────────────────────


def _apply_resource_limits():
    if torch.cuda.is_available():
        total_gpu = torch.cuda.get_device_properties(0).total_memory
        torch.cuda.set_per_process_memory_fraction(GPU_MEMORY_FRACTION)
        logger.info(
            f"GPU mem limit: {GPU_MEMORY_FRACTION*100:.0f}% "
            f"({total_gpu * GPU_MEMORY_FRACTION / 1024**3:.1f} GB / "
            f"{total_gpu / 1024**3:.1f} GB)"
        )

    try:
        import psutil
        pid = os.getpid()
        proc = psutil.Process(pid)
        if platform.system() == "Windows":
            proc.nice(psutil.HIGH_PRIORITY_CLASS)
        cpu_count = psutil.cpu_count(logical=True)
        if cpu_count and cpu_count > 1:
            proc.cpu_affinity(list(range(cpu_count // 2)))
    except ImportError:
        pass
    except Exception:
        pass


# ── Logging ────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler(os.path.join(LOG_DIR, "api_server.log"), encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger("subtitle_api")

app = FastAPI(title="Subtitle Remover API", version="1.1.0", docs_url=f"{URL_PREFIX}/docs")
router = APIRouter(prefix=URL_PREFIX)

# ── GPU Lock ───────────────────────────────────────────────────────────────
_gpu_lock = asyncio.Lock()
_queue_count: int = 0
_QUEUE_PATHS = (f"{URL_PREFIX}/remove", f"{URL_PREFIX}/remove/download")


@app.middleware("http")
async def _track_queue_middleware(request, call_next):
    global _queue_count
    if request.url.path in _QUEUE_PATHS:
        _queue_count += 1
        try:
            return await call_next(request)
        finally:
            _queue_count -= 1
    return await call_next(request)


# ── MD5 Result Cache ──────────────────────────────────────────────────────
# _cache[key] = (expires_at, result_dict)
# result_dict["cached_output"] points to a file under CACHE_DIR
_cache: dict[str, tuple] = {}
_cache_lock = asyncio.Lock()


def _cache_key(content_hash: str, algorithm: str, sub_area: str) -> str:
    return f"md5:{content_hash}:algo:{algorithm}:area:{sub_area or 'auto'}"


async def _cache_get(key: str):
    async with _cache_lock:
        entry = _cache.get(key)
        if entry and entry[0] > time.time():
            return entry[1]
        # Expired or missing
        evicted = _cache.pop(key, None)
        if evicted:
            _drop_cached_file(evicted[1])
        return None


async def _cache_set(key: str, data, ttl: int = CACHE_TTL_SEC):
    async with _cache_lock:
        # If overwriting, drop the old cached file first
        old = _cache.get(key)
        if old:
            _drop_cached_file(old[1])
        _cache[key] = (time.time() + ttl, data)


def _drop_cached_file(result: dict):
    p = result.get("cached_output") if isinstance(result, dict) else None
    if not p:
        return
    fp = Path(p)
    if fp.exists() and fp.parent == Path(CACHE_DIR):
        try:
            fp.unlink()
        except Exception:
            pass


async def _cache_cleanup():
    """Periodically evict expired cache entries."""
    while True:
        await asyncio.sleep(300)  # every 5 min
        now = time.time()
        async with _cache_lock:
            expired_keys = [k for k, (exp, _) in _cache.items() if exp <= now]
            for k in expired_keys:
                _, val = _cache.pop(k)
                _drop_cached_file(val)
        if expired_keys:
            logger.info(
                f"Cache cleanup: removed {len(expired_keys)} expired (total: {len(_cache)})"
            )


# ── Algorithms ─────────────────────────────────────────────────────────────
ALGORITHMS = {
    "sttn":       "Spatial-Temporal Transformer — fast, best for live-action video (default)",
    "lama":       "LaMa — moderate speed, best for animation / static backgrounds",
    "propainter": "ProPainter — slow, high VRAM, best for high-motion video",
}


# ── Core ───────────────────────────────────────────────────────────────────
def _run_subtitle_remove(input_path: str, algorithm: str, sub_area: Optional[tuple]) -> dict:
    """Sync, runs in thread pool under GPU lock."""
    if not os.path.isfile(input_path):
        raise FileNotFoundError(f"Input file not found: {input_path}")

    from backend import config
    from backend.config import InpaintMode

    algo_map = {
        "sttn": InpaintMode.STTN,
        "lama": InpaintMode.LAMA,
        "propainter": InpaintMode.PROPAINTER,
    }
    config.MODE = algo_map[algorithm]
    if algorithm == "propainter":
        # 12GB 显存兜底，且本进程已让出一半给 audio_separator/vace 同租户
        config.PROPAINTER_MAX_LOAD_NUM = min(
            getattr(config, "PROPAINTER_MAX_LOAD_NUM", 70),
            PROPAINTER_MAX_LOAD_NUM_SHARED,
        )

    from backend.main import SubtitleRemover

    t0 = time.perf_counter()
    remover = SubtitleRemover(input_path, sub_area=sub_area, gui_mode=False)
    remover.run()
    elapsed = time.perf_counter() - t0

    if not remover.video_out_name or not os.path.isfile(remover.video_out_name):
        raise RuntimeError("SubtitleRemover produced no output file")

    return {"output_path": remover.video_out_name, "duration_seconds": round(elapsed, 2)}


async def _process_or_cache(
    content: bytes,
    filename: str,
    input_size_mb: float,
    algorithm: str,
    sub_area: Optional[tuple],
):
    content_hash = hashlib.md5(content).hexdigest()
    sa_str = ",".join(str(x) for x in sub_area) if sub_area else ""
    ck = _cache_key(content_hash, algorithm, sa_str)

    cached = await _cache_get(ck)
    if cached is not None and Path(cached["cached_output"]).exists():
        logger.info(f"[cache HIT] {filename} (md5={content_hash[:8]}...)")
        return cached, True

    logger.info(f"[cache MISS] {filename} md5={content_hash[:8]}... waiting for GPU...")

    ext = Path(filename).suffix or ".mp4"
    input_path = os.path.join(INPUT_DIR, f"input_{uuid.uuid4().hex[:12]}{ext}")
    with open(input_path, "wb") as f:
        f.write(content)

    try:
        async with _gpu_lock:
            logger.info(f"[gpu] started: {filename} algo={algorithm} sub_area={sub_area}")
            result = await asyncio.to_thread(
                _run_subtitle_remove, input_path, algorithm, sub_area
            )
    finally:
        try:
            os.remove(input_path)
        except Exception:
            pass

    # Move output (next to input, suffixed `_no_sub.mp4`) into cache dir
    src = Path(result["output_path"])
    cached_path = Path(CACHE_DIR) / f"{content_hash[:12]}_{algorithm}_{src.name}"
    try:
        shutil.move(str(src), str(cached_path))
    except Exception:
        shutil.copy2(str(src), str(cached_path))
        try:
            src.unlink()
        except Exception:
            pass

    resp = {
        "status": "ok",
        "duration_seconds": result["duration_seconds"],
        "input_file": filename,
        "input_size_mb": round(input_size_mb, 2),
        "algorithm": algorithm,
        "sub_area": list(sub_area) if sub_area else None,
        "output_filename": cached_path.name,
        "cached_output": str(cached_path),
        "cached": False,
    }
    await _cache_set(ck, resp)
    return resp, False


# ── Startup ────────────────────────────────────────────────────────────────


@app.on_event("startup")
async def startup():
    _apply_resource_limits()
    asyncio.create_task(_cache_cleanup())

    # Pre-warm: import VSR backend so first request doesn't pay init cost
    logger.info("Pre-warming VSR backend (loading models)...")
    try:
        await asyncio.to_thread(_prewarm)
        logger.info("Pre-warm complete.")
    except Exception as e:
        logger.warning(f"Pre-warm failed: {e}")


def _prewarm():
    # Importing config triggers ffmpeg/model file unsplit; importing main loads class refs.
    from backend import config  # noqa: F401
    from backend.main import SubtitleRemover  # noqa: F401
    return True


# ── Helpers ────────────────────────────────────────────────────────────────


def _parse_sub_area(s: Optional[str]) -> Optional[tuple]:
    if not s:
        return None
    try:
        parts = [int(x) for x in s.replace(" ", "").split(",")]
        if len(parts) != 4:
            raise ValueError
        return tuple(parts)
    except Exception:
        raise HTTPException(400, "sub_area format: 'ymin,ymax,xmin,xmax'")


def _validate_algorithm(a: str) -> str:
    a = (a or DEFAULT_ALGORITHM).lower().strip()
    if a not in ALGORITHMS:
        raise HTTPException(400, f"Unsupported algorithm: {a}. Available: {list(ALGORITHMS)}")
    return a


# Note: routes are defined above as @router.* and registered here.
# Keep this include AFTER all @router definitions.


# ── Routes ─────────────────────────────────────────────────────────────────


@router.get("/health")
async def health():
    info = {
        "status": "ok",
        "cuda_available": torch.cuda.is_available(),
        "cuda_device": torch.cuda.get_device_name(0) if torch.cuda.is_available() else None,
        "gpu_memory_limit": (
            f"{torch.cuda.get_device_properties(0).total_memory * GPU_MEMORY_FRACTION / 1024**3:.1f} GB "
            f"({GPU_MEMORY_FRACTION*100:.0f}%)"
            if torch.cuda.is_available() else "N/A"
        ),
        "default_algorithm": DEFAULT_ALGORITHM,
        "queue": {"waiting_or_active": max(0, _queue_count), "gpu_busy": _gpu_lock.locked()},
        "cache": {"entries": len(_cache), "ttl_sec": CACHE_TTL_SEC},
    }
    try:
        import psutil
        info["cpu_affinity"] = str(psutil.Process(os.getpid()).cpu_affinity())
    except Exception:
        pass
    return info


@router.get("/queue")
async def queue_status():
    return {
        "waiting_or_active": max(0, _queue_count),
        "gpu_busy": _gpu_lock.locked(),
        "cache_entries": len(_cache),
    }


@router.get("/algorithms")
async def list_algorithms():
    return {"count": len(ALGORITHMS), "default": DEFAULT_ALGORITHM, "algorithms": ALGORITHMS}


@router.post("/remove")
async def remove_subtitles(
    file: UploadFile = File(...),
    algorithm: str = Form(default=DEFAULT_ALGORITHM),
    sub_area: Optional[str] = Form(default=None),
):
    """
    Upload video → MD5 cache check → GPU queue → remove subtitles → JSON metadata.

    Same file + same params within 1h returns cached metadata instantly.
    Client MUST set timeout >= 1800s for uncached uploads (long videos take time).

    sub_area: 'ymin,ymax,xmin,xmax' to limit removal to one rectangle. Omit = auto-detect.
    """
    algorithm = _validate_algorithm(algorithm)
    sa = _parse_sub_area(sub_area)

    content = await file.read()
    input_size_mb = len(content) / (1024 * 1024)

    result, from_cache = await _process_or_cache(
        content, file.filename or "video.mp4", input_size_mb, algorithm, sa
    )
    safe_resp = {**result, "cached": from_cache}
    safe_resp.pop("cached_output", None)  # do not expose server path
    return safe_resp


@router.post("/remove/download")
async def remove_and_download(
    file: UploadFile = File(...),
    algorithm: str = Form(default=DEFAULT_ALGORITHM),
    sub_area: Optional[str] = Form(default=None),
):
    """
    Upload video → MD5 cache check → GPU queue → remove subtitles → return MP4.
    Same file + same params within 1h returns cached MP4 instantly.
    """
    algorithm = _validate_algorithm(algorithm)
    sa = _parse_sub_area(sub_area)

    content = await file.read()
    input_size_mb = len(content) / (1024 * 1024)

    result, from_cache = await _process_or_cache(
        content, file.filename or "video.mp4", input_size_mb, algorithm, sa
    )

    out_path = result["cached_output"]
    base_name = Path(file.filename or "video").stem
    return FileResponse(
        out_path,
        media_type="video/mp4",
        filename=f"{base_name}_no_sub.mp4",
        headers={
            "X-Cached": str(from_cache).lower(),
            "X-Duration-Seconds": str(result["duration_seconds"]),
        },
    )


app.include_router(router)


# ── Entrypoint ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    logger.info(
        f"Starting Subtitle Remover API on port {SERVICE_PORT} prefix={URL_PREFIX} ..."
    )
    # Pass `app` object (not "api_server:app" string) because we chdir into resources/
    # at import time, so uvicorn's reloader can no longer find the module by name.
    uvicorn.run(app, host="0.0.0.0", port=SERVICE_PORT, log_level="info", workers=1)
