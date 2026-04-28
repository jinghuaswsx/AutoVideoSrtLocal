"""用 Vertex AI 真实生成图像并落盘，验证能否走通。"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from appcore.llm_provider_configs import require_provider_config
from google import genai
from google.genai import types as genai_types

cfg = require_provider_config("gemini_cloud_image")
api_key = (cfg.api_key or "").strip()
project = (cfg.extra_config or {}).get("project", "")
location = (cfg.extra_config or {}).get("location", "global")
if not api_key and not project:
    sys.exit("no gemini_cloud_image api_key/project in llm_provider_configs")

client = (
    genai.Client(vertexai=True, project=project, location=location)
    if project
    else genai.Client(vertexai=True, api_key=api_key)
)
out_dir = Path(__file__).resolve().parent.parent / "output" / "vertex_debug"
out_dir.mkdir(parents=True, exist_ok=True)

PROMPT_TEXT2IMG = "A cute cartoon orange tabby cat sitting on a windowsill at sunset, watercolor style"

CASES = [
    ("gemini-3-pro-image-preview",   "pro"),
    ("gemini-3.1-flash-image-preview", "flash"),
]

for model_id, tag in CASES:
    print(f"\n[{tag}] model={model_id}")
    try:
        resp = client.models.generate_content(
            model=model_id,
            contents=[genai_types.Part.from_text(text=PROMPT_TEXT2IMG)],
        )
    except Exception as e:
        print(f"  EXCEPTION {type(e).__name__}: {e}")
        continue

    cands = resp.candidates or []
    print(f"  candidates={len(cands)}")
    saved = False
    for ci, cand in enumerate(cands):
        finish = getattr(cand, "finish_reason", None)
        safety = getattr(cand, "safety_ratings", None)
        content = getattr(cand, "content", None)
        parts = (content.parts if content else []) or []
        print(f"  cand[{ci}] finish_reason={finish} parts={len(parts)} safety={safety}")
        for pi, part in enumerate(parts):
            inline = getattr(part, "inline_data", None)
            text = getattr(part, "text", None)
            if inline and getattr(inline, "data", None):
                ext = (inline.mime_type or "image/png").split("/")[-1]
                path = out_dir / f"{tag}_{ci}_{pi}.{ext}"
                path.write_bytes(inline.data)
                print(f"    part[{pi}] IMAGE bytes={len(inline.data)} mime={inline.mime_type} -> {path}")
                saved = True
            elif text:
                print(f"    part[{pi}] TEXT {text[:120]!r}")
            else:
                print(f"    part[{pi}] ??? {part}")
    if not saved:
        pf = getattr(resp, "prompt_feedback", None)
        print(f"  NO IMAGE. prompt_feedback={pf}")
