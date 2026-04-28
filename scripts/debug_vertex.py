"""Vertex AI Express Mode 全量探测：文本 / 图像 / 视频模型都试一遍。"""
from __future__ import annotations

import os
import sys
import traceback

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from appcore.llm_provider_configs import require_provider_config
from google import genai
from google.genai import types as genai_types

cfg = require_provider_config("gemini_cloud_text")
api_key = (cfg.api_key or "").strip()
project = (cfg.extra_config or {}).get("project", "")
location = (cfg.extra_config or {}).get("location", "global")
print(f"provider=gemini_cloud_text key_len={len(api_key)} project={project!r} location={location!r}")
if not api_key and not project:
    sys.exit("no gemini_cloud_text api_key/project in llm_provider_configs")

client = (
    genai.Client(vertexai=True, project=project, location=location)
    if project
    else genai.Client(vertexai=True, api_key=api_key)
)

TEXT_MODELS = [
    "gemini-3.1-pro-preview",
    "gemini-3-flash-preview",
    "gemini-3.1-flash-lite-preview",
    "gemini-2.5-flash",
]
IMAGE_MODELS = [
    "gemini-3-pro-image-preview",
    "gemini-3.1-flash-image-preview",
]


def try_text(model_id: str) -> None:
    print(f"\n[TEXT] {model_id}")
    try:
        resp = client.models.generate_content(
            model=model_id,
            contents="回复 'ok'",
            config=genai_types.GenerateContentConfig(max_output_tokens=16),
        )
        print(f"  OK -> { (resp.text or '').strip()[:60]!r}")
    except Exception as e:
        code = getattr(e, "code", None) or getattr(e, "status_code", None)
        print(f"  FAIL code={code} {type(e).__name__}: {str(e)[:200]}")


def try_image(model_id: str) -> None:
    print(f"\n[IMAGE] {model_id}")
    import base64
    # 1x1 白色 png
    tiny_png = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+ip1sAAAAASUVORK5CYII="
    )
    try:
        resp = client.models.generate_content(
            model=model_id,
            contents=[
                genai_types.Part.from_bytes(data=tiny_png, mime_type="image/png"),
                genai_types.Part.from_text(text="return the same image, no changes"),
            ],
        )
        got = None
        for cand in resp.candidates or []:
            for part in (cand.content.parts if cand.content else []) or []:
                inline = getattr(part, "inline_data", None)
                if inline and getattr(inline, "data", None):
                    got = (len(inline.data), inline.mime_type)
                    break
            if got:
                break
        print(f"  OK -> got_image={got}")
    except Exception as e:
        code = getattr(e, "code", None) or getattr(e, "status_code", None)
        print(f"  FAIL code={code} {type(e).__name__}: {str(e)[:240]}")


for m in TEXT_MODELS:
    try_text(m)
for m in IMAGE_MODELS:
    try_image(m)
