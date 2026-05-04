"""
FastAPI wrapper for python-audio-separator
GPU-accelerated audio stem separation service (port 80)

Architecture:
  - asyncio.Lock: serialized GPU access, concurrent requests queue automatically
  - MD5 cache: same file + same params within 1h returns cached result instantly
  - Client sets timeout=300s, no polling needed
"""

import asyncio
import hashlib
import io
import json
import logging
import os
import platform
import time
import uuid
from pathlib import Path
from typing import Optional

import uvicorn
import torch
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse

from audio_separator.separator import Separator

# ── Config ─────────────────────────────────────────────────────────────────
_BASE = os.path.realpath("/g/audio")
MODEL_DIR = os.path.join(_BASE, "models")
OUTPUT_DIR = os.path.join(_BASE, "output")
LOG_DIR = os.path.join(_BASE, "logs")
CACHE_DIR = os.path.join(_BASE, "cache")
DEFAULT_ENSEMBLE_PRESET = "vocal_balanced"
CACHE_TTL_SEC = 3600  # 1 hour

for d in (MODEL_DIR, OUTPUT_DIR, LOG_DIR, CACHE_DIR):
    os.makedirs(d, exist_ok=True)

# ── Resource Limits ────────────────────────────────────────────────────────


def _apply_resource_limits():
    if torch.cuda.is_available():
        total_gpu = torch.cuda.get_device_properties(0).total_memory
        torch.cuda.set_per_process_memory_fraction(0.9)
        logger.info(f"GPU mem limit: 90% ({total_gpu * 0.9 / 1024**3:.1f} GB / {total_gpu / 1024**3:.1f} GB)")

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
logger = logging.getLogger("audio_api")

app = FastAPI(title="Audio Separator API", version="2.1.0", docs_url="/docs")

# ── GPU Lock ───────────────────────────────────────────────────────────────
_gpu_lock = asyncio.Lock()
_queue_count: int = 0


@app.middleware("http")
async def _track_queue_middleware(request, call_next):
    global _queue_count
    if request.url.path in ("/separate", "/separate/download"):
        _queue_count += 1
        try:
            return await call_next(request)
        finally:
            _queue_count -= 1
    return await call_next(request)


# ── MD5 Result Cache ──────────────────────────────────────────────────────
# _cache[key] = (expires_at, result_data)
# key = md5:{hash}:preset:{p}:fmt:{f}:stem:{s}
_cache: dict[str, tuple] = {}
_cache_lock = asyncio.Lock()


def _cache_key(content_hash: str, preset: str, fmt: str, single_stem: str) -> str:
    return f"md5:{content_hash}:preset:{preset}:fmt:{fmt}:stem:{single_stem or ''}"


async def _cache_get(key: str):
    async with _cache_lock:
        entry = _cache.get(key)
        if entry and entry[0] > time.time():
            return entry[1]
        # Expired or missing
        _cache.pop(key, None)
        return None


async def _cache_set(key: str, data, ttl=CACHE_TTL_SEC):
    async with _cache_lock:
        _cache[key] = (time.time() + ttl, data)


async def _cache_cleanup():
    """Periodically evict expired cache entries."""
    while True:
        await asyncio.sleep(300)  # every 5 min
        now = time.time()
        async with _cache_lock:
            expired = [k for k, (exp, _) in _cache.items() if exp <= now]
            for k in expired:
                del _cache[k]
        if expired:
            logger.info(f"Cache cleanup: removed {len(expired)} expired entries (total: {len(_cache)})")


# ── Model Cache ────────────────────────────────────────────────────────────
_sep_cache: dict[str, Separator] = {}

ENSEMBLE_PRESETS = {
    "vocal_balanced": "Best overall vocals — Resurrection + Beta 6X (avg_fft)",
    "vocal_clean": "Minimal instrument bleed — Revive V2 + FT2 bleedless (min_fft)",
    "vocal_full": "Max vocal capture incl. harmonies — Revive 3e + becruily (max_fft)",
    "vocal_rvc": "Optimized for RVC training — Beta 6X + Gabox FV4 (avg_wave)",
    "instrumental_clean": "Cleanest instrumentals, minimal vocal bleed (uvr_max_spec)",
    "instrumental_full": "Max instrument preservation (uvr_max_spec)",
    "instrumental_balanced": "Good balance of noise and fullness (uvr_max_spec)",
    "instrumental_low_resource": "Fast ensemble for low VRAM (avg_fft)",
    "karaoke": "Lead vocal removal — 3-model karaoke (avg_wave)",
}


def _get_separator(model_filename: str = None, ensemble_preset: str = None) -> Separator:
    global _sep_cache
    key = f"preset:{ensemble_preset}" if ensemble_preset else (f"model:{model_filename}" if model_filename else f"preset:{DEFAULT_ENSEMBLE_PRESET}")
    if key in _sep_cache:
        return _sep_cache[key]

    logger.info(f"Loading separator: {key}")
    if ensemble_preset:
        sep = Separator(log_level=logging.INFO, model_file_dir=MODEL_DIR, output_dir=OUTPUT_DIR,
                        output_format="WAV", normalization_threshold=0.9, use_autocast=True,
                        ensemble_preset=ensemble_preset)
        sep.load_model()
    else:
        fn = model_filename or DEFAULT_ENSEMBLE_PRESET
        if fn in ENSEMBLE_PRESETS:
            sep = Separator(log_level=logging.INFO, model_file_dir=MODEL_DIR, output_dir=OUTPUT_DIR,
                            output_format="WAV", normalization_threshold=0.9, use_autocast=True,
                            ensemble_preset=fn)
            sep.load_model()
        else:
            sep = Separator(log_level=logging.INFO, model_file_dir=MODEL_DIR, output_dir=OUTPUT_DIR,
                            output_format="WAV", normalization_threshold=0.9, use_autocast=True)
            sep.load_model(model_filename=fn)

    _sep_cache[key] = sep
    if len(_sep_cache) > 3:
        oldest = next(iter(_sep_cache))
        try:
            del _sep_cache[oldest]
        except Exception:
            pass
    return sep


# ── Helpers ────────────────────────────────────────────────────────────────


def _load_model_index() -> dict:
    import importlib.resources as res
    try:
        return json.loads(res.read_text("audio_separator", "models.json"))
    except Exception:
        return json.loads(Path("/g/audio/audio_separator/models.json").read_text(encoding="utf-8"))


def _list_models() -> list[dict]:
    idx = _load_model_index()
    results = []
    for cat, entries in idx.items():
        if isinstance(entries, dict):
            for name, fname in entries.items():
                dl = fname if isinstance(fname, str) else (list(fname.keys())[0] if isinstance(fname, dict) else str(fname))
                results.append({"category": cat, "display_name": name, "download_filename": dl})
        elif isinstance(entries, str):
            results.append({"category": cat, "display_name": cat, "download_filename": entries})
    return results


# ── Core separation ────────────────────────────────────────────────────────


def _run_separation(input_path: str,
                    ensemble_preset: Optional[str], model_filename: Optional[str],
                    output_format: str, single_stem: Optional[str]) -> dict:
    """Run separation (sync, called in thread pool under GPU lock)."""
    if not os.path.isfile(input_path):
        raise FileNotFoundError(f"Input file not found: {input_path}")

    sep = _get_separator(model_filename=model_filename, ensemble_preset=ensemble_preset)
    sep.output_format = output_format
    sep.output_single_stem = single_stem

    t0 = time.perf_counter()
    output_files = sep.separate(input_path)
    elapsed = time.perf_counter() - t0

    output_files = [os.path.join(OUTPUT_DIR, f) if not os.path.isabs(f) else f for f in output_files]
    stem_names = [Path(fp).stem for fp in output_files]

    return {"stem_names": stem_names, "output_files": output_files, "duration_seconds": round(elapsed, 2)}


async def _process_or_cache(content: bytes, filename: str, input_size_mb: float,
                            ensemble_preset: Optional[str], model_filename: Optional[str],
                            output_format: str, single_stem: Optional[str],
                            return_zip: bool = False):
    """Check cache, then run separation under GPU lock."""
    content_hash = hashlib.md5(content).hexdigest()
    preset = ensemble_preset or DEFAULT_ENSEMBLE_PRESET
    ck = _cache_key(content_hash, preset, output_format, single_stem or '')

    # Check cache first
    cached = await _cache_get(ck)
    if cached is not None:
        logger.info(f"[cache HIT] {filename} (md5={content_hash[:8]}...)")
        return cached, True  # (result_data, from_cache)

    logger.info(f"[cache MISS] {filename} md5={content_hash[:8]}... waiting for GPU...")

    # Save temp file
    ext = Path(filename).suffix or ".wav"
    input_path = os.path.join(OUTPUT_DIR, f"input_{uuid.uuid4().hex[:12]}{ext}")
    with open(input_path, "wb") as f:
        f.write(content)

    try:
        async with _gpu_lock:
            logger.info(f"[gpu] started: {filename}")
            result = await asyncio.to_thread(
                _run_separation, input_path, ensemble_preset, model_filename, output_format, single_stem
            )
    finally:
        try:
            os.remove(input_path)
        except Exception:
            pass

    # Build response
    if return_zip:
        import zipfile
        zip_buf = io.BytesIO()
        with zipfile.ZipFile(zip_buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for fp in result["output_files"]:
                if os.path.exists(fp):
                    zf.write(fp, f"{Path(fp).stem}.{output_format.lower()}")
        zip_buf.seek(0)
        # Cache the zip bytes
        await _cache_set(ck, zip_buf.getvalue())
        # Cleanup output files after caching
        for fp in result["output_files"]:
            try:
                os.remove(fp)
            except Exception:
                pass
        return zip_buf.getvalue(), False
    else:
        resp = {
            "status": "ok",
            "duration_seconds": result["duration_seconds"],
            "input_file": filename,
            "input_size_mb": round(input_size_mb, 2),
            "preset": preset,
            "output_format": output_format,
            "stems": result["stem_names"],
            "cached": False,
        }
        await _cache_set(ck, json.dumps(resp).encode("utf-8"))
        # Cleanup output files
        for fp in result["output_files"]:
            try:
                os.remove(fp)
            except Exception:
                pass
        return resp, False


# ── Startup ────────────────────────────────────────────────────────────────


@app.on_event("startup")
async def startup():
    _apply_resource_limits()
    asyncio.create_task(_cache_cleanup())

    logger.info(f"Pre-warming: {DEFAULT_ENSEMBLE_PRESET}")
    try:
        _get_separator(ensemble_preset=DEFAULT_ENSEMBLE_PRESET)
        logger.info("Pre-warm complete.")
    except Exception as e:
        logger.warning(f"Pre-warm failed: {e}")


# ── Routes ─────────────────────────────────────────────────────────────────


@app.get("/health")
async def health():
    info = {
        "status": "ok",
        "cuda_available": torch.cuda.is_available(),
        "cuda_device": torch.cuda.get_device_name(0) if torch.cuda.is_available() else None,
        "gpu_memory_90pct": f"{torch.cuda.get_device_properties(0).total_memory * 0.9 / 1024**3:.1f} GB" if torch.cuda.is_available() else "N/A",
        "default_preset": DEFAULT_ENSEMBLE_PRESET,
        "queue": {"waiting_or_active": max(0, _queue_count), "gpu_busy": _gpu_lock.locked()},
        "cache": {"entries": len(_cache), "ttl_sec": CACHE_TTL_SEC},
    }
    try:
        import psutil
        info["cpu_affinity"] = str(psutil.Process(os.getpid()).cpu_affinity())
    except Exception:
        pass
    return info


@app.get("/queue")
async def queue_status():
    return {
        "waiting_or_active": max(0, _queue_count),
        "gpu_busy": _gpu_lock.locked(),
        "cache_entries": len(_cache),
    }


@app.get("/models")
async def list_models():
    return {"count": 0, "models": _list_models()}


@app.get("/presets")
async def list_presets():
    return {"count": len(ENSEMBLE_PRESETS), "default": DEFAULT_ENSEMBLE_PRESET, "presets": ENSEMBLE_PRESETS}


@app.post("/separate")
async def separate_audio(
    file: UploadFile = File(...),
    ensemble_preset: Optional[str] = Form(default=None),
    model_filename: Optional[str] = Form(default=None),
    output_format: str = Form(default="WAV"),
    single_stem: Optional[str] = Form(default=None),
):
    """
    Upload audio → MD5 cache check → GPU queue → separate → JSON metadata.

    Same file + same params within 1h returns instant cached result.
    Client MUST set timeout >= 300s for uncached requests.
    """
    output_format = output_format.upper()
    if output_format not in ("WAV", "FLAC", "MP3", "OGG", "M4A"):
        raise HTTPException(400, f"Unsupported format: {output_format}")

    content = await file.read()
    input_size_mb = len(content) / (1024 * 1024)

    result, from_cache = await _process_or_cache(
        content, file.filename, input_size_mb,
        ensemble_preset, model_filename, output_format, single_stem,
        return_zip=False,
    )

    if from_cache:
        if isinstance(result, bytes):
            result = json.loads(result.decode("utf-8"))
    result["cached"] = from_cache
    return result


@app.post("/separate/download")
async def separate_and_download(
    file: UploadFile = File(...),
    ensemble_preset: Optional[str] = Form(default=None),
    model_filename: Optional[str] = Form(default=None),
    output_format: str = Form(default="WAV"),
    single_stem: Optional[str] = Form(default=None),
):
    """
    Upload audio → MD5 cache check → GPU queue → separate → download ZIP.

    Same file + same params within 1h returns cached ZIP instantly.
    """
    output_format = output_format.upper()
    if output_format not in ("WAV", "FLAC", "MP3", "OGG", "M4A"):
        raise HTTPException(400, f"Unsupported format: {output_format}")

    content = await file.read()
    input_size_mb = len(content) / (1024 * 1024)

    result, from_cache = await _process_or_cache(
        content, file.filename, input_size_mb,
        ensemble_preset, model_filename, output_format, single_stem,
        return_zip=True,
    )

    # result is zip bytes
    buf = io.BytesIO(result if from_cache else result)
    buf.seek(0)
    base_name = Path(file.filename).stem

    # Get stem names from cache key or use default
    preset = ensemble_preset or DEFAULT_ENSEMBLE_PRESET
    ck = _cache_key(hashlib.md5(content).hexdigest(), preset, output_format, single_stem or '')
    cached_json = await _cache_get(ck + ":meta")
    stems_str = cached_json.decode() if cached_json else "unknown"

    return FileResponse(
        buf,
        media_type="application/zip",
        filename=f"{base_name}_separated.zip",
        headers={
            "X-Cached": str(from_cache).lower(),
            "X-Stems": stems_str,
        },
    )


# ── Entrypoint ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    logger.info("Starting Audio Separator API on port 80 (cache + queue)...")
    uvicorn.run("api_server:app", host="0.0.0.0", port=80, log_level="info", workers=1)
