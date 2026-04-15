"""CSK 视频分析模块：用 Gemini 3.1 Pro 对视频做深度特征锁定 + 3 个关键帧提取。"""
from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

from appcore import gemini
from pipeline.llm_util import parse_json_response

logger = logging.getLogger(__name__)

CSK_MODEL = "gemini-3.1-pro-preview"

CSK_PROMPT = """**Role & Objective**:
You are a Senior Video Analysis Expert.
Analyze the user-uploaded video and return the result as a structured JSON object.

STRICT OUTPUT RULES:
* Output ONLY a single valid JSON object.
* Output MUST start with '{' and end with '}'.
* Do NOT use markdown.
* Do NOT use code blocks or backticks.
* Do NOT include any explanation or extra text.
* The output will be parsed by a strict machine parser.
* If any rule is violated, the response is invalid.
* All fields must be present.
* If some information is not visible or not available, use an empty string "" or an empty array [] as appropriate.
* Do not fabricate information that is not visible or reasonably inferable from the video.

**Language Rules**:
1. **Analysis & Reasons**: Use **Chinese (Simplified)**.
2. **Source Material**: Keep original visible text and spoken language in the **original language**.

**Task Instructions**:

1. **Deep Analysis & Feature Locking**:
   * Watch the video frame-by-frame.
   * Identify the product as precisely as possible.
   * Lock the product's visible identity, including:
     * precise color
     * material
     * shape / structure / geometry
     * visible functional parts
     * how it is being held, used, worn, installed, or interacted with
   * Summarize the narrative, atmosphere, scene progression, and key actions.
   * In `detailed_description`, describe the scene, subject, product appearance, action flow, interaction logic, camera perspective, and environmental context in enough detail for later visual generation reference.
   * If there is visible text on screen, extract it into `video_text` in reading order.
   * If there is voiceover or speech, transcribe it as fully as possible into `voiceover`. If there is no audible speech, use an empty string.

2. **Millisecond Keyframe Extraction (Exactly 3 Frames)**:
   * Identify **exactly 3** distinct keyframes that best represent the video.
   * **Format**: `MM:SS.mmm`.
   * **Criteria**:
     * Must be the sharpest available frames with minimal motion blur.
     * Must clearly represent:
       * **Hero/Product Front View**
       * **Detail Close-up**
       * **Usage Scenario**
     * Each frame must capture a unique perspective, function, or usage meaning of the product.
     * Avoid redundant angles, repeated moments, similar compositions, transition frames, obstructed frames, noisy frames, or frames dominated by platform UI / overlays.
     * Prefer frames that are visually reusable for later cover-image generation or product scene reconstruction.
   * If the video does not contain a perfect example of one required category, select the closest valid frame that most strongly matches that category.
   * Reasons must explain clearly in Chinese why this frame is the best choice.

3. **Noise / Overlay Handling**:
   * Ignore compression artifacts, accidental blur, UI remnants, editing overlays, platform-like interface elements, or irrelevant background distractions unless they are truly part of the original creative.
   * Do not treat platform UI, watermarks, subtitles, or decorative stickers as the product itself.
   * If visible text is only decorative overlay and not part of the product, still record it in `video_text`, but do not let it dominate keyframe selection.

**Output Format**:
You MUST output the result as a valid JSON object using the following schema:

{
  "video_analysis": {
    "summary": "视频主要内容的中文概括。",
    "detailed_description": "详细描述视频中的场景、动作、氛围、物品细节、产品外观、使用方式、镜头视角与环境信息（中文）。",
    "product_features": {
      "color_desc": "Detailed description of the product color (e.g., 'Vibrant Cyan and Magenta')",
      "material_desc": "Material description (e.g., 'Translucent Plastic')"
    },
    "video_text": ["Line 1", "Line 2"],
    "voiceover": "Full transcript."
  },
  "keyframes": [
    {
      "timestamp": "MM:SS.mmm",
      "type": "Hero Shot / Front View",
      "reason": "中文理由：产品正面主体清晰，光线稳定，无遮挡，最能代表整体外观。"
    },
    {
      "timestamp": "MM:SS.mmm",
      "type": "Detail Close-up",
      "reason": "中文理由：关键细节特写，材质与做工清晰可辨，无运动模糊。"
    },
    {
      "timestamp": "MM:SS.mmm",
      "type": "Usage Scenario",
      "reason": "中文理由：真实使用场景，动作完整，功能表达明确。"
    }
  ]
}"""


def analyze_video(video_path: str | Path, *, user_id: int | None = None) -> dict:
    """对视频做 CSK 深度分析。返回 {video_analysis, keyframes, model, analyzed_at}。"""
    p = Path(video_path)
    if not p.is_file():
        raise FileNotFoundError(f"视频不存在：{p}")

    raw = gemini.generate(
        CSK_PROMPT,
        media=p,
        temperature=0.2,
        max_output_tokens=4096,
        user_id=user_id,
        service="gemini_video_analysis",
        default_model=CSK_MODEL,
    )
    data = raw if isinstance(raw, dict) else parse_json_response(raw)

    va = data.get("video_analysis") or {}
    pf = va.get("product_features") or {}
    video_text = va.get("video_text") or []
    if not isinstance(video_text, list):
        video_text = [str(video_text)]

    keyframes_raw = data.get("keyframes") or []
    keyframes: list[dict] = []
    for kf in keyframes_raw[:3]:
        if not isinstance(kf, dict):
            continue
        keyframes.append({
            "timestamp": (kf.get("timestamp") or "").strip(),
            "type": (kf.get("type") or "").strip(),
            "reason": (kf.get("reason") or "").strip(),
        })

    return {
        "video_analysis": {
            "summary": (va.get("summary") or "").strip(),
            "detailed_description": (va.get("detailed_description") or "").strip(),
            "product_features": {
                "color_desc": (pf.get("color_desc") or "").strip(),
                "material_desc": (pf.get("material_desc") or "").strip(),
            },
            "video_text": [str(t).strip() for t in video_text if str(t).strip()],
            "voiceover": (va.get("voiceover") or "").strip(),
        },
        "keyframes": keyframes,
        "model": CSK_MODEL,
        "analyzed_at": datetime.utcnow().isoformat() + "Z",
    }
