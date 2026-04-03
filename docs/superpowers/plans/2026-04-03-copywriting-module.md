# 文案创作模块实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为 AutoVideoSrt 新增独立的"文案创作"模块——上传 BGM 视频 → 自动抽帧 → LLM 生成美区 TikTok 卖货文案 → 可选 TTS 合成。

**Architecture:** 独立管线（CopywritingRunner），复用底层 TTS/compose 模块和 EventBus 机制。新增 `pipeline/keyframe.py`（抽帧）、`pipeline/copywriting.py`（LLM 文案生成）、`appcore/copywriting_runtime.py`（管线编排）。前端新增独立页面，通过 `projects.type` 字段区分项目类型。

**Tech Stack:** Flask + Jinja2 + SocketIO / MySQL / OpenAI SDK (OpenRouter & Doubao) / FFmpeg + scenedetect / ElevenLabs TTS

**Spec:** `docs/superpowers/specs/2026-04-03-copywriting-module-design.md`

---

## 文件结构

### 新增文件

| 文件 | 职责 |
|------|------|
| `db/migrations/add_copywriting.sql` | 数据库迁移：projects.type、copywriting_inputs 表、user_prompts.type |
| `pipeline/keyframe.py` | 视频关键帧抽取（scenedetect + ffmpeg） |
| `pipeline/copywriting.py` | 文案生成 LLM 调用（全文生成 + 单段重写） |
| `appcore/copywriting_runtime.py` | 文案管线编排（CopywritingRunner） |
| `web/routes/copywriting.py` | Flask 蓝图：页面路由 + API |
| `web/templates/copywriting_list.html` | 文案项目列表页 |
| `web/templates/copywriting_detail.html` | 文案创作工作页（混合式布局） |
| `web/templates/_copywriting_scripts.html` | 工作页 JavaScript（SocketIO + 编辑逻辑） |
| `web/templates/_copywriting_styles.html` | 工作页 CSS |

### 修改文件

| 文件 | 变更 |
|------|------|
| `db/schema.sql` | 追加 copywriting 相关表定义（保持 schema.sql 为最新全量） |
| `web/app.py` | 注册 copywriting 蓝图 + SocketIO 事件 |
| `web/templates/layout.html` | 导航栏新增"文案创作"菜单项 |
| `appcore/events.py` | 新增文案相关事件常量 |
| `appcore/task_state.py` | 新增 copywriting 项目的 create/update 辅助函数 |

---

## Task 1: 数据库迁移

**Files:**
- Create: `db/migrations/add_copywriting.sql`
- Modify: `db/schema.sql`

- [ ] **Step 1: 编写迁移脚本**

```sql
-- db/migrations/add_copywriting.sql
-- 文案创作模块数据库迁移

-- 1. projects 表新增 type 字段
ALTER TABLE projects ADD COLUMN type VARCHAR(20) NOT NULL DEFAULT 'translation' AFTER user_id;

-- 2. 新增 copywriting_inputs 表
CREATE TABLE IF NOT EXISTS copywriting_inputs (
    id INT AUTO_INCREMENT PRIMARY KEY,
    project_id VARCHAR(36) NOT NULL,
    product_title VARCHAR(255) DEFAULT '',
    product_image_url TEXT,
    price VARCHAR(50) DEFAULT '',
    selling_points TEXT,
    target_audience VARCHAR(255) DEFAULT '',
    extra_info TEXT,
    language VARCHAR(10) NOT NULL DEFAULT 'en',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 3. user_prompts 表新增 type 字段
ALTER TABLE user_prompts ADD COLUMN type VARCHAR(20) NOT NULL DEFAULT 'translation' AFTER user_id;
```

- [ ] **Step 2: 更新 schema.sql 追加全量定义**

在 `db/schema.sql` 的 `projects` 表定义中，在 `user_id` 之后加入 `type VARCHAR(20) NOT NULL DEFAULT 'translation'`。

在 `user_prompts` 表定义中，在 `user_id` 之后加入 `type VARCHAR(20) NOT NULL DEFAULT 'translation'`。

在文件末尾追加 `copywriting_inputs` 表的完整 CREATE TABLE 语句（同迁移脚本中的定义）。

- [ ] **Step 3: 在服务器上执行迁移**

```bash
mysql -u root -p auto_video_srt < db/migrations/add_copywriting.sql
```

- [ ] **Step 4: 提交**

```bash
git add db/migrations/add_copywriting.sql db/schema.sql
git commit -m "feat: 文案创作模块数据库迁移"
```

---

## Task 2: EventBus 扩展

**Files:**
- Modify: `appcore/events.py`

- [ ] **Step 1: 新增文案相关事件常量**

在 `appcore/events.py` 现有事件常量之后追加：

```python
# ── 文案创作事件 ──────────────────────────────────────
EVT_CW_STEP_UPDATE      = "cw_step_update"
EVT_CW_KEYFRAMES_READY  = "cw_keyframes_ready"
EVT_CW_COPY_READY       = "cw_copy_ready"
EVT_CW_SEGMENT_REWRITTEN = "cw_segment_rewritten"
EVT_CW_TTS_READY        = "cw_tts_ready"
EVT_CW_COMPOSE_READY    = "cw_compose_ready"
EVT_CW_DONE             = "cw_done"
EVT_CW_ERROR            = "cw_error"
```

- [ ] **Step 2: 提交**

```bash
git add appcore/events.py
git commit -m "feat: 新增文案创作事件常量"
```

---

## Task 3: 关键帧抽取模块

**Files:**
- Create: `pipeline/keyframe.py`

- [ ] **Step 1: 创建 keyframe.py**

```python
"""pipeline/keyframe.py
视频关键帧抽取：scenedetect 检测场景切换点，ffmpeg 抽取对应帧图片。
"""

from __future__ import annotations

import logging
import os
import subprocess

log = logging.getLogger(__name__)


def detect_scene_timestamps(video_path: str, threshold: float = 27.0) -> list[float]:
    """用 scenedetect 检测场景切换时间点（秒）。

    如果 scenedetect 不可用或视频无明显场景切换，
    回退到按固定间隔采样。
    """
    try:
        from scenedetect import open_video, SceneManager
        from scenedetect.detectors import ContentDetector
    except ImportError:
        log.warning("scenedetect 未安装，回退到固定间隔采样")
        return _fallback_uniform_timestamps(video_path)

    video = open_video(video_path)
    sm = SceneManager()
    sm.add_detector(ContentDetector(threshold=threshold))
    sm.detect_scenes(video)
    scene_list = sm.get_scene_list()

    if len(scene_list) < 2:
        log.info("场景切换点不足，回退到固定间隔采样")
        return _fallback_uniform_timestamps(video_path)

    # 取每个场景的起始时间 + 最后一个场景的中间点
    timestamps: list[float] = []
    for scene in scene_list:
        start = scene[0].get_seconds()
        timestamps.append(round(start, 3))

    return timestamps


def _fallback_uniform_timestamps(video_path: str, count: int = 6) -> list[float]:
    """均匀采样 count 个时间点。"""
    duration = _get_video_duration(video_path)
    if duration <= 0:
        return [0.0]
    step = duration / (count + 1)
    return [round(step * (i + 1), 3) for i in range(count)]


def _get_video_duration(video_path: str) -> float:
    """通过 ffprobe 获取视频时长（秒）。"""
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        video_path,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        return float(result.stdout.strip())
    except Exception:
        log.exception("ffprobe 获取时长失败")
        return 0.0


def extract_keyframes(
    video_path: str,
    output_dir: str,
    timestamps: list[float] | None = None,
    max_frames: int = 8,
    threshold: float = 27.0,
) -> list[str]:
    """抽取关键帧图片。

    Args:
        video_path: 源视频路径
        output_dir: 帧图片输出目录
        timestamps: 指定时间点（秒），为 None 则自动检测
        max_frames: 最大帧数
        threshold: scenedetect 阈值

    Returns:
        帧图片路径列表（按时间排序）
    """
    os.makedirs(output_dir, exist_ok=True)

    if timestamps is None:
        timestamps = detect_scene_timestamps(video_path, threshold=threshold)

    # 限制最大帧数：均匀采样
    if len(timestamps) > max_frames:
        step = len(timestamps) / max_frames
        timestamps = [timestamps[int(i * step)] for i in range(max_frames)]

    frame_paths: list[str] = []
    for i, ts in enumerate(timestamps):
        out_path = os.path.join(output_dir, f"frame_{i:03d}.jpg")
        cmd = [
            "ffmpeg", "-y",
            "-ss", str(ts),
            "-i", video_path,
            "-vframes", "1",
            "-q:v", "2",
            out_path,
        ]
        try:
            subprocess.run(cmd, capture_output=True, check=True)
            if os.path.exists(out_path):
                frame_paths.append(out_path)
        except subprocess.CalledProcessError:
            log.warning("抽帧失败: ts=%.3f", ts)

    log.info("抽取了 %d 帧关键帧", len(frame_paths))
    return frame_paths
```

- [ ] **Step 2: 提交**

```bash
git add pipeline/keyframe.py
git commit -m "feat: 新增视频关键帧抽取模块"
```

---

## Task 4: 文案生成 LLM 模块

**Files:**
- Create: `pipeline/copywriting.py`

- [ ] **Step 1: 创建 copywriting.py**

```python
"""pipeline/copywriting.py
文案生成：调用 LLM 生成 / 重写短视频卖货文案。
"""

from __future__ import annotations

import base64
import json
import logging
import os
import re
from typing import Any

log = logging.getLogger(__name__)

# ── 默认系统提示词 ──────────────────────────────────

DEFAULT_SYSTEM_PROMPT_EN = """\
You are an expert TikTok short-video copywriter specializing in US e-commerce ads.

**Your task:** Based on the video keyframes, product information, and product images provided, write a compelling short-video sales script for the US market. The script must match the video's visual content and the product being sold.

**Video understanding:** Carefully analyze each keyframe to understand the video's scenes, actions, mood, and pacing. Your script must align with what's happening on screen — each segment should correspond to the visual flow.

**Script structure (follow TikTok best practices):**
1. **Hook (0-3s):** An attention-grabbing opening that stops the scroll. Use curiosity, shock, relatability, or a bold claim. Must connect to what's shown in the first frames.
2. **Problem/Scene (3-8s):** Identify a pain point or set a relatable scene that the target audience experiences. Match the video's visual context.
3. **Product Reveal (8-15s):** Introduce the product naturally as the solution. Highlight key selling points that are visible in the video. Be specific — mention features shown on screen.
4. **Social Proof / Demo (15-22s):** Reinforce credibility — results, transformations, or demonstrations visible in the video. Use sensory language.
5. **CTA (last 3-5s):** Clear call-to-action. Create urgency. Direct viewers to take action.

**Style guidelines:**
- Conversational, authentic tone — sounds like a real person, not an ad
- Short punchy sentences, easy to speak aloud
- Use power words: "obsessed", "game-changer", "finally", "you need this"
- Match the energy/mood of the video (upbeat, calm, dramatic, etc.)
- Aim for 15-45 seconds total speaking time depending on video length

**Output format:** Return ONLY a JSON object with this exact structure:
{
  "segments": [
    {"label": "Hook", "text": "...", "duration_hint": 3.0},
    {"label": "Problem", "text": "...", "duration_hint": 5.0},
    {"label": "Product", "text": "...", "duration_hint": 7.0},
    {"label": "Demo", "text": "...", "duration_hint": 5.0},
    {"label": "CTA", "text": "...", "duration_hint": 3.0}
  ],
  "full_text": "Complete script as one paragraph",
  "tone": "Description of the tone used",
  "target_duration": 23
}"""

DEFAULT_SYSTEM_PROMPT_ZH = """\
你是一位专业的短视频带货文案专家，擅长为美国 TikTok 市场创作电商广告脚本。

**你的任务：** 根据提供的视频关键帧、商品信息和商品图片，撰写一段面向美国市场的短视频带货口播文案。文案必须与视频画面内容和所售商品高度匹配。

**视频理解：** 仔细分析每一帧关键画面，理解视频的场景、动作、氛围和节奏。你的文案必须与画面同步——每一段都要对应视频的视觉流程。

**文案结构（遵循 TikTok 最佳实践）：**
1. **Hook 开头（0-3秒）：** 抓眼球的开场，让用户停止滑动。用好奇心、冲击感、共鸣或大胆主张。必须关联开头几帧画面。
2. **痛点/场景（3-8秒）：** 点出目标用户的痛点或建立一个有共鸣的场景，匹配视频画面。
3. **产品展示（8-15秒）：** 自然引入产品作为解决方案。突出视频中可见的核心卖点，要具体——提及画面中展示的功能特点。
4. **信任背书/演示（15-22秒）：** 强化可信度——视频中可见的效果、变化或演示。使用感官化语言。
5. **CTA 行动号召（最后3-5秒）：** 清晰的行动指令，制造紧迫感，引导用户下单。

**风格要求：**
- 口语化、真实自然的语气——听起来像真人分享，不像广告
- 短句为主，朗朗上口，适合口播
- 善用有感染力的词汇
- 匹配视频的情绪和节奏（活力、舒缓、震撼等）
- 根据视频时长，口播总时长控制在 15-45 秒

**输出格式：** 仅返回如下 JSON 对象：
{
  "segments": [
    {"label": "Hook", "text": "...", "duration_hint": 3.0},
    {"label": "Problem", "text": "...", "duration_hint": 5.0},
    {"label": "Product", "text": "...", "duration_hint": 7.0},
    {"label": "Demo", "text": "...", "duration_hint": 5.0},
    {"label": "CTA", "text": "...", "duration_hint": 3.0}
  ],
  "full_text": "完整文案拼接为一段话",
  "tone": "语气描述",
  "target_duration": 23
}"""

REWRITE_SEGMENT_PROMPT_EN = """\
You are rewriting ONE segment of a TikTok sales script. Keep the same style and flow as the rest of the script.

Full script context:
{full_text}

Segment to rewrite (label: {label}):
"{original_text}"

{user_instruction}

Return ONLY a JSON object:
{{"label": "{label}", "text": "rewritten text here", "duration_hint": {duration_hint}}}"""

REWRITE_SEGMENT_PROMPT_ZH = """\
你正在重写一段 TikTok 带货文案中的某一段。请保持与其余文案一致的风格和节奏。

完整文案上下文：
{full_text}

需要重写的段落（标签：{label}）：
"{original_text}"

{user_instruction}

仅返回如下 JSON 对象：
{{"label": "{label}", "text": "重写后的文案", "duration_hint": {duration_hint}}}"""

# ── JSON Schema ──────────────────────────────────────

COPYWRITING_RESPONSE_FORMAT = {
    "type": "json_schema",
    "json_schema": {
        "name": "copywriting_result",
        "strict": True,
        "schema": {
            "type": "object",
            "required": ["segments", "full_text", "tone", "target_duration"],
            "additionalProperties": False,
            "properties": {
                "segments": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "required": ["label", "text", "duration_hint"],
                        "additionalProperties": False,
                        "properties": {
                            "label": {"type": "string"},
                            "text": {"type": "string"},
                            "duration_hint": {"type": "number"},
                        },
                    },
                },
                "full_text": {"type": "string"},
                "tone": {"type": "string"},
                "target_duration": {"type": "number"},
            },
        },
    },
}


# ── 辅助函数 ──────────────────────────────────────────

def _image_to_base64_url(image_path: str) -> str:
    """将本地图片转为 base64 data URL。"""
    with open(image_path, "rb") as f:
        data = base64.b64encode(f.read()).decode()
    ext = os.path.splitext(image_path)[1].lower()
    mime = {"jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
            ".webp": "image/webp", ".gif": "image/gif"}.get(ext, "image/jpeg")
    return f"data:{mime};base64,{data}"


def _parse_json_content(raw: str) -> dict:
    """解析 LLM 返回的 JSON（兼容 markdown code block 包裹）。"""
    text = raw.strip()
    m = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if m:
        text = m.group(1).strip()
    return json.loads(text)


def _build_product_text(inputs: dict) -> str:
    """将结构化商品信息拼成文本块。"""
    parts: list[str] = []
    if inputs.get("product_title"):
        parts.append(f"Product: {inputs['product_title']}")
    if inputs.get("price"):
        parts.append(f"Price: {inputs['price']}")
    if inputs.get("selling_points"):
        sp = inputs["selling_points"]
        if isinstance(sp, str):
            try:
                sp = json.loads(sp)
            except json.JSONDecodeError:
                sp = [sp]
        parts.append("Key selling points:\n" + "\n".join(f"- {p}" for p in sp))
    if inputs.get("target_audience"):
        parts.append(f"Target audience: {inputs['target_audience']}")
    if inputs.get("extra_info"):
        parts.append(f"Additional info: {inputs['extra_info']}")
    return "\n".join(parts)


def _supports_vision(provider: str) -> bool:
    """判断 provider 是否支持 vision（图片输入）。"""
    return provider != "doubao"


# ── 主函数 ─────────────────────────────────────────────

def generate_copy(
    keyframe_paths: list[str],
    product_inputs: dict,
    provider: str = "openrouter",
    user_id: int | None = None,
    custom_system_prompt: str | None = None,
    language: str = "en",
) -> dict:
    """生成短视频文案。

    Args:
        keyframe_paths: 关键帧图片路径列表
        product_inputs: 商品信息 dict（product_title, price, selling_points 等）
        provider: LLM provider（"openrouter" 或 "doubao"）
        user_id: 用户 ID（用于解析 API key）
        custom_system_prompt: 自定义系统提示词，为 None 则用默认
        language: 输出语言 "en" 或 "zh"

    Returns:
        dict: {segments, full_text, tone, target_duration}
    """
    from pipeline.translate import _resolve_provider_config

    client, model = _resolve_provider_config(provider, user_id=user_id)

    # 系统提示词
    if custom_system_prompt:
        system_prompt = custom_system_prompt
    elif language == "zh":
        system_prompt = DEFAULT_SYSTEM_PROMPT_ZH
    else:
        system_prompt = DEFAULT_SYSTEM_PROMPT_EN

    # 构建用户消息内容
    content: list[dict[str, Any]] = []

    # 图片（仅 vision 支持的模型）
    use_vision = _supports_vision(provider) and keyframe_paths
    if use_vision:
        content.append({"type": "text", "text": "Video keyframes (in chronological order):"})
        for path in keyframe_paths:
            content.append({
                "type": "image_url",
                "image_url": {"url": _image_to_base64_url(path)},
            })

    # 商品主图
    product_image = product_inputs.get("product_image_url") or product_inputs.get("product_image_path")
    if use_vision and product_image and os.path.isfile(product_image):
        content.append({"type": "text", "text": "Product image:"})
        content.append({
            "type": "image_url",
            "image_url": {"url": _image_to_base64_url(product_image)},
        })

    # 商品文本信息
    product_text = _build_product_text(product_inputs)
    if not use_vision:
        product_text = (
            "[Note: Current model does not support image input. "
            "Generating copy based on text information only.]\n\n" + product_text
        )
    content.append({"type": "text", "text": product_text})

    # 语言指令
    if language == "zh":
        content.append({"type": "text", "text": "请用中文撰写文案。"})
    else:
        content.append({"type": "text", "text": "Write the script in English for the US market."})

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": content},
    ]

    extra_kwargs: dict[str, Any] = {"temperature": 0.7, "max_tokens": 4096}
    if provider == "openrouter":
        extra_kwargs["extra_body"] = {"plugins": [{"id": "response-healing"}]}
    extra_kwargs["response_format"] = COPYWRITING_RESPONSE_FORMAT

    log.info("调用 LLM 生成文案: provider=%s, model=%s, images=%d",
             provider, model, len(keyframe_paths) if use_vision else 0)

    response = client.chat.completions.create(
        model=model,
        messages=messages,
        **extra_kwargs,
    )

    raw = response.choices[0].message.content
    result = _parse_json_content(raw)

    # 补充 index
    for i, seg in enumerate(result.get("segments", [])):
        seg["index"] = i

    log.info("文案生成完成: %d 段, 预计时长 %ds",
             len(result.get("segments", [])), result.get("target_duration", 0))
    return result


def rewrite_segment(
    full_text: str,
    segment: dict,
    user_instruction: str = "",
    provider: str = "openrouter",
    user_id: int | None = None,
    language: str = "en",
) -> dict:
    """重写文案的某一段。

    Args:
        full_text: 完整文案文本（上下文）
        segment: 要重写的段落 dict（label, text, duration_hint）
        user_instruction: 用户的修改要求
        provider: LLM provider
        user_id: 用户 ID
        language: 语言

    Returns:
        dict: {label, text, duration_hint}
    """
    from pipeline.translate import _resolve_provider_config

    client, model = _resolve_provider_config(provider, user_id=user_id)

    template = REWRITE_SEGMENT_PROMPT_ZH if language == "zh" else REWRITE_SEGMENT_PROMPT_EN
    if not user_instruction:
        user_instruction = "请重写这一段，使其更有吸引力。" if language == "zh" else "Rewrite to be more engaging."

    prompt = template.format(
        full_text=full_text,
        label=segment["label"],
        original_text=segment["text"],
        duration_hint=segment.get("duration_hint", 3.0),
        user_instruction=f"User request: {user_instruction}",
    )

    response = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.7,
        max_tokens=1024,
    )

    raw = response.choices[0].message.content
    return _parse_json_content(raw)
```

- [ ] **Step 2: 提交**

```bash
git add pipeline/copywriting.py
git commit -m "feat: 新增文案生成 LLM 模块"
```

---

## Task 5: Task State 扩展

**Files:**
- Modify: `appcore/task_state.py`

- [ ] **Step 1: 新增 copywriting 项目创建函数**

在 `appcore/task_state.py` 文件末尾追加以下函数：

```python
def create_copywriting(task_id: str, video_path: str, task_dir: str,
                       original_filename: str, user_id: int) -> dict:
    """创建文案创作项目的初始状态。"""
    task = {
        "id": task_id,
        "type": "copywriting",
        "status": "uploaded",
        "video_path": video_path,
        "task_dir": task_dir,
        "original_filename": original_filename,
        "steps": {
            "keyframe": "pending",
            "copywrite": "pending",
            "tts": "pending",
            "compose": "pending",
        },
        "step_messages": {},
        "keyframes": [],
        "copy": {},
        "copy_history": [],
        "voice_id": None,
        "source_tos_key": "",
        "source_object_info": {},
        "tos_uploads": {},
        "result": {},
        "artifacts": {},
        "preview_files": {},
        "_user_id": user_id,
        "display_name": "",
    }
    _tasks[task_id] = task
    _sync_task_to_db(task_id)
    return task


def set_keyframes(task_id: str, keyframes: list[str]) -> None:
    """设置关键帧路径列表。"""
    task = get(task_id)
    if task:
        task["keyframes"] = keyframes
        _sync_task_to_db(task_id)


def set_copy(task_id: str, copy_data: dict) -> None:
    """设置生成的文案数据，并追加到历史。"""
    task = get(task_id)
    if task:
        task["copy"] = copy_data
        task.setdefault("copy_history", []).append(copy_data)
        _sync_task_to_db(task_id)


def update_copy_segment(task_id: str, index: int, segment: dict) -> None:
    """更新文案中的某一段。"""
    task = get(task_id)
    if task and task.get("copy") and 0 <= index < len(task["copy"].get("segments", [])):
        task["copy"]["segments"][index] = segment
        # 重建 full_text
        task["copy"]["full_text"] = " ".join(
            s["text"] for s in task["copy"]["segments"]
        )
        _sync_task_to_db(task_id)
```

- [ ] **Step 2: 提交**

```bash
git add appcore/task_state.py
git commit -m "feat: task_state 新增文案创作状态管理函数"
```

---

## Task 6: 文案管线编排

**Files:**
- Create: `appcore/copywriting_runtime.py`

- [ ] **Step 1: 创建 CopywritingRunner**

```python
"""appcore/copywriting_runtime.py
文案创作管线编排器。

管线步骤：keyframe → copywrite → [人工确认] → tts → compose
"""

from __future__ import annotations

import json
import logging
import os

from appcore.events import (
    EventBus, Event,
    EVT_CW_STEP_UPDATE, EVT_CW_KEYFRAMES_READY, EVT_CW_COPY_READY,
    EVT_CW_TTS_READY, EVT_CW_COMPOSE_READY, EVT_CW_DONE, EVT_CW_ERROR,
)
from appcore import task_state
from appcore.api_keys import resolve_key, resolve_extra

log = logging.getLogger(__name__)


class CopywritingRunner:
    """文案创作管线运行器。"""

    def __init__(self, bus: EventBus, user_id: int | None = None):
        self._bus = bus
        self._user_id = user_id

    # ── 事件辅助 ─────────────────────────────────────

    def _emit(self, task_id: str, event_type: str, payload: dict | None = None):
        self._bus.publish(Event(type=event_type, task_id=task_id,
                                payload=payload or {}))

    def _set_step(self, task_id: str, step: str, status: str, message: str = ""):
        task_state.set_step(task_id, step, status)
        if message:
            task_state.set_step_message(task_id, step, message)
        self._emit(task_id, EVT_CW_STEP_UPDATE, {
            "step": step, "status": status, "message": message,
        })

    # ── 公开接口 ─────────────────────────────────────

    def start(self, task_id: str):
        """启动管线：keyframe → copywrite，然后等待用户确认。"""
        task = task_state.get(task_id)
        if not task:
            return
        task_state.update(task_id, status="running")
        try:
            self._step_keyframe(task_id)
            self._step_copywrite(task_id)
        except Exception:
            log.exception("文案管线异常: %s", task_id)
            task_state.update(task_id, status="error")
            self._emit(task_id, EVT_CW_ERROR, {"message": "管线执行失败"})

    def generate_copy(self, task_id: str):
        """单独触发文案生成（重新生成）。"""
        try:
            self._step_copywrite(task_id)
        except Exception:
            log.exception("文案生成异常: %s", task_id)
            self._set_step(task_id, "copywrite", "error", "文案生成失败")
            self._emit(task_id, EVT_CW_ERROR, {"message": "文案生成失败"})

    def start_tts_compose(self, task_id: str):
        """用户确认文案后，触发 TTS → 合成。"""
        task = task_state.get(task_id)
        if not task:
            return
        try:
            self._step_tts(task_id)
            self._step_compose(task_id)
            task_state.update(task_id, status="done")
            self._emit(task_id, EVT_CW_DONE, {})
        except Exception:
            log.exception("TTS/合成异常: %s", task_id)
            task_state.update(task_id, status="error")
            self._emit(task_id, EVT_CW_ERROR, {"message": "TTS/合成失败"})

    # ── 管线步骤 ─────────────────────────────────────

    def _step_keyframe(self, task_id: str):
        from pipeline.keyframe import extract_keyframes

        self._set_step(task_id, "keyframe", "running", "正在抽取关键帧...")
        task = task_state.get(task_id)
        video_path = task["video_path"]
        task_dir = task["task_dir"]
        keyframe_dir = os.path.join(task_dir, "keyframes")

        frame_paths = extract_keyframes(video_path, keyframe_dir)
        task_state.set_keyframes(task_id, frame_paths)
        self._set_step(task_id, "keyframe", "done",
                       f"已抽取 {len(frame_paths)} 帧关键帧")
        self._emit(task_id, EVT_CW_KEYFRAMES_READY, {
            "keyframes": frame_paths,
            "count": len(frame_paths),
        })

    def _step_copywrite(self, task_id: str):
        from pipeline.copywriting import generate_copy

        self._set_step(task_id, "copywrite", "running", "正在生成文案...")
        task = task_state.get(task_id)
        keyframes = task.get("keyframes", [])

        # 从数据库读取商品信息
        product_inputs = self._load_product_inputs(task_id)
        language = product_inputs.get("language", "en")

        # 解析 provider
        provider = self._resolve_provider()

        # 解析用户自定义提示词
        custom_prompt = self._load_user_prompt(task_id, language)

        result = generate_copy(
            keyframe_paths=keyframes,
            product_inputs=product_inputs,
            provider=provider,
            user_id=self._user_id,
            custom_system_prompt=custom_prompt,
            language=language,
        )

        task_state.set_copy(task_id, result)
        self._set_step(task_id, "copywrite", "done",
                       f"文案生成完成: {len(result.get('segments', []))} 段")
        self._emit(task_id, EVT_CW_COPY_READY, {"copy": result})

    def _step_tts(self, task_id: str):
        from pipeline.tts import generate_full_audio, get_voice_by_id, get_default_voice

        self._set_step(task_id, "tts", "running", "正在生成语音...")
        task = task_state.get(task_id)
        task_dir = task["task_dir"]
        copy_data = task.get("copy", {})

        # 构建 TTS segments
        tts_segments = []
        for seg in copy_data.get("segments", []):
            tts_segments.append({
                "index": seg.get("index", 0),
                "tts_text": seg["text"],
            })

        # 获取 voice
        voice_id = task.get("voice_id")
        if voice_id:
            voice = get_voice_by_id(voice_id, self._user_id)
        else:
            voice = get_default_voice(self._user_id)

        elevenlabs_key = resolve_key(self._user_id, "elevenlabs")

        result = generate_full_audio(
            segments=tts_segments,
            voice_id=voice["elevenlabs_voice_id"],
            output_dir=task_dir,
            variant="copywriting",
            elevenlabs_api_key=elevenlabs_key,
        )

        task_state.update(task_id, tts_audio_path=result["full_audio_path"])
        task_state.set_artifact(task_id, "tts", {
            "audio_path": result["full_audio_path"],
            "segments": result["segments"],
        })
        self._set_step(task_id, "tts", "done", "语音生成完成")
        self._emit(task_id, EVT_CW_TTS_READY, {
            "audio_path": result["full_audio_path"],
        })

    def _step_compose(self, task_id: str):
        from pipeline.compose import compose_video

        self._set_step(task_id, "compose", "running", "正在合成视频...")
        task = task_state.get(task_id)
        video_path = task["video_path"]
        task_dir = task["task_dir"]
        tts_audio_path = task.get("tts_audio_path", "")

        result = compose_video(
            video_path=video_path,
            tts_audio_path=tts_audio_path,
            srt_path=None,
            output_dir=task_dir,
            subtitle_position="bottom",
            timeline_manifest=None,
            variant="copywriting",
        )

        task_state.update(task_id, result=result)
        self._set_step(task_id, "compose", "done", "视频合成完成")
        self._emit(task_id, EVT_CW_COMPOSE_READY, {"result": result})

    # ── 内部辅助 ─────────────────────────────────────

    def _load_product_inputs(self, task_id: str) -> dict:
        """从 copywriting_inputs 表加载商品信息。"""
        from appcore.db import get_connection
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT product_title, product_image_url, price, "
                    "selling_points, target_audience, extra_info, language "
                    "FROM copywriting_inputs WHERE project_id = %s",
                    (task_id,),
                )
                row = cur.fetchone()
                if not row:
                    return {"language": "en"}
                return dict(row)
        finally:
            conn.close()

    def _resolve_provider(self) -> str:
        """解析用户偏好的 LLM provider。"""
        try:
            extra = resolve_extra(self._user_id, "translate_preference")
            if extra and extra.get("provider"):
                return extra["provider"]
        except Exception:
            pass
        return "openrouter"

    def _load_user_prompt(self, task_id: str, language: str) -> str | None:
        """加载用户选择的文案提示词，返回 None 则用默认。"""
        # 优先从任务状态中读取 prompt_id
        task = task_state.get(task_id)
        prompt_id = task.get("prompt_id") if task else None
        if not prompt_id:
            return None

        from appcore.db import get_connection
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT prompt_text, prompt_text_zh FROM user_prompts "
                    "WHERE id = %s AND user_id = %s AND type = 'copywriting'",
                    (prompt_id, self._user_id),
                )
                row = cur.fetchone()
                if not row:
                    return None
                if language == "zh" and row.get("prompt_text_zh"):
                    return row["prompt_text_zh"]
                return row.get("prompt_text")
        finally:
            conn.close()
```

- [ ] **Step 2: 提交**

```bash
git add appcore/copywriting_runtime.py
git commit -m "feat: 新增文案创作管线编排器 CopywritingRunner"
```

---

## Task 7: Flask 路由

**Files:**
- Create: `web/routes/copywriting.py`

- [ ] **Step 1: 创建蓝图文件**

```python
"""web/routes/copywriting.py
文案创作模块 Flask 蓝图：页面路由 + API。
"""

from __future__ import annotations

import json
import os
import uuid

import eventlet
from flask import Blueprint, render_template, request, jsonify, send_file
from flask_login import login_required, current_user

from appcore import task_state
from appcore.copywriting_runtime import CopywritingRunner
from appcore.events import EventBus
from appcore.db import get_connection
from config import UPLOAD_DIR, OUTPUT_DIR

bp = Blueprint("copywriting", __name__)


# ── 页面路由 ──────────────────────────────────────────

@bp.route("/copywriting")
@login_required
def list_page():
    """文案项目列表页。"""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, display_name, original_filename, thumbnail_path, "
                "status, created_at, expires_at "
                "FROM projects "
                "WHERE user_id = %s AND type = 'copywriting' AND deleted_at IS NULL "
                "ORDER BY created_at DESC",
                (current_user.id,),
            )
            projects = cur.fetchall()
    finally:
        conn.close()
    return render_template("copywriting_list.html", projects=projects)


@bp.route("/copywriting/<task_id>")
@login_required
def detail_page(task_id: str):
    """文案创作工作页。"""
    task = task_state.get(task_id)
    if not task or task.get("_user_id") != current_user.id:
        return "Not found", 404

    # 加载商品信息
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM copywriting_inputs WHERE project_id = %s",
                (task_id,),
            )
            inputs = cur.fetchone() or {}
    finally:
        conn.close()

    return render_template("copywriting_detail.html",
                           task=task, inputs=inputs, task_id=task_id)


# ── API 路由 ──────────────────────────────────────────

@bp.route("/api/copywriting/upload", methods=["POST"])
@login_required
def upload():
    """上传视频 + 商品信息，创建文案项目并启动抽帧。"""
    file = request.files.get("video")
    if not file or not file.filename:
        return jsonify(error="请上传视频文件"), 400

    task_id = str(uuid.uuid4())
    task_dir = os.path.join(OUTPUT_DIR, task_id)
    os.makedirs(task_dir, exist_ok=True)

    # 保存视频
    video_filename = file.filename
    video_path = os.path.join(UPLOAD_DIR, f"{task_id}_{video_filename}")
    file.save(video_path)

    # 生成缩略图
    thumbnail_path = _extract_thumbnail(video_path, task_dir)

    # 创建任务状态
    task = task_state.create_copywriting(
        task_id=task_id,
        video_path=video_path,
        task_dir=task_dir,
        original_filename=video_filename,
        user_id=current_user.id,
    )

    # 解析显示名
    display_name = os.path.splitext(video_filename)[0]
    task_state.update(task_id, display_name=display_name)

    # 写入数据库
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO projects "
                "(id, user_id, type, original_filename, display_name, "
                "thumbnail_path, status, task_dir, state_json, "
                "created_at, expires_at) "
                "VALUES (%s, %s, 'copywriting', %s, %s, %s, 'uploaded', %s, %s, "
                "NOW(), DATE_ADD(NOW(), INTERVAL 48 HOUR))",
                (task_id, current_user.id, video_filename, display_name,
                 thumbnail_path, task_dir, json.dumps(task, ensure_ascii=False)),
            )

            # 保存商品信息
            selling_points = request.form.get("selling_points", "")
            cur.execute(
                "INSERT INTO copywriting_inputs "
                "(project_id, product_title, price, selling_points, "
                "target_audience, extra_info, language) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                (task_id,
                 request.form.get("product_title", ""),
                 request.form.get("price", ""),
                 selling_points,
                 request.form.get("target_audience", ""),
                 request.form.get("extra_info", ""),
                 request.form.get("language", "en")),
            )
        conn.commit()
    finally:
        conn.close()

    # 处理商品主图上传
    product_image = request.files.get("product_image")
    if product_image and product_image.filename:
        img_path = os.path.join(task_dir, "product_image.jpg")
        product_image.save(img_path)
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE copywriting_inputs SET product_image_url = %s "
                    "WHERE project_id = %s",
                    (img_path, task_id),
                )
            conn.commit()
        finally:
            conn.close()

    # 后台启动管线（keyframe → copywrite）
    from web.extensions import socketio
    bus = EventBus()
    _subscribe_socketio(bus, socketio)
    runner = CopywritingRunner(bus, user_id=current_user.id)
    eventlet.spawn(runner.start, task_id)

    return jsonify(task_id=task_id), 201


@bp.route("/api/copywriting/<task_id>/inputs", methods=["PUT"])
@login_required
def update_inputs(task_id: str):
    """更新商品信息。"""
    data = request.get_json()
    if not data:
        return jsonify(error="缺少数据"), 400

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            fields = []
            values = []
            for key in ("product_title", "price", "selling_points",
                        "target_audience", "extra_info", "language"):
                if key in data:
                    fields.append(f"{key} = %s")
                    values.append(data[key])
            if fields:
                values.append(task_id)
                cur.execute(
                    f"UPDATE copywriting_inputs SET {', '.join(fields)} "
                    "WHERE project_id = %s",
                    values,
                )
            conn.commit()
    finally:
        conn.close()
    return jsonify(ok=True)


@bp.route("/api/copywriting/<task_id>/generate", methods=["POST"])
@login_required
def generate(task_id: str):
    """触发文案生成（首次或重新生成）。"""
    task = task_state.get(task_id)
    if not task:
        return jsonify(error="任务不存在"), 404

    # 可选：前端传入 prompt_id
    data = request.get_json(silent=True) or {}
    if data.get("prompt_id"):
        task_state.update(task_id, prompt_id=data["prompt_id"])

    from web.extensions import socketio
    bus = EventBus()
    _subscribe_socketio(bus, socketio)
    runner = CopywritingRunner(bus, user_id=current_user.id)
    eventlet.spawn(runner.generate_copy, task_id)

    return jsonify(ok=True)


@bp.route("/api/copywriting/<task_id>/rewrite-segment", methods=["POST"])
@login_required
def rewrite_segment(task_id: str):
    """单段重写。"""
    data = request.get_json()
    if not data or "index" not in data:
        return jsonify(error="缺少 index"), 400

    task = task_state.get(task_id)
    if not task or not task.get("copy"):
        return jsonify(error="文案未生成"), 400

    segments = task["copy"].get("segments", [])
    idx = data["index"]
    if idx < 0 or idx >= len(segments):
        return jsonify(error="index 超出范围"), 400

    from pipeline.copywriting import rewrite_segment as _rewrite
    from appcore.events import EVT_CW_SEGMENT_REWRITTEN

    # 解析 provider
    provider = "openrouter"
    try:
        from appcore.api_keys import resolve_extra
        extra = resolve_extra(current_user.id, "translate_preference")
        if extra and extra.get("provider"):
            provider = extra["provider"]
    except Exception:
        pass

    language = "en"
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT language FROM copywriting_inputs WHERE project_id = %s",
                (task_id,),
            )
            row = cur.fetchone()
            if row:
                language = row["language"]
    finally:
        conn.close()

    new_seg = _rewrite(
        full_text=task["copy"].get("full_text", ""),
        segment=segments[idx],
        user_instruction=data.get("instruction", ""),
        provider=provider,
        user_id=current_user.id,
        language=language,
    )

    new_seg["index"] = idx
    task_state.update_copy_segment(task_id, idx, new_seg)

    return jsonify(segment=new_seg)


@bp.route("/api/copywriting/<task_id>/segments", methods=["PUT"])
@login_required
def save_segments(task_id: str):
    """保存用户编辑后的文案。"""
    data = request.get_json()
    if not data or "segments" not in data:
        return jsonify(error="缺少 segments"), 400

    task = task_state.get(task_id)
    if not task:
        return jsonify(error="任务不存在"), 404

    copy_data = task.get("copy", {})
    copy_data["segments"] = data["segments"]
    copy_data["full_text"] = " ".join(s["text"] for s in data["segments"])
    task_state.set_copy(task_id, copy_data)

    return jsonify(ok=True)


@bp.route("/api/copywriting/<task_id>/tts", methods=["POST"])
@login_required
def start_tts(task_id: str):
    """触发 TTS + 合成。"""
    task = task_state.get(task_id)
    if not task:
        return jsonify(error="任务不存在"), 404

    # 可选：前端传入 voice_id
    data = request.get_json(silent=True) or {}
    if data.get("voice_id"):
        task_state.update(task_id, voice_id=data["voice_id"])

    from web.extensions import socketio
    bus = EventBus()
    _subscribe_socketio(bus, socketio)
    runner = CopywritingRunner(bus, user_id=current_user.id)
    eventlet.spawn(runner.start_tts_compose, task_id)

    return jsonify(ok=True)


@bp.route("/api/copywriting/<task_id>/download/<file_type>")
@login_required
def download(task_id: str, file_type: str):
    """下载产物。"""
    task = task_state.get(task_id)
    if not task:
        return jsonify(error="任务不存在"), 404

    if file_type == "copy":
        # 导出纯文案文本
        copy_data = task.get("copy", {})
        text = copy_data.get("full_text", "")
        segments_text = "\n\n".join(
            f"[{s.get('label', '')}]\n{s['text']}"
            for s in copy_data.get("segments", [])
        )
        content = segments_text or text
        return content, 200, {
            "Content-Type": "text/plain; charset=utf-8",
            "Content-Disposition": f"attachment; filename={task_id}_copy.txt",
        }

    result = task.get("result", {})
    path = result.get(file_type)  # "soft_video", "hard_video", "srt"
    if not path or not os.path.isfile(path):
        return jsonify(error="文件不存在"), 404
    return send_file(path, as_attachment=True)


@bp.route("/api/copywriting/<task_id>/keyframe/<int:index>")
@login_required
def get_keyframe(task_id: str, index: int):
    """获取关键帧图片。"""
    task = task_state.get(task_id)
    if not task:
        return jsonify(error="任务不存在"), 404

    keyframes = task.get("keyframes", [])
    if index < 0 or index >= len(keyframes):
        return jsonify(error="帧不存在"), 404

    return send_file(keyframes[index])


@bp.route("/api/copywriting/<task_id>/artifact/<name>")
@login_required
def get_artifact(task_id: str, name: str):
    """获取中间产物（音频预览等）。"""
    task = task_state.get(task_id)
    if not task:
        return jsonify(error="任务不存在"), 404

    artifacts = task.get("artifacts", {})
    if name == "tts_audio":
        tts = artifacts.get("tts", {})
        audio_path = tts.get("audio_path")
        if audio_path and os.path.isfile(audio_path):
            return send_file(audio_path)
    elif name == "video":
        result = task.get("result", {})
        video_path = result.get("soft_video")
        if video_path and os.path.isfile(video_path):
            return send_file(video_path)

    return jsonify(error="产物不存在"), 404


# ── 辅助函数 ──────────────────────────────────────────

def _extract_thumbnail(video_path: str, task_dir: str) -> str | None:
    """抽取视频第一帧作为缩略图。"""
    import subprocess
    thumb_path = os.path.join(task_dir, "thumbnail.jpg")
    try:
        subprocess.run([
            "ffmpeg", "-y", "-i", video_path,
            "-vframes", "1", "-q:v", "5", thumb_path,
        ], capture_output=True, check=True)
        return thumb_path if os.path.exists(thumb_path) else None
    except Exception:
        return None


def _subscribe_socketio(bus: EventBus, socketio):
    """将 EventBus 事件转发到 SocketIO。"""
    def handler(event):
        socketio.emit(event.type, {
            "task_id": event.task_id,
            **event.payload,
        }, room=event.task_id)
    bus.subscribe(handler)
```

- [ ] **Step 2: 提交**

```bash
git add web/routes/copywriting.py
git commit -m "feat: 新增文案创作 Flask 蓝图"
```

---

## Task 8: 蓝图注册 & 导航更新

**Files:**
- Modify: `web/app.py`
- Modify: `web/templates/layout.html`

- [ ] **Step 1: 在 web/app.py 中注册蓝图**

在现有蓝图注册代码块中追加：

```python
from web.routes.copywriting import bp as copywriting_bp
app.register_blueprint(copywriting_bp)
```

在 SocketIO 事件处理部分追加：

```python
@socketio.on("join_copywriting_task")
def on_join_copywriting(data):
    task_id = data.get("task_id")
    if task_id:
        join_room(task_id)
```

- [ ] **Step 2: 在 layout.html 导航栏追加菜单项**

在现有 "Projects" 导航链接之后追加：

```html
<a href="/copywriting" class="nav-link{% if request.path.startswith('/copywriting') %} active{% endif %}">
    <span class="nav-icon">✍️</span>
    <span class="nav-label">文案创作</span>
</a>
```

- [ ] **Step 3: 提交**

```bash
git add web/app.py web/templates/layout.html
git commit -m "feat: 注册文案创作蓝图并添加导航菜单"
```

---

## Task 9: 文案项目列表页模板

**Files:**
- Create: `web/templates/copywriting_list.html`

- [ ] **Step 1: 创建列表页模板**

模板结构复用 `projects.html` 的模式（网格/列表切换、卡片布局、状态徽标），但过滤条件为 `type='copywriting'`。

```html
{% extends "layout.html" %}
{% block title %}文案创作{% endblock %}
{% block content %}
<style>
    .cw-header { display:flex; justify-content:space-between; align-items:center; margin-bottom:20px; }
    .cw-header h2 { margin:0; font-size:1.4rem; }
    .btn-new { background:#4f46e5; color:#fff; border:none; padding:8px 20px; border-radius:8px; cursor:pointer; font-size:14px; text-decoration:none; }
    .btn-new:hover { background:#4338ca; }
    .cw-grid { display:grid; grid-template-columns:repeat(auto-fill, minmax(220px, 1fr)); gap:16px; }
    .cw-card { border:1px solid var(--border); border-radius:10px; overflow:hidden; background:var(--card-bg); cursor:pointer; transition:box-shadow .2s; }
    .cw-card:hover { box-shadow:0 2px 12px rgba(0,0,0,.1); }
    .cw-card-thumb { width:100%; aspect-ratio:16/9; object-fit:cover; background:#111; }
    .cw-card-body { padding:12px; }
    .cw-card-title { font-weight:600; font-size:14px; margin-bottom:4px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
    .cw-card-meta { font-size:12px; color:var(--text-muted); display:flex; justify-content:space-between; align-items:center; }
    .badge { padding:2px 8px; border-radius:10px; font-size:11px; font-weight:500; }
    .badge-draft { background:#f3f4f6; color:#6b7280; }
    .badge-done { background:#d1fae5; color:#065f46; }
    .badge-running { background:#ede9fe; color:#5b21b6; }
    .badge-error { background:#fee2e2; color:#991b1b; }
    .empty-state { text-align:center; padding:60px 20px; color:var(--text-muted); }
</style>

<div class="cw-header">
    <h2>文案创作</h2>
    <a href="#" class="btn-new" onclick="openUploadModal(); return false;">新建文案</a>
</div>

{% if projects %}
<div class="cw-grid">
    {% for p in projects %}
    <div class="cw-card" onclick="location.href='/copywriting/{{ p.id }}'">
        {% if p.thumbnail_path %}
        <img class="cw-card-thumb" src="/api/copywriting/{{ p.id }}/artifact/thumbnail" alt="">
        {% else %}
        <div class="cw-card-thumb" style="display:flex;align-items:center;justify-content:center;color:#666;">无缩略图</div>
        {% endif %}
        <div class="cw-card-body">
            <div class="cw-card-title">{{ p.display_name or p.original_filename }}</div>
            <div class="cw-card-meta">
                <span>{{ p.created_at.strftime('%m-%d %H:%M') if p.created_at else '' }}</span>
                {% if p.status == 'done' %}
                <span class="badge badge-done">已完成</span>
                {% elif p.status == 'running' %}
                <span class="badge badge-running">生成中</span>
                {% elif p.status == 'error' %}
                <span class="badge badge-error">失败</span>
                {% else %}
                <span class="badge badge-draft">草稿</span>
                {% endif %}
            </div>
        </div>
    </div>
    {% endfor %}
</div>
{% else %}
<div class="empty-state">
    <p>还没有文案项目</p>
    <p style="font-size:13px; margin-top:8px;">点击"新建文案"开始创作</p>
</div>
{% endif %}

<!-- 上传弹窗 -->
<div id="uploadModal" style="display:none; position:fixed; inset:0; background:rgba(0,0,0,.5); z-index:100; display:none; align-items:center; justify-content:center;">
    <div style="background:var(--card-bg); border-radius:12px; padding:24px; width:480px; max-height:80vh; overflow-y:auto;">
        <h3 style="margin:0 0 16px;">新建文案项目</h3>
        <form id="uploadForm" enctype="multipart/form-data">
            <div style="margin-bottom:12px;">
                <label style="font-size:13px; font-weight:600; display:block; margin-bottom:4px;">视频文件 *</label>
                <input type="file" name="video" accept="video/*" required style="width:100%;">
            </div>
            <div style="margin-bottom:12px;">
                <label style="font-size:13px; font-weight:600; display:block; margin-bottom:4px;">商品主图</label>
                <input type="file" name="product_image" accept="image/*" style="width:100%;">
            </div>
            <div style="margin-bottom:12px;">
                <label style="font-size:13px; font-weight:600; display:block; margin-bottom:4px;">商品标题 *</label>
                <input type="text" name="product_title" required class="form-input" placeholder="如：便携式榨汁机">
            </div>
            <div style="display:flex; gap:12px; margin-bottom:12px;">
                <div style="flex:1;">
                    <label style="font-size:13px; font-weight:600; display:block; margin-bottom:4px;">价格</label>
                    <input type="text" name="price" class="form-input" placeholder="$29.99">
                </div>
                <div style="flex:1;">
                    <label style="font-size:13px; font-weight:600; display:block; margin-bottom:4px;">目标人群</label>
                    <input type="text" name="target_audience" class="form-input" placeholder="年轻女性">
                </div>
            </div>
            <div style="margin-bottom:12px;">
                <label style="font-size:13px; font-weight:600; display:block; margin-bottom:4px;">卖点（每行一个）</label>
                <textarea name="selling_points" class="form-input" rows="3" placeholder="便携小巧&#10;30秒快速榨汁&#10;USB充电"></textarea>
            </div>
            <div style="margin-bottom:12px;">
                <label style="font-size:13px; font-weight:600; display:block; margin-bottom:4px;">补充信息</label>
                <textarea name="extra_info" class="form-input" rows="2" placeholder="其他想让AI知道的信息..."></textarea>
            </div>
            <div style="margin-bottom:16px;">
                <label style="font-size:13px; font-weight:600; display:block; margin-bottom:4px;">文案语言</label>
                <select name="language" class="form-input">
                    <option value="en" selected>English</option>
                    <option value="zh">中文</option>
                </select>
            </div>
            <div style="display:flex; gap:8px; justify-content:flex-end;">
                <button type="button" onclick="closeUploadModal()" style="padding:8px 16px; border-radius:6px; border:1px solid var(--border); background:transparent; cursor:pointer;">取消</button>
                <button type="submit" class="btn-new" style="border-radius:6px;">开始创作</button>
            </div>
        </form>
    </div>
</div>

<style>
    .form-input { width:100%; padding:8px 10px; border:1px solid var(--border); border-radius:6px; font-size:13px; background:var(--card-bg); color:var(--text); box-sizing:border-box; }
</style>

<script>
function openUploadModal() {
    document.getElementById('uploadModal').style.display = 'flex';
}
function closeUploadModal() {
    document.getElementById('uploadModal').style.display = 'none';
}
document.getElementById('uploadModal').addEventListener('click', function(e) {
    if (e.target === this) closeUploadModal();
});
document.getElementById('uploadForm').addEventListener('submit', async function(e) {
    e.preventDefault();
    const form = new FormData(this);
    // 将卖点文本转为 JSON 数组
    const sp = form.get('selling_points');
    if (sp) {
        const arr = sp.split('\n').map(s => s.trim()).filter(Boolean);
        form.set('selling_points', JSON.stringify(arr));
    }
    const btn = this.querySelector('button[type=submit]');
    btn.disabled = true;
    btn.textContent = '上传中...';
    try {
        const resp = await fetch('/api/copywriting/upload', { method: 'POST', body: form });
        const data = await resp.json();
        if (data.task_id) {
            location.href = '/copywriting/' + data.task_id;
        } else {
            alert(data.error || '上传失败');
        }
    } catch(err) {
        alert('上传失败: ' + err.message);
    } finally {
        btn.disabled = false;
        btn.textContent = '开始创作';
    }
});
</script>
{% endblock %}
```

- [ ] **Step 2: 提交**

```bash
git add web/templates/copywriting_list.html
git commit -m "feat: 新增文案项目列表页模板"
```

---

## Task 10: 文案创作工作页模板

**Files:**
- Create: `web/templates/copywriting_detail.html`
- Create: `web/templates/_copywriting_styles.html`
- Create: `web/templates/_copywriting_scripts.html`

- [ ] **Step 1: 创建样式文件 _copywriting_styles.html**

```html
{# web/templates/_copywriting_styles.html #}
<style>
/* ── 混合式布局：上方素材区 + 下方文案区 ── */
.cw-page { max-width:960px; margin:0 auto; }
.cw-back { font-size:13px; color:var(--text-muted); text-decoration:none; margin-bottom:12px; display:inline-block; }
.cw-back:hover { color:var(--text); }

/* 素材区 */
.cw-materials { background:var(--card-bg); border:1px solid var(--border); border-radius:10px; padding:16px; margin-bottom:16px; }
.cw-materials-header { display:flex; justify-content:space-between; align-items:center; margin-bottom:12px; cursor:pointer; }
.cw-materials-header h3 { margin:0; font-size:14px; }
.cw-materials-toggle { font-size:12px; color:var(--text-muted); }
.cw-materials-body { display:flex; gap:16px; }
.cw-materials-body.collapsed { display:none; }
.cw-video-col { flex:0 0 200px; }
.cw-video-player { width:100%; border-radius:6px; background:#111; }
.cw-keyframes-col { flex:1; min-width:0; }
.cw-keyframes-row { display:flex; gap:4px; flex-wrap:wrap; }
.cw-keyframe-thumb { width:72px; height:54px; object-fit:cover; border-radius:4px; border:1px solid var(--border); }
.cw-product-col { flex:0 0 180px; font-size:12px; line-height:1.6; }
.cw-product-col .label { font-weight:600; color:var(--text-muted); font-size:11px; text-transform:uppercase; }
.cw-product-img { width:60px; height:60px; object-fit:cover; border-radius:6px; border:1px solid var(--border); }

/* 步骤状态栏 */
.cw-steps { display:flex; gap:8px; margin-bottom:16px; }
.cw-step { flex:1; padding:8px 12px; border-radius:8px; background:var(--card-bg); border:1px solid var(--border); font-size:12px; text-align:center; }
.cw-step.active { border-color:#4f46e5; color:#4f46e5; font-weight:600; }
.cw-step.done { border-color:#10b981; color:#10b981; }
.cw-step.error { border-color:#ef4444; color:#ef4444; }

/* 文案编辑区 */
.cw-editor { background:var(--card-bg); border:1px solid var(--border); border-radius:10px; padding:16px; }
.cw-editor-header { display:flex; justify-content:space-between; align-items:center; margin-bottom:12px; flex-wrap:wrap; gap:8px; }
.cw-editor-header h3 { margin:0; font-size:14px; }
.cw-editor-tools { display:flex; gap:8px; align-items:center; }
.cw-lang-toggle { display:flex; gap:4px; }
.cw-lang-btn { padding:3px 10px; border-radius:10px; font-size:11px; border:1px solid var(--border); background:transparent; cursor:pointer; }
.cw-lang-btn.active { background:#dbeafe; border-color:#93c5fd; color:#1d4ed8; }
.cw-prompt-select { font-size:12px; padding:4px 8px; border-radius:6px; border:1px solid var(--border); background:var(--card-bg); }
.cw-segments { display:flex; flex-direction:column; gap:8px; margin-bottom:16px; }
.cw-segment { display:flex; align-items:flex-start; gap:10px; padding:10px 12px; border:1px solid var(--border); border-radius:8px; background:var(--bg); }
.cw-segment:hover { border-color:#c7d2fe; }
.cw-seg-label { flex:0 0 70px; font-size:11px; font-weight:700; color:#4f46e5; padding-top:2px; }
.cw-seg-text { flex:1; font-size:13px; line-height:1.6; min-height:20px; }
.cw-seg-text[contenteditable=true] { outline:none; background:#fefce8; padding:4px 6px; border-radius:4px; }
.cw-seg-actions { flex:0 0 auto; display:flex; gap:4px; }
.cw-seg-btn { font-size:11px; padding:3px 8px; border-radius:4px; border:1px solid var(--border); background:transparent; cursor:pointer; color:var(--text-muted); }
.cw-seg-btn:hover { background:var(--card-bg); color:var(--text); }
.cw-seg-duration { font-size:11px; color:var(--text-muted); flex:0 0 30px; text-align:right; padding-top:2px; }

/* 底部操作栏 */
.cw-actions { display:flex; gap:8px; justify-content:flex-end; flex-wrap:wrap; }
.cw-btn { padding:8px 16px; border-radius:6px; font-size:13px; border:none; cursor:pointer; }
.cw-btn-secondary { background:var(--card-bg); border:1px solid var(--border); color:var(--text); }
.cw-btn-secondary:hover { background:var(--bg); }
.cw-btn-primary { background:#4f46e5; color:#fff; }
.cw-btn-primary:hover { background:#4338ca; }
.cw-btn:disabled { opacity:.5; cursor:not-allowed; }

/* TTS 结果区 */
.cw-result { background:var(--card-bg); border:1px solid var(--border); border-radius:10px; padding:16px; margin-top:16px; }
.cw-result h3 { margin:0 0 12px; font-size:14px; }
.cw-result audio, .cw-result video { width:100%; border-radius:6px; margin-bottom:8px; }
.cw-download-row { display:flex; gap:8px; }

/* Loading */
.cw-loading { text-align:center; padding:20px; color:var(--text-muted); font-size:13px; }
.cw-spinner { display:inline-block; width:16px; height:16px; border:2px solid var(--border); border-top-color:#4f46e5; border-radius:50%; animation:spin .6s linear infinite; margin-right:6px; vertical-align:middle; }
@keyframes spin { to { transform:rotate(360deg); } }

/* Rewrite modal */
.cw-rewrite-modal { position:fixed; inset:0; background:rgba(0,0,0,.5); z-index:100; display:flex; align-items:center; justify-content:center; }
.cw-rewrite-box { background:var(--card-bg); border-radius:10px; padding:20px; width:400px; }
</style>
```

- [ ] **Step 2: 创建主页面 copywriting_detail.html**

```html
{# web/templates/copywriting_detail.html #}
{% extends "layout.html" %}
{% block title %}文案创作 — {{ task.display_name or task.original_filename }}{% endblock %}
{% block content %}
{% include "_copywriting_styles.html" %}

<div class="cw-page">
    <a href="/copywriting" class="cw-back">← 返回列表</a>

    <!-- 步骤状态栏 -->
    <div class="cw-steps">
        {% for step_key, step_label in [("keyframe", "抽帧"), ("copywrite", "文案"), ("tts", "语音"), ("compose", "合成")] %}
        <div class="cw-step" id="step-{{ step_key }}"
             data-status="{{ task.steps.get(step_key, 'pending') }}">
            {{ step_label }}
        </div>
        {% endfor %}
    </div>

    <!-- 素材 & 商品信息区（可折叠） -->
    <div class="cw-materials">
        <div class="cw-materials-header" onclick="toggleMaterials()">
            <h3>📦 素材 & 商品信息</h3>
            <span class="cw-materials-toggle" id="materialsToggle">▼ 收起</span>
        </div>
        <div class="cw-materials-body" id="materialsBody">
            <div class="cw-video-col">
                <video class="cw-video-player" controls preload="metadata"
                       src="/api/copywriting/{{ task_id }}/artifact/video_source">
                </video>
            </div>
            <div class="cw-keyframes-col">
                <div class="label" style="margin-bottom:4px;">关键帧</div>
                <div class="cw-keyframes-row" id="keyframesRow">
                    {% for i in range(task.keyframes|length) %}
                    <img class="cw-keyframe-thumb"
                         src="/api/copywriting/{{ task_id }}/keyframe/{{ i }}" alt="帧{{ i+1 }}">
                    {% endfor %}
                    {% if not task.keyframes %}
                    <span style="font-size:12px; color:var(--text-muted);">抽帧中...</span>
                    {% endif %}
                </div>
            </div>
            <div class="cw-product-col">
                <div class="label">商品信息</div>
                {% if inputs.get('product_image_url') %}
                <img class="cw-product-img" src="/api/copywriting/{{ task_id }}/artifact/product_image" alt="">
                {% endif %}
                <div><strong>{{ inputs.get('product_title', '') }}</strong></div>
                {% if inputs.get('price') %}<div>💰 {{ inputs.price }}</div>{% endif %}
                {% if inputs.get('target_audience') %}<div>🎯 {{ inputs.target_audience }}</div>{% endif %}
            </div>
        </div>
    </div>

    <!-- 文案编辑区 -->
    <div class="cw-editor" id="editorSection">
        <div class="cw-editor-header">
            <h3>📝 文案编辑</h3>
            <div class="cw-editor-tools">
                <select class="cw-prompt-select" id="promptSelect">
                    <option value="">默认提示词</option>
                </select>
                <div class="cw-lang-toggle">
                    <button class="cw-lang-btn {% if (inputs.get('language','en')) == 'en' %}active{% endif %}"
                            onclick="setLanguage('en')">English</button>
                    <button class="cw-lang-btn {% if (inputs.get('language','en')) == 'zh' %}active{% endif %}"
                            onclick="setLanguage('zh')">中文</button>
                </div>
                <button class="cw-btn cw-btn-secondary" onclick="regenerateCopy()" id="btnRegenerate">
                    🔄 重新生成
                </button>
            </div>
        </div>

        <div class="cw-segments" id="segmentsContainer">
            {% if task.copy and task.copy.segments %}
                {% for seg in task.copy.segments %}
                <div class="cw-segment" data-index="{{ loop.index0 }}">
                    <div class="cw-seg-label">{{ seg.label }}</div>
                    <div class="cw-seg-text" id="segText{{ loop.index0 }}">{{ seg.text }}</div>
                    <div class="cw-seg-duration">{{ seg.duration_hint|round(1) }}s</div>
                    <div class="cw-seg-actions">
                        <button class="cw-seg-btn" onclick="startEdit({{ loop.index0 }})">✏️</button>
                        <button class="cw-seg-btn" onclick="openRewrite({{ loop.index0 }})">🔄</button>
                    </div>
                </div>
                {% endfor %}
            {% else %}
                <div class="cw-loading" id="copyLoading">
                    {% if task.steps.get('copywrite') == 'running' %}
                    <span class="cw-spinner"></span>文案生成中...
                    {% elif task.steps.get('keyframe') == 'running' %}
                    <span class="cw-spinner"></span>正在抽取关键帧...
                    {% else %}
                    等待文案生成...
                    {% endif %}
                </div>
            {% endif %}
        </div>

        <div class="cw-actions">
            <button class="cw-btn cw-btn-secondary" onclick="exportCopy()" id="btnExport"
                    {% if not task.copy %}disabled{% endif %}>
                📋 复制文案
            </button>
            <button class="cw-btn cw-btn-secondary" onclick="downloadCopy()" id="btnDownload"
                    {% if not task.copy %}disabled{% endif %}>
                💾 导出文案
            </button>
            <button class="cw-btn cw-btn-primary" onclick="startTTS()" id="btnTTS"
                    {% if not task.copy %}disabled{% endif %}>
                ▶ 生成语音视频
            </button>
        </div>
    </div>

    <!-- TTS & 合成结果区（动态显示） -->
    <div class="cw-result" id="resultSection" style="display:none;">
        <h3>🎬 生成结果</h3>
        <audio id="resultAudio" controls style="display:none;"></audio>
        <video id="resultVideo" controls style="display:none;"></video>
        <div class="cw-download-row" id="downloadRow" style="display:none;">
            <a class="cw-btn cw-btn-secondary" id="dlVideo" href="#" download>下载视频</a>
            <a class="cw-btn cw-btn-secondary" id="dlCopy" href="#" download>下载文案</a>
        </div>
    </div>
</div>

<!-- 重写弹窗 -->
<div class="cw-rewrite-modal" id="rewriteModal" style="display:none;">
    <div class="cw-rewrite-box">
        <h3 style="margin:0 0 12px; font-size:14px;">重写段落</h3>
        <div style="font-size:12px; color:var(--text-muted); margin-bottom:8px;" id="rewriteOriginal"></div>
        <input type="text" id="rewriteInstruction" class="form-input"
               placeholder="修改要求（可选，如：更口语化、加入紧迫感）" style="margin-bottom:12px; width:100%; padding:8px; border:1px solid var(--border); border-radius:6px; font-size:13px; box-sizing:border-box;">
        <div style="display:flex; gap:8px; justify-content:flex-end;">
            <button class="cw-btn cw-btn-secondary" onclick="closeRewrite()">取消</button>
            <button class="cw-btn cw-btn-primary" onclick="confirmRewrite()" id="btnConfirmRewrite">重写</button>
        </div>
    </div>
</div>

{% include "_copywriting_scripts.html" %}
{% endblock %}
```

- [ ] **Step 3: 创建脚本文件 _copywriting_scripts.html**

```html
{# web/templates/_copywriting_scripts.html #}
<script src="https://cdnjs.cloudflare.com/ajax/libs/socket.io/4.7.4/socket.io.min.js"></script>
<script>
const TASK_ID = "{{ task_id }}";
const socket = io();
socket.emit("join_copywriting_task", { task_id: TASK_ID });

let currentSegments = {{ task.copy.segments | tojson if task.copy and task.copy.segments else '[]' }};
let rewriteIndex = -1;

// ── SocketIO 事件处理 ────────────────────────────

socket.on("cw_step_update", function(data) {
    if (data.task_id !== TASK_ID) return;
    updateStepUI(data.step, data.status, data.message);
});

socket.on("cw_keyframes_ready", function(data) {
    if (data.task_id !== TASK_ID) return;
    const row = document.getElementById("keyframesRow");
    row.innerHTML = "";
    for (let i = 0; i < data.count; i++) {
        const img = document.createElement("img");
        img.className = "cw-keyframe-thumb";
        img.src = "/api/copywriting/" + TASK_ID + "/keyframe/" + i;
        row.appendChild(img);
    }
});

socket.on("cw_copy_ready", function(data) {
    if (data.task_id !== TASK_ID) return;
    currentSegments = data.copy.segments || [];
    renderSegments(currentSegments);
    enableButtons(true);
});

socket.on("cw_tts_ready", function(data) {
    if (data.task_id !== TASK_ID) return;
    document.getElementById("resultSection").style.display = "block";
    const audio = document.getElementById("resultAudio");
    audio.src = "/api/copywriting/" + TASK_ID + "/artifact/tts_audio";
    audio.style.display = "block";
});

socket.on("cw_compose_ready", function(data) {
    if (data.task_id !== TASK_ID) return;
    const video = document.getElementById("resultVideo");
    video.src = "/api/copywriting/" + TASK_ID + "/artifact/video";
    video.style.display = "block";
    const dlRow = document.getElementById("downloadRow");
    dlRow.style.display = "flex";
    document.getElementById("dlVideo").href = "/api/copywriting/" + TASK_ID + "/download/soft_video";
    document.getElementById("dlCopy").href = "/api/copywriting/" + TASK_ID + "/download/copy";
    document.getElementById("btnTTS").disabled = false;
    document.getElementById("btnTTS").textContent = "▶ 生成语音视频";
});

socket.on("cw_error", function(data) {
    if (data.task_id !== TASK_ID) return;
    alert("错误: " + (data.message || "未知错误"));
    enableButtons(true);
});

// ── UI 更新 ──────────────────────────────────────

function updateStepUI(step, status, message) {
    const el = document.getElementById("step-" + step);
    if (!el) return;
    el.className = "cw-step";
    if (status === "done") el.classList.add("done");
    else if (status === "running") el.classList.add("active");
    else if (status === "error") el.classList.add("error");

    if (status === "running") {
        const loading = document.getElementById("copyLoading");
        if (loading) loading.innerHTML = '<span class="cw-spinner"></span>' + (message || "处理中...");
    }
}

function renderSegments(segments) {
    const container = document.getElementById("segmentsContainer");
    container.innerHTML = "";
    segments.forEach(function(seg, i) {
        const div = document.createElement("div");
        div.className = "cw-segment";
        div.dataset.index = i;
        div.innerHTML =
            '<div class="cw-seg-label">' + seg.label + '</div>' +
            '<div class="cw-seg-text" id="segText' + i + '">' + escapeHtml(seg.text) + '</div>' +
            '<div class="cw-seg-duration">' + (seg.duration_hint || 0).toFixed(1) + 's</div>' +
            '<div class="cw-seg-actions">' +
            '<button class="cw-seg-btn" onclick="startEdit(' + i + ')">✏️</button>' +
            '<button class="cw-seg-btn" onclick="openRewrite(' + i + ')">🔄</button>' +
            '</div>';
        container.appendChild(div);
    });
}

function enableButtons(enabled) {
    document.getElementById("btnExport").disabled = !enabled;
    document.getElementById("btnDownload").disabled = !enabled;
    document.getElementById("btnTTS").disabled = !enabled;
    document.getElementById("btnRegenerate").disabled = !enabled;
}

function escapeHtml(text) {
    const d = document.createElement("div");
    d.textContent = text;
    return d.innerHTML;
}

// ── 素材区折叠 ──────────────────────────────────

let materialsOpen = true;
function toggleMaterials() {
    materialsOpen = !materialsOpen;
    document.getElementById("materialsBody").classList.toggle("collapsed", !materialsOpen);
    document.getElementById("materialsToggle").textContent = materialsOpen ? "▼ 收起" : "▶ 展开";
}

// ── 文案操作 ─────────────────────────────────────

function regenerateCopy() {
    if (!confirm("重新生成将覆盖当前文案，确定？")) return;
    document.getElementById("btnRegenerate").disabled = true;
    const promptId = document.getElementById("promptSelect").value;
    fetch("/api/copywriting/" + TASK_ID + "/generate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ prompt_id: promptId || null }),
    });
}

function startEdit(index) {
    const el = document.getElementById("segText" + index);
    el.contentEditable = "true";
    el.focus();
    el.addEventListener("blur", function handler() {
        el.contentEditable = "false";
        currentSegments[index].text = el.textContent.trim();
        saveCopy();
        el.removeEventListener("blur", handler);
    }, { once: true });
}

function saveCopy() {
    fetch("/api/copywriting/" + TASK_ID + "/segments", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ segments: currentSegments }),
    });
}

function openRewrite(index) {
    rewriteIndex = index;
    document.getElementById("rewriteOriginal").textContent =
        "[" + currentSegments[index].label + "] " + currentSegments[index].text;
    document.getElementById("rewriteInstruction").value = "";
    document.getElementById("rewriteModal").style.display = "flex";
}

function closeRewrite() {
    document.getElementById("rewriteModal").style.display = "none";
    rewriteIndex = -1;
}

async function confirmRewrite() {
    if (rewriteIndex < 0) return;
    const btn = document.getElementById("btnConfirmRewrite");
    btn.disabled = true;
    btn.textContent = "重写中...";
    try {
        const resp = await fetch("/api/copywriting/" + TASK_ID + "/rewrite-segment", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                index: rewriteIndex,
                instruction: document.getElementById("rewriteInstruction").value,
            }),
        });
        const data = await resp.json();
        if (data.segment) {
            currentSegments[rewriteIndex] = data.segment;
            currentSegments[rewriteIndex].index = rewriteIndex;
            renderSegments(currentSegments);
        }
    } catch (err) {
        alert("重写失败: " + err.message);
    } finally {
        btn.disabled = false;
        btn.textContent = "重写";
        closeRewrite();
    }
}

function exportCopy() {
    const text = currentSegments.map(s => "[" + s.label + "]\n" + s.text).join("\n\n");
    navigator.clipboard.writeText(text).then(() => alert("已复制到剪贴板"));
}

function downloadCopy() {
    window.open("/api/copywriting/" + TASK_ID + "/download/copy");
}

function startTTS() {
    document.getElementById("btnTTS").disabled = true;
    document.getElementById("btnTTS").textContent = "生成中...";
    fetch("/api/copywriting/" + TASK_ID + "/tts", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({}),
    });
}

function setLanguage(lang) {
    document.querySelectorAll(".cw-lang-btn").forEach(b => b.classList.remove("active"));
    event.target.classList.add("active");
    fetch("/api/copywriting/" + TASK_ID + "/inputs", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ language: lang }),
    });
}

// ── 提示词加载 ──────────────────────────────────

async function loadPrompts() {
    try {
        const resp = await fetch("/api/prompts?type=copywriting");
        const data = await resp.json();
        const select = document.getElementById("promptSelect");
        (data.prompts || []).forEach(function(p) {
            const opt = document.createElement("option");
            opt.value = p.id;
            opt.textContent = p.name;
            select.appendChild(opt);
        });
    } catch(e) { /* ignore */ }
}
loadPrompts();
</script>
```

- [ ] **Step 4: 提交**

```bash
git add web/templates/copywriting_detail.html web/templates/_copywriting_styles.html web/templates/_copywriting_scripts.html
git commit -m "feat: 新增文案创作工作页模板"
```

---

## Task 11: Prompt API 兼容 type 过滤

**Files:**
- Modify: `web/routes/prompt.py`

- [ ] **Step 1: 在 prompt 列表接口中支持 type 参数过滤**

在 `web/routes/prompt.py` 的列表接口（GET `/api/prompts`）的 SQL 查询中，追加按 `type` 过滤的逻辑：

```python
# 在查询 user_prompts 时增加 type 过滤
prompt_type = request.args.get("type", "translation")
# SQL WHERE 条件追加: AND type = %s
# 参数追加: prompt_type
```

具体修改：找到查询 `user_prompts` 的 SQL，将 `WHERE user_id = %s` 改为 `WHERE user_id = %s AND type = %s`，并在参数元组中追加 `prompt_type`。

- [ ] **Step 2: 在创建接口中支持 type 字段**

在 `POST /api/prompts` 的 INSERT 语句中，将 `type` 字段从请求数据中读取并写入：

```python
prompt_type = data.get("type", "translation")
# INSERT INTO user_prompts (..., type) VALUES (..., %s)
```

- [ ] **Step 3: 提交**

```bash
git add web/routes/prompt.py
git commit -m "feat: prompt API 支持 type 参数过滤"
```

---

## Task 12: 视频源和商品主图的 Artifact 路由

**Files:**
- Modify: `web/routes/copywriting.py`

- [ ] **Step 1: 补充 artifact 路由中对 video_source、product_image、thumbnail 的处理**

在 `web/routes/copywriting.py` 的 `get_artifact` 函数中追加：

```python
    if name == "video_source":
        video_path = task.get("video_path")
        if video_path and os.path.isfile(video_path):
            return send_file(video_path)
    elif name == "product_image":
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT product_image_url FROM copywriting_inputs WHERE project_id = %s",
                    (task_id,),
                )
                row = cur.fetchone()
                if row and row.get("product_image_url") and os.path.isfile(row["product_image_url"]):
                    return send_file(row["product_image_url"])
        finally:
            conn.close()
    elif name == "thumbnail":
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT thumbnail_path FROM projects WHERE id = %s",
                    (task_id,),
                )
                row = cur.fetchone()
                if row and row.get("thumbnail_path") and os.path.isfile(row["thumbnail_path"]):
                    return send_file(row["thumbnail_path"])
        finally:
            conn.close()
```

- [ ] **Step 2: 提交**

```bash
git add web/routes/copywriting.py
git commit -m "feat: 文案创作 artifact 路由补充视频源和图片"
```

---

## Task 13: 默认文案提示词种子数据

**Files:**
- Create: `db/migrations/seed_copywriting_prompts.sql`

- [ ] **Step 1: 编写种子数据 SQL**

为每个用户插入两条默认文案提示词（中英文版本）。由于是多用户系统，这里创建一个管理员级别的种子数据，具体用户首次使用时可由应用层自动创建。

```sql
-- db/migrations/seed_copywriting_prompts.sql
-- 为现有用户创建默认文案提示词

INSERT INTO user_prompts (user_id, type, name, prompt_text, prompt_text_zh, is_default)
SELECT u.id, 'copywriting', 'TikTok 卖货文案 (English)',
'You are an expert TikTok short-video copywriter specializing in US e-commerce ads.

**Your task:** Based on the video keyframes, product information, and product images provided, write a compelling short-video sales script for the US market. The script must match the video''s visual content and the product being sold.

**Video understanding:** Carefully analyze each keyframe to understand the video''s scenes, actions, mood, and pacing. Your script must align with what''s happening on screen — each segment should correspond to the visual flow.

**Script structure (follow TikTok best practices):**
1. **Hook (0-3s):** An attention-grabbing opening that stops the scroll. Use curiosity, shock, relatability, or a bold claim. Must connect to what''s shown in the first frames.
2. **Problem/Scene (3-8s):** Identify a pain point or set a relatable scene that the target audience experiences. Match the video''s visual context.
3. **Product Reveal (8-15s):** Introduce the product naturally as the solution. Highlight key selling points that are visible in the video. Be specific — mention features shown on screen.
4. **Social Proof / Demo (15-22s):** Reinforce credibility — results, transformations, or demonstrations visible in the video. Use sensory language.
5. **CTA (last 3-5s):** Clear call-to-action. Create urgency. Direct viewers to take action.

**Style guidelines:**
- Conversational, authentic tone — sounds like a real person, not an ad
- Short punchy sentences, easy to speak aloud
- Use power words: "obsessed", "game-changer", "finally", "you need this"
- Match the energy/mood of the video (upbeat, calm, dramatic, etc.)
- Aim for 15-45 seconds total speaking time depending on video length',
'你是一位专业的短视频带货文案专家，擅长为美国 TikTok 市场创作电商广告脚本。

**你的任务：** 根据提供的视频关键帧、商品信息和商品图片，撰写一段面向美国市场的短视频带货口播文案。文案必须与视频画面内容和所售商品高度匹配。

**视频理解：** 仔细分析每一帧关键画面，理解视频的场景、动作、氛围和节奏。你的文案必须与画面同步——每一段都要对应视频的视觉流程。

**文案结构（遵循 TikTok 最佳实践）：**
1. **Hook 开头（0-3秒）：** 抓眼球的开场，让用户停止滑动。用好奇心、冲击感、共鸣或大胆主张。必须关联开头几帧画面。
2. **痛点/场景（3-8秒）：** 点出目标用户的痛点或建立一个有共鸣的场景，匹配视频画面。
3. **产品展示（8-15秒）：** 自然引入产品作为解决方案。突出视频中可见的核心卖点，要具体——提及画面中展示的功能特点。
4. **信任背书/演示（15-22秒）：** 强化可信度——视频中可见的效果、变化或演示。使用感官化语言。
5. **CTA 行动号召（最后3-5秒）：** 清晰的行动指令，制造紧迫感，引导用户下单。

**风格要求：**
- 口语化、真实自然的语气——听起来像真人分享，不像广告
- 短句为主，朗朗上口，适合口播
- 善用有感染力的词汇
- 匹配视频的情绪和节奏（活力、舒缓、震撼等）
- 根据视频时长，口播总时长控制在 15-45 秒',
TRUE
FROM users u
WHERE NOT EXISTS (
    SELECT 1 FROM user_prompts up
    WHERE up.user_id = u.id AND up.type = 'copywriting'
);
```

- [ ] **Step 2: 执行种子数据**

```bash
mysql -u root -p auto_video_srt < db/migrations/seed_copywriting_prompts.sql
```

- [ ] **Step 3: 提交**

```bash
git add db/migrations/seed_copywriting_prompts.sql
git commit -m "feat: 添加默认文案创作提示词种子数据"
```

---

## Task 14: 集成测试 — 手动验证

- [ ] **Step 1: 启动服务**

```bash
python main.py
```

- [ ] **Step 2: 验证导航菜单**

打开浏览器访问应用，确认导航栏出现"文案创作"菜单项，点击跳转到 `/copywriting`。

- [ ] **Step 3: 验证项目创建流程**

1. 点击"新建文案"
2. 上传一段测试视频
3. 填写商品信息（标题、价格、卖点）
4. 点击"开始创作"
5. 确认跳转到工作页
6. 确认关键帧自动抽取并展示
7. 确认文案自动生成并展示在编辑区

- [ ] **Step 4: 验证文案编辑功能**

1. 点击某段的 ✏️ 按钮，验证内联编辑
2. 点击某段的 🔄 按钮，验证单段重写弹窗
3. 点击"重新生成"，验证全部重生成
4. 点击"复制文案"，验证剪贴板内容

- [ ] **Step 5: 验证 TTS 合成（可选）**

1. 点击"生成语音视频"
2. 确认 TTS 进度更新
3. 确认音频和视频预览播放
4. 确认下载按钮可用

- [ ] **Step 6: 提交最终调整**

修复验证中发现的问题后提交。
