from __future__ import annotations

import json
import re
import shutil
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from pathlib import Path
from typing import Any, Callable, Mapping

from config import OUTPUT_DIR
from appcore import llm_client
from appcore.meta_hot_posts import store, video_localization

VIDEO_COPYABILITY_USE_CASE = "meta_hot_posts.video_copyability"
VIDEO_COPYABILITY_PROVIDER = "gemini_vertex_adc"
VIDEO_COPYABILITY_MODEL = "gemini-3-flash-preview"
DEFAULT_ANALYSIS_SUBDIR = Path("meta_hot_posts") / "analysis_videos"
DEFAULT_ANALYSIS_LIMIT = 20
DEFAULT_ANALYSIS_DELAY_SECONDS = 30
VIDEO_COPYABILITY_TIMEOUT_SECONDS = 40

RunFn = Callable[..., Any]
SleepFn = Callable[[float], None]


def _safe_id(value: Any) -> str:
    raw = str(value or "").strip()
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", raw).strip("._") or "post"


def _relative_to_output(path: Path, *, output_dir: str | Path) -> str:
    return path.resolve().relative_to(Path(output_dir).resolve()).as_posix()


def compress_video_for_analysis(
    local_video_path: str | Path,
    *,
    post_id: int,
    output_dir: str | Path = OUTPUT_DIR,
    run_fn: RunFn = subprocess.run,
    which_fn: Callable[[str], str | None] = shutil.which,
    timeout_seconds: int = 600,
) -> str:
    executable = which_fn("ffmpeg")
    if not executable:
        raise RuntimeError("ffmpeg is not installed")

    source = Path(local_video_path)
    if not source.is_file():
        raise RuntimeError(f"local video file not found: {source}")

    root = Path(output_dir)
    target_dir = root / DEFAULT_ANALYSIS_SUBDIR
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"meta_hot_post_{_safe_id(post_id)}_480p15_600k.mp4"
    if target.is_file() and target.stat().st_size > 0:
        return _relative_to_output(target, output_dir=root)

    command = [
        executable,
        "-y",
        "-i",
        str(source),
        "-vf",
        "scale=-2:480,fps=15",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-b:v",
        "600k",
        "-maxrate",
        "600k",
        "-bufsize",
        "1200k",
        "-c:a",
        "aac",
        "-b:a",
        "64k",
        "-movflags",
        "+faststart",
        str(target),
    ]
    completed = run_fn(
        command,
        timeout=int(timeout_seconds),
        capture_output=True,
        text=True,
    )
    if getattr(completed, "returncode", 1) != 0:
        stderr = str(getattr(completed, "stderr", "") or "").strip()
        stdout = str(getattr(completed, "stdout", "") or "").strip()
        raise RuntimeError((stderr or stdout or "ffmpeg failed")[:1000])
    if not target.is_file() or target.stat().st_size <= 0:
        raise RuntimeError("ffmpeg completed but no compressed video was created")
    return _relative_to_output(target, output_dir=root)


def build_response_schema() -> dict[str, Any]:
    score = {"type": "number", "minimum": 0, "maximum": 100}
    return {
        "type": "object",
        "properties": {
            "overall_score": score,
            "copyability_score": score,
            "meta_us_ad_fit_score": score,
            "product_fit_score": score,
            "compliance_risk_score": score,
            "recommendation": {"type": "string", "enum": ["copy", "adapt", "avoid"]},
            "summary": {"type": "string"},
            "summary_zh": {"type": "string"},
            "winning_angles": {"type": "array", "items": {"type": "string"}},
            "copy_notes": {"type": "array", "items": {"type": "string"}},
            "risk_notes": {"type": "array", "items": {"type": "string"}},
        },
        "required": [
            "overall_score",
            "copyability_score",
            "meta_us_ad_fit_score",
            "product_fit_score",
            "compliance_risk_score",
            "recommendation",
            "summary",
            "summary_zh",
        ],
    }


def _response_schema() -> dict[str, Any]:
    return build_response_schema()


def build_system_prompt() -> str:
    return (
        "You are a senior US Meta performance creative analyst. "
        "Judge copyability, ad fit, product match, and compliance risk from the video."
    )


def _clean_html_text(value: Any) -> str:
    text = re.sub(r"<[^>]+>", " ", str(value or ""))
    return re.sub(r"\s+", " ", text).strip()


def build_prompt(row: Mapping[str, Any]) -> str:
    product_url = str(row.get("product_url") or "").strip()
    product_title = str(row.get("product_title") or "").strip()
    category = str(row.get("category_l1") or "").strip()
    message = _clean_html_text(row.get("message_zh_html") or row.get("message_html"))
    return f"""
You are evaluating a short-form ecommerce video for US Meta ecosystem ads.

Goal: decide whether this material is worth copying/adapting for US Facebook, Instagram, Reels and Advantage+ style ad placement.

Product URL: {product_url}
Product title: {product_title or "-"}
Category: {category or "-"}
Meta post URL: {row.get("post_url") or "-"}
Engagement: likes={row.get("latest_likes") or 0}, comments={row.get("latest_comments") or 0}, shares={row.get("latest_shares") or 0}
Post copy: {message or "-"}

Score 0-100:
- overall_score: final ranking score for choosing the best 50 materials to copy.
- copyability_score: how directly this creative structure can be copied or closely adapted.
- meta_us_ad_fit_score: fit for US Meta ad delivery and consumer expectations.
- product_fit_score: whether the demonstrated product and product link match.
- compliance_risk_score: higher means more legal, policy, exaggerated claim, IP, medical, before-after, or unsafe-content risk.

Return concise JSON only. Prefer "copy" only when the video has a clear hook, visible product demo, low compliance risk, and enough creative clarity to brief a new ad.
Keep summary as English compatibility text for legacy exports.
Fill summary_zh with a natural Simplified Chinese interpretation for Chinese ecommerce operators.
Return winning_angles, copy_notes, and risk_notes as Simplified Chinese bullet-style strings. Keep terms such as Meta, Facebook, Instagram, Reels, SKU, and ROAS unchanged.
""".strip()


def _parse_response_payload(response: Mapping[str, Any]) -> dict[str, Any]:
    payload = response.get("json")
    if isinstance(payload, dict):
        return dict(payload)
    text = response.get("text")
    if isinstance(text, str) and text.strip():
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            return {"summary": text[:4000]}
        if isinstance(parsed, dict):
            return parsed
    return {}


def _coerce_delay_seconds(value: float | int | str | None) -> float:
    try:
        delay = float(value or 0)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, delay)


def _sleep_after_item(
    *,
    index: int,
    total: int,
    per_item_delay_seconds: float | int | str | None,
    sleep_fn: SleepFn | None,
) -> None:
    delay = _coerce_delay_seconds(per_item_delay_seconds)
    if delay <= 0 or index >= total - 1:
        return
    (sleep_fn or time.sleep)(delay)


def analyze_video_copyability(
    row: Mapping[str, Any],
    *,
    output_dir: str | Path = OUTPUT_DIR,
    user_id: int | None = None,
    compress_fn: Callable[..., str] = compress_video_for_analysis,
    invoke_fn: Callable[..., Mapping[str, Any]] = llm_client.invoke_generate,
) -> dict[str, Any]:
    post_id = int(row["hot_post_id"])
    local_path = video_localization.resolve_local_video_path(
        str(row.get("local_video_path") or ""),
        output_dir=output_dir,
    )
    if local_path is None:
        raise RuntimeError("local video file is missing or outside output directory")

    compressed_rel_path = compress_fn(
        local_path,
        post_id=post_id,
        output_dir=output_dir,
    )
    compressed_path = Path(output_dir) / compressed_rel_path
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(
            invoke_fn,
            VIDEO_COPYABILITY_USE_CASE,
            prompt=build_prompt(row),
            system=build_system_prompt(),
            media=[compressed_path],
            response_schema=build_response_schema(),
            provider_override=VIDEO_COPYABILITY_PROVIDER,
            model_override=VIDEO_COPYABILITY_MODEL,
            temperature=0.2,
            max_output_tokens=1400,
            user_id=user_id,
            billing_extra={"source": "meta_hot_posts_video_copyability"},
        )
        response = future.result(timeout=VIDEO_COPYABILITY_TIMEOUT_SECONDS)
    result = _parse_response_payload(response)
    result["provider"] = VIDEO_COPYABILITY_PROVIDER
    result["model"] = VIDEO_COPYABILITY_MODEL
    result["compressed_video_path"] = compressed_rel_path
    if response.get("usage"):
        result["usage"] = response.get("usage")
    return result


def run_pending_video_copyability_analyses(
    *,
    limit: int = DEFAULT_ANALYSIS_LIMIT,
    user_id: int | None = None,
    per_item_delay_seconds: float | int | str | None = DEFAULT_ANALYSIS_DELAY_SECONDS,
    sleep_fn: SleepFn | None = None,
    analyze_fn: Callable[..., dict[str, Any]] = analyze_video_copyability,
) -> dict[str, int]:
    queued = int(store.ensure_video_copyability_candidates() or 0)
    rows = store.next_pending_video_copyability_analyses(limit=limit)
    summary = {"queued": queued, "scanned": 0, "done": 0, "failed": 0}
    total = len(rows)
    for index, row in enumerate(rows):
        analysis_id = int(row["analysis_id"])
        summary["scanned"] += 1
        store.mark_video_copyability_running(analysis_id)
        try:
            result = analyze_fn(row, user_id=user_id)
        except Exception as exc:
            store.finish_video_copyability_analysis(
                analysis_id,
                result={},
                error_message=str(exc)[:1000],
            )
            summary["failed"] += 1
        else:
            store.finish_video_copyability_analysis(
                analysis_id,
                result=result,
                error_message=None,
            )
            summary["done"] += 1
        _sleep_after_item(
            index=index,
            total=total,
            per_item_delay_seconds=per_item_delay_seconds,
            sleep_fn=sleep_fn,
        )
    return summary
