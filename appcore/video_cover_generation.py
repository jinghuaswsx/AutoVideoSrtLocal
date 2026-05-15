from __future__ import annotations

import base64
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date
from io import BytesIO
import json
import os
import tempfile
import uuid
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

from PIL import Image, ImageOps
import requests

from appcore import gemini_image, llm_client, local_media_storage
from appcore.llm_media_optimizer import (
    REVIEW_480P_AUDIO,
    cleanup_optimized_media,
    media_debug_snapshot,
    prepare_video_for_llm,
)
from appcore.llm_provider_configs import get_provider_config
from appcore.meta_hot_posts.product_analysis import fetch_product_analysis
from pipeline.ffutil import extract_frame_at_timestamp, extract_thumbnail, probe_media_info


DEFAULT_IMAGE_CHANNEL = "local"
DEFAULT_IMAGE_MODEL = "gpt-image-2"
DEFAULT_COVER_EXECUTION_MODE = ""
LOCAL_IMAGE_PROVIDER_CODE = "video_cover_local_image"
LOCAL_IMAGE_BASE_URL_DEFAULT = "http://172.30.254.14:82/v1"
OUTPUT_SIZE = (1080, 1920)
REFERENCE_SIZE = (1080, 1920)
PRODUCT_IMAGE_SIZE = (400, 400)
ALLOWED_VIDEO_EXTENSIONS = {".mp4", ".mov", ".mpeg", ".mpg", ".avi", ".webm", ".m4v"}


class VideoCoverGenerationError(RuntimeError):
    """User-facing validation or generation error for video cover creation."""


@dataclass(frozen=True)
class PlatformSpec:
    platform: str
    label: str


@dataclass(frozen=True)
class ModelSelection:
    provider: str
    model: str
    alias: str
    label: str


SOCIAL_REELS_SPEC = PlatformSpec(
    platform="social_reels",
    label="Facebook / Instagram / TikTok / Shorts",
)
PLATFORM_SPECS = (SOCIAL_REELS_SPEC,)
ALLOWED_IMAGE_COUNTS = {1, 2, 3, 4}

GEMINI_TEXT_MODEL_LABELS: dict[str, tuple[str, str]] = {
    "gemini_31_pro": ("Gemini 3.1 Pro Preview", "gemini-3.1-pro-preview"),
    "gemini_3_flash": ("Gemini 3 Flash", "gemini-3-flash-preview"),
    "gemini_31_flash_lite": ("Gemini 3.1 Flash-Lite", "gemini-3.1-flash-lite-preview"),
}
OPENROUTER_TEXT_EXTRAS: dict[str, tuple[str, str]] = {
    "claude_sonnet": ("Claude Sonnet 4.6", "anthropic/claude-sonnet-4.6"),
    "gpt_5_5": ("GPT-5.5", "openai/gpt-5.5"),
    "gpt_5_mini": ("GPT-5 Mini", "openai/gpt-5-mini"),
}


def _gemini_text_models(alias_order: tuple[str, ...], *, openrouter: bool = False) -> dict[str, dict[str, str]]:
    return {
        alias: {
            "label": GEMINI_TEXT_MODEL_LABELS[alias][0],
            "model": (
                f"google/{GEMINI_TEXT_MODEL_LABELS[alias][1]}"
                if openrouter
                else GEMINI_TEXT_MODEL_LABELS[alias][1]
            ),
        }
        for alias in alias_order
    }


def _text_providers(alias_order: tuple[str, ...], *, openrouter_extra: bool = False) -> dict[str, dict[str, Any]]:
    openrouter_models = _gemini_text_models(alias_order, openrouter=True)
    if openrouter_extra:
        openrouter_models.update(
            {
                alias: {"label": label, "model": model}
                for alias, (label, model) in OPENROUTER_TEXT_EXTRAS.items()
            }
        )
    return {
        "openrouter": {
            "label": "OPENROUTER",
            "models": openrouter_models,
        },
        "gemini_aistudio": {
            "label": "GOOGLE AI STUDIO",
            "models": _gemini_text_models(alias_order),
        },
        "gemini_vertex": {
            "label": "GOOGLE VERTEX",
            "models": _gemini_text_models(alias_order),
        },
        "gemini_vertex_adc": {
            "label": "GOOGLE VERTEX ADC",
            "models": _gemini_text_models(alias_order),
        },
    }

TEXT_STEP_MODEL_OPTIONS: dict[str, dict[str, Any]] = {
    "video_analysis": {
        "label": "视频分析",
        "default_provider": "gemini_vertex_adc",
        "providers": _text_providers(("gemini_31_pro", "gemini_3_flash", "gemini_31_flash_lite")),
    },
    "product_analysis": {
        "label": "产品分析",
        "default_provider": "openrouter",
        "providers": _text_providers(("gemini_3_flash", "gemini_31_pro", "gemini_31_flash_lite")),
    },
    "ad_copy": {
        "label": "文案创作",
        "default_provider": "openrouter",
        "providers": _text_providers(
            ("gemini_3_flash", "gemini_31_pro", "gemini_31_flash_lite"),
            openrouter_extra=True,
        ),
    },
}

COVER_MODEL_OPTIONS: dict[str, Any] = {
    "label": "封面生成",
    "default_provider": "local",
    "providers": {
        "local": "本地接口",
        "openrouter": "OPENROUTER",
        "gemini_vertex_adc": "GOOGLE VERTEX ADC",
    },
    "models": {
        "local": {
            "gpt_image_2": "gpt-image-2",
            "nano_banana_2": "gemini-3.1-flash-image-preview",
            "nano_banana_pro": "gemini-3-pro-image-preview",
            "nano_banana_1": "gemini-2.5-flash-image-preview",
        },
        "openrouter": {
            "openai_image_2_low": "openai/gpt-5.4-image-2:low",
            "openai_image_2_mid": "openai/gpt-5.4-image-2:mid",
            "openai_image_2_high": "openai/gpt-5.4-image-2:high",
            "nano_banana_2": "google/gemini-3.1-flash-image-preview",
            "nano_banana_pro": "google/gemini-3-pro-image-preview",
            "nano_banana_1": "google/gemini-2.5-flash-image-preview",
        },
        "gemini_vertex_adc": {
            "nano_banana_2": "gemini-3.1-flash-image-preview",
            "nano_banana_pro": "gemini-3-pro-image-preview",
            "nano_banana_1": "gemini-2.5-flash-image-preview",
        },
    },
    "model_labels": {
        "gpt_image_2": "GPT-Image-2",
        "openai_image_2_low": "OpenAI Image 2（Low）",
        "openai_image_2_mid": "OpenAI Image 2（Mid）",
        "openai_image_2_high": "OpenAI Image 2（High）",
        "nano_banana_2": "Nano Banana 2",
        "nano_banana_pro": "Nano Banana Pro",
        "nano_banana_1": "Nano Banana 1",
    },
    "model_aliases": {
        "openrouter": {
            "gpt_image_2": "openai_image_2_mid",
            "gemini-3.1-flash-image-preview": "nano_banana_2",
            "gemini-3-pro-image-preview": "nano_banana_pro",
            "gemini-2.5-flash-image-preview": "nano_banana_1",
        },
    },
}


def _first_model_alias(models: dict[str, Any]) -> str:
    return next(iter(models))


def resolve_text_model_selection(step: str, provider: str | None, model: str | None) -> ModelSelection:
    config = TEXT_STEP_MODEL_OPTIONS.get(step)
    if not config:
        raise VideoCoverGenerationError(f"未知步骤：{step}")
    provider_key = (provider or config["default_provider"]).strip().lower()
    providers = config["providers"]
    if provider_key not in providers:
        provider_key = config["default_provider"]
    model_options = providers[provider_key]["models"]
    model_key = (model or "").strip()
    if model_key not in model_options:
        for alias, item in model_options.items():
            if model_key and model_key == item["model"]:
                model_key = alias
                break
        else:
            if model_key:
                return ModelSelection(
                    provider=provider_key,
                    model=model_key,
                    alias=model_key,
                    label=model_key,
                )
            model_key = _first_model_alias(model_options)
    selected = model_options[model_key]
    return ModelSelection(
        provider=provider_key,
        model=selected["model"],
        alias=model_key,
        label=selected["label"],
    )


def resolve_cover_model_selection(provider: str | None, model: str | None) -> ModelSelection:
    provider_key = (provider or COVER_MODEL_OPTIONS["default_provider"]).strip().lower()
    if provider_key not in COVER_MODEL_OPTIONS["providers"]:
        provider_key = COVER_MODEL_OPTIONS["default_provider"]
    model_options = COVER_MODEL_OPTIONS["models"][provider_key]
    model_key = (model or "").strip()
    model_key = COVER_MODEL_OPTIONS.get("model_aliases", {}).get(provider_key, {}).get(model_key, model_key)
    if model_key not in model_options:
        for alias, actual in model_options.items():
            if model_key and model_key == actual:
                model_key = alias
                break
        else:
            if model_key:
                return ModelSelection(
                    provider=provider_key,
                    model=model_key,
                    alias=model_key,
                    label=model_key,
                )
            model_key = _first_model_alias(model_options)
    return ModelSelection(
        provider=provider_key,
        model=model_options[model_key],
        alias=model_key,
        label=COVER_MODEL_OPTIONS["model_labels"][model_key],
    )


def normalize_cover_execution_mode(provider: str | None, mode: Any) -> str:
    provider_key = str(provider or "").strip().lower()
    if provider_key != "openrouter":
        return "serial"
    requested = str(mode or "").strip().lower()
    return requested if requested in {"parallel", "serial"} else "parallel"


def video_cover_model_options() -> dict[str, Any]:
    return {
        "steps": {
            **TEXT_STEP_MODEL_OPTIONS,
            "cover_generation": COVER_MODEL_OPTIONS,
        }
    }

CREATIVE_DIRECTOR_PROMPT_TEMPLATE = """请基于上传的产品图片、精选视频帧、cover_brief 和 selected_ad_copy，生成一张 9:16 竖版封面图，用于 Facebook Reels / Instagram Reels / TikTok / Shorts。
请像一位优秀的创意总监一样思考：目标是做出一张适合西方社交媒体、能提升短视频点击率的完整封面。

核心目标：
- 画面像真实爆款短视频中最值得停留的一帧。
- 自然、本土化、可信，在手机屏幕上一眼能看懂产品、使用方式、适合人群和实际好处。
- 不要做成电商商品主图、海报、影棚产品照，也不要做成截图。

输入摘要：
- product_title: {product_title}
- product_url: {product_url}
- 商品主图 URL: {main_image_url}
- reference_image: 上半部分为 product_image_*，下半部分为按视频分析关键帧抽取的精选视频帧；如果关键帧不可用，则使用视频首帧兜底。
- cover_brief: {cover_brief}
- selected_ad_copy: {ad_copy_sets}

不可妥协的要求：
1. 产品必须准确。产品的形状、颜色、材质、表面质感、比例和可见功能部件，必须忠实于上传的产品图片。
2. 使用方式必须可信。根据 cover_brief 展示正确、自然的使用方式；需要手部、身体互动、安装位置或可见结果时必须清楚可见。
3. 画面必须具有西方生活方式和社交平台原生感。如果源素材包含亚洲面孔、亚洲室内环境、中文文字或国内电商风格，请自然改写为西方生活方式场景。
4. 必须把 selected_ad_copy.english.title 原生嵌入画面，作为最终封面中的唯一可读 hook。

唯一文字约束：
- 画面中必须且只能有一个可读英文 hook：原生嵌入 selected_ad_copy.english.title，并保持英文拼写完全一致。
- 除 selected_ad_copy.english.title 之外，不要生成任何可读文字、字幕、品牌字样、UI、用户名、评论框、价格、折扣、按钮、标签、贴纸、红圈、箭头、涂鸦或水印。
- 如果参考视频帧里有字幕、水印或界面残留，请忽略并移除。
- 禁止固定位置半透明背景框、整条黑色横幅、模板化标题栏；字体、位置、阴影和局部轻量托底可以随构图自然变化。
- hook 必须清晰可读，但要像社交平台原生封面的一部分，不要像后期模板压上去的标题条。

视觉方向：
写实摄影风格，真实 UGC 质感，像 iPhone 15 Pro 拍摄，4K 清晰度，自然光，真实的西方生活方式，构图有吸引力但不过度设计。优先使用特写、中近景或中景，让产品成为主要视觉焦点之一。

最终任务：
生成一张强有力的 9:16 竖版封面图，在产品准确性、正确使用方式、高点击吸引力和唯一英文 hook 可读性之间取得平衡。"""

PRODUCT_ANALYSIS_PROMPT_TEMPLATE = """角色：资深跨境电商产品分析师 + 欧美短视频广告封面策略专家。你的任务是根据用户提供的产品信息，生成可用于「文生图、视频封面生成、广告文案创作」的产品分析报告。

你的分析目标不是写商品详情页，而是帮助后续模型正确判断：

1. 产品是什么
2. 谁会用
3. 在哪里用
4. 解决什么具体生活问题
5. 哪个使用瞬间最适合做 Facebook / Reels 视频封面
6. 生图时哪些地方绝对不能画错

输入：

- 商品标题：{title}
- 描述：{description}
- 商品主图 URL：{image_url}
- 商品主图文件：已下载并标准化为 400x400 JPG，作为本次多模态 media 输入；产品外观、颜色、结构、材质判断必须优先参考该图片文件。
- 价格：{price_info}

分析原则：

- 所有判断必须基于输入信息
- 信息不足可以做合理推断，但必须明确标注“推断”
- 每个核心结论尽量结合至少两个信息源，例如：标题+描述、描述+图片、标题+图片
- 如果只有单一信息源支持，必须写明“仅由某一信息源支持”
- 信息矛盾、缺失、模糊处须明确标注
- 不得编造产品功能、材质、尺寸、适用人群、使用效果
- 不得把局部作用产品泛化成全场景产品
- 不得把装饰效果误判成功能效果
- 机械结构、安装、调节、穿戴、连接方式不确定时，必须写“推断”
- 若产品只作用于特定小部位、连接点、安装位、身体部位或物体局部，必须明确写出具体位置

重点要求一：使用方式详析
必须回答谁用、在哪里用、使用前状态、动作、接触点、使用姿势、使用顺序、最适合被画出的瞬间、使用后直观结果、辅助元素、错误画法。

重点要求二：封面转化逻辑
必须围绕欧美 Facebook / Reels 视频封面判断：一秒能否看懂产品、人群、场景、价值；痛点画面和结果画面哪个更适合；是否适合 Before / After、手部操作近景、人物出镜、安装完成态、局部细节特写；是否需要文案钩子。

重点要求三：欧美本地化判断
判断该产品最适合出现在哪种欧美生活场景中，并说明推荐人物年龄/身份、是否需要手部或露脸、推荐环境元素、需要避免的亚洲化场景/中文文字/国内电商感元素。

视觉生成分类：
A 外观展示型；B 简单使用型；C 穿戴贴合型；D 安装摆放型；E 精细接触点复杂功能型；F 多状态变形型。

输出结构：
请仅输出单个合法 JSON 对象，不要 markdown，不要代码块，不要解释文字。
JSON 字段必须包含：
- information_check
- product_definition
- core_functions
- usage_analysis
- physical_features
- western_scene_suggestions
- visual_category
- cover_decision
- ad_copy_direction
- overall_judgment

现在开始处理输入。"""

VIDEO_ANALYSIS_PROMPT_TEMPLATE = """角色：欧美短视频广告素材分析师。请基于上传的视频文件和补充商品信息，生成可供「文案创作」与「封面生成」使用的视频素材分析。

输入：
- 商品标题：{product_title}
- 商品链接：{product_url}
- 商品主图：{main_image_url}
- 商品主图文件：已下载并标准化为 400x400 JPG，作为本次多模态 media 输入；请用它校验视频中的产品是否一致，并辅助判断正确使用方式。
- 视频元信息：{video_meta}

分析目标：
- 提取视频中真实可用的使用动作、手部位置、拍摄角度、构图、节奏和真实感线索
- 识别 video_text、voiceover、字幕、水印、贴纸、平台 UI 等内容，并明确哪些应被后续封面生成忽略
- 给出 cover_reference：最适合用作封面的动作瞬间、镜头距离、主体位置、可见结果和留白位置
- 判断画面是否偏亚洲电商/中文环境/截图感，并说明如何自然改写为欧美生活方式场景

关键帧要求：
- 必须输出 keyframes 数组，严格 3 帧。
- 三帧类型固定为 Hero Shot / Front View、Detail Close-up、Usage Scenario。
- timestamp 使用 MM:SS.mmm；无完美帧时选择最接近者，并在 reason 中说明限制。
- cover_reference.best_cover_reference_timestamp 必须优先从 keyframes 中选择最适合做封面参考的一帧。

输出要求：
请仅输出单个合法 JSON 对象，不要 markdown，不要代码块，不要解释文字。
JSON 字段必须包含：
- video_analysis：对象，包含 summary、detailed_description、product_identity、product_features、usage_logic、video_text、voiceover
- keyframes：数组，严格 3 个对象，每个对象包含 timestamp、type、visual_content、reason、use_for_generation
- cover_reference：对象，包含 best_cover_reference_timestamp、why_best_for_cover、cover_readability、recommended_reference_usage、not_recommended_elements
- localization_and_adaptation
- noise_and_risk_notes
- actions
- composition
- authenticity_cues
- ignore_elements
- cover_suggestions"""

AD_COPY_PROMPT_TEMPLATE = """角色：资深 Facebook / Instagram Reels 视频广告文案专家，熟悉欧美独立站 DTC 产品投放，擅长为短视频封面标题、广告正文和短描述生成高点击且不夸张的英文广告文案。

你的任务：
根据输入的产品分析和视频素材分析，生成 5 组适合欧美 Facebook / Reels 投放的短视频广告文案。

核心目标：

1. 前 1–2 秒抓住注意力
2. 让用户快速看懂产品价值
3. 文案要能配合视频封面和视频开头
4. 减少空泛卖点，转成具体生活场景
5. 适合中低客单产品制造即时下单冲动
6. 适合高客单产品突出质感、实用性和长期价值
7. 符合 Meta 广告政策，避免夸张、冒犯、敏感表达

输入信息：

- 商品标题：{product_title}
- 商品主图 URL：{main_image_url}
- 产品分析：{product_analysis}
- 视频素材分析：{video_analysis}
- 当前日期：{current_date}

优先参考顺序：

1. 产品分析中的 <使用方式解析>
2. 产品分析中的 <视觉生成分类与封面策略>
3. 产品分析中的 <封面画面决策>
4. 产品分析中的 <广告文案方向>
5. 视频素材分析中的 video_text 与 voiceover
6. 视频素材分析中的 cover_reference
7. 产品定义、核心功能、物理特征、综合判断

文案策略：

- 不要写成传统广告口号
- 要像真实短视频里的自然引导
- 用具体生活场景替代抽象卖点
- 用可视化结果替代夸张承诺
- 用轻痛点 + 轻解决方案，不制造焦虑
- 不要过度恐吓、羞辱或暗示用户有缺陷
- 不要使用医疗、财务、身体缺陷、年龄歧视等高风险表达
- 不要承诺无法验证的结果
- 不要使用 “guaranteed”, “miracle”, “cure”, “permanent”, “100%”, “instantly fixes everything” 等夸张词
- 如果产品效果存在推断，只能写成温和表达
- 若产品属于工具、家居、车品、宠物、厨房、收纳类，优先强调省力、整洁、方便、耐用、日常实用
- 若产品适合季节场景，请结合当前日期推演未来 2 个月内的自然生活需求，例如换季、庭院整理、假日准备、家庭收纳、车内维护、厨房清洁、宠物护理等
- 季节性表达必须自然，不要强行套节日

人群心理：
主要面向欧美 30–65 岁成熟消费群体，包括家庭支柱、车主、宠物主、园艺爱好者、厨房/家居用品购买者、银发族及其家庭成员。

他们更重视：

- 好用
- 省事
- 耐用
- 清楚
- 不复杂
- 看得见的生活改善
- 现在就能用上的价值

如果产品明显不适合 30–65 岁人群，可根据产品分析调整目标人群，但必须保持欧美短视频广告语气。

5 组文案方向要求：
5 组必须角度不同，禁止只是换词。

建议覆盖以下方向中的 5 种：

1. 痛点解决型：轻微点出麻烦，然后给出解决方案
2. 使用场景型：把产品放进具体生活场景
3. 结果向往型：突出使用后的轻松、整洁、方便
4. 季节/时间型：结合未来 2 个月生活需求
5. 实用工具型：突出省力、好用、日常高频
6. 礼品/家庭型：适合送礼或家庭使用时使用
7. 低门槛尝试型：强调简单、容易上手
8. 对比改善型：突出 Before / After，但不得夸张

输出语言：

- 默认英文 + 中文翻译
- 英文用于广告投放
- 中文仅用于内部理解
- 英文要自然、简短、像母语广告文案
- 中文翻译要忠实表达英文含义，不需要意译扩写

英文长度限制：

- title ≤ 42 个英文字符，优先 3 到 7 个英文单词，用作封面叠字
- message ≤ 180 个英文字符，用作广告正文
- description ≤ 60 个英文字符，用作短描述或副标题

字符控制要求：

- title 必须短，适合视频封面或前 1 秒钩子
- message 必须适合短视频正文或广告主文案
- description 必须自然，概括场景价值，不要写成行动按钮
- Emoji 可用，但每组最多 1 个；如果会影响严肃感或 Meta 风险，则不用
- 不要使用全大写长句
- 不要使用过多感叹号
- 不要使用标题党

description 建议：
可根据产品类型选择短语风格，例如：

- Road Trips Made Safer
- Everyday Cleanup Made Easier
- A Simple Upgrade For Home
- Better Light When You Need It
- Built For Busy Routines

禁止事项：

- 禁止输出解释文字
- 禁止 markdown
- 禁止代码块
- 禁止输出 JSON 之外的任何内容
- 禁止编造产品没有的功能
- 禁止夸大效果
- 禁止使用平台敏感表达
- 禁止 5 组使用同一套句式
- 禁止把中文翻译写得比英文更夸张

输出格式：
请仅输出单个合法 JSON 对象，根字段为 ad_copy_sets。

ad_copy_sets 是数组，必须包含 5 个元素。

每个元素必须包含以下字段：

- id：整数，范围 1–5
- angle：中文说明该组文案的测试角度
- english：对象，包含 title、message、description
- chinese_translation：对象，包含 title、message、description
- usage_note：中文说明这组适合搭配哪类视频画面或封面方向

字段含义必须严格对齐：
- title：对应业务格式里的“标题”，也是封面图里由图片模型原生嵌入的唯一可读 hook。
- message：对应业务格式里的“文案”，不要写得像按钮。
- description：对应业务格式里的“描述”，是一句短描述/副标题。

最终文案能被格式化为：
标题: <english.title>
文案: <english.message>
描述: <english.description>

输出 JSON 结构如下：

{
  "ad_copy_sets": [
    {
      "id": 1,
      "angle": "痛点解决型",
      "english": {"title": "", "message": "", "description": ""},
      "chinese_translation": {"title": "", "message": "", "description": ""},
      "usage_note": ""
    },
    {
      "id": 2,
      "angle": "使用场景型",
      "english": {"title": "", "message": "", "description": ""},
      "chinese_translation": {"title": "", "message": "", "description": ""},
      "usage_note": ""
    },
    {
      "id": 3,
      "angle": "结果向往型",
      "english": {"title": "", "message": "", "description": ""},
      "chinese_translation": {"title": "", "message": "", "description": ""},
      "usage_note": ""
    },
    {
      "id": 4,
      "angle": "季节/时间型",
      "english": {"title": "", "message": "", "description": ""},
      "chinese_translation": {"title": "", "message": "", "description": ""},
      "usage_note": ""
    },
    {
      "id": 5,
      "angle": "实用工具型",
      "english": {"title": "", "message": "", "description": ""},
      "chinese_translation": {"title": "", "message": "", "description": ""},
      "usage_note": ""
    }
  ]
}

现在开始生成。"""


def _validate_http_url(url: str) -> str:
    cleaned = str(url or "").strip()
    parsed = urlparse(cleaned)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise VideoCoverGenerationError("请输入有效的商品链接")
    return cleaned


def _validate_video_path(video_path: str, video_filename: str) -> str:
    path = Path(video_path)
    if not path.is_file():
        raise VideoCoverGenerationError("视频文件不存在")
    suffix = (Path(video_filename or path.name).suffix or path.suffix).lower()
    if suffix not in ALLOWED_VIDEO_EXTENSIONS:
        raise VideoCoverGenerationError("仅支持 MP4 / MOV / MPEG / AVI / WEBM 视频")
    return suffix


def _product_value(product: Any, key: str) -> str:
    if isinstance(product, dict):
        return str(product.get(key) or "").strip()
    return str(getattr(product, key, "") or "").strip()


def _product_any(product: Any, key: str) -> Any:
    if isinstance(product, dict):
        return product.get(key)
    return getattr(product, key, None)


def _nested_raw_value(product: Any, *keys: str) -> Any:
    raw = _product_any(product, "raw")
    current = raw
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _product_description(product: Any) -> str:
    for value in (
        _product_value(product, "description"),
        _nested_raw_value(product, "product", "body_html"),
        _nested_raw_value(product, "product", "description"),
        _nested_raw_value(product, "product", "descriptionHtml"),
    ):
        text = str(value or "").strip()
        if text:
            return text[:4000]
    return "未提取到商品描述"


def _product_price_text(product: Any) -> str:
    price_min = _product_any(product, "price_min")
    price_max = _product_any(product, "price_max")
    currency = _product_value(product, "currency") or "USD"
    if price_min is not None and price_max is not None:
        if price_min == price_max:
            return f"{currency} {price_min}"
        return f"{currency} {price_min} - {price_max}"
    if price_min is not None:
        return f"{currency} {price_min}"
    return "未提取到价格"


def _fetch_product_image(url: str) -> bytes:
    try:
        response = requests.get(
            url,
            headers={"User-Agent": "Mozilla/5.0", "Accept": "image/*,*/*"},
            timeout=30,
        )
        response.raise_for_status()
    except requests.RequestException as exc:
        raise VideoCoverGenerationError(f"商品主图下载失败：{exc}") from exc
    if not response.content:
        raise VideoCoverGenerationError("商品主图下载失败：响应为空")
    return response.content


def _open_rgb_image(payload: bytes) -> Image.Image:
    try:
        with Image.open(BytesIO(payload)) as img:
            return img.convert("RGB")
    except Exception as exc:
        raise VideoCoverGenerationError("图片解析失败") from exc


def _read_image_file(path: str | os.PathLike[str]) -> Image.Image:
    try:
        with Image.open(path) as img:
            return img.convert("RGB")
    except Exception as exc:
        raise VideoCoverGenerationError("视频抽帧图片解析失败") from exc


def _contain_on_panel(image: Image.Image, size: tuple[int, int]) -> Image.Image:
    panel = Image.new("RGB", size, (248, 250, 252))
    fitted = ImageOps.contain(image, size, method=Image.Resampling.LANCZOS)
    x = (size[0] - fitted.width) // 2
    y = (size[1] - fitted.height) // 2
    panel.paste(fitted, (x, y))
    return panel


def _png_bytes(image: Image.Image) -> bytes:
    out = BytesIO()
    image.save(out, format="PNG")
    return out.getvalue()


def _jpg_bytes(image: Image.Image) -> bytes:
    out = BytesIO()
    image.save(out, format="JPEG", quality=92, optimize=True)
    return out.getvalue()


def normalize_product_image_jpg(product_image_bytes: bytes) -> bytes:
    image = _open_rgb_image(product_image_bytes)
    panel = Image.new("RGB", PRODUCT_IMAGE_SIZE, (255, 255, 255))
    fitted = ImageOps.contain(image, PRODUCT_IMAGE_SIZE, method=Image.Resampling.LANCZOS)
    x = (PRODUCT_IMAGE_SIZE[0] - fitted.width) // 2
    y = (PRODUCT_IMAGE_SIZE[1] - fitted.height) // 2
    panel.paste(fitted, (x, y))
    return _jpg_bytes(panel)


def normalize_cover_png(image_bytes: bytes) -> tuple[bytes, int, int]:
    image = _open_rgb_image(image_bytes)
    fitted = ImageOps.fit(
        image,
        OUTPUT_SIZE,
        method=Image.Resampling.LANCZOS,
        centering=(0.5, 0.5),
    )
    return _png_bytes(fitted), OUTPUT_SIZE[0], OUTPUT_SIZE[1]


def _frame_panel(frame_paths: list[str], size: tuple[int, int]) -> Image.Image:
    width, height = size
    if not frame_paths:
        return Image.new("RGB", size, (248, 250, 252))
    if len(frame_paths) == 1:
        return _contain_on_panel(_read_image_file(frame_paths[0]), size)

    cols = 1 if len(frame_paths) <= 3 else 2
    rows = (len(frame_paths) + cols - 1) // cols
    cell_w = width // cols
    cell_h = height // rows
    panel = Image.new("RGB", size, (248, 250, 252))
    for idx, path in enumerate(frame_paths):
        row = idx // cols
        col = idx % cols
        cell = _contain_on_panel(_read_image_file(path), (cell_w, cell_h))
        panel.paste(cell, (col * cell_w, row * cell_h))
    return panel


def build_reference_image(product_image_bytes: bytes, frame_path: str | list[str]) -> bytes:
    product_image = _open_rgb_image(product_image_bytes)
    frame_paths = [frame_path] if isinstance(frame_path, str) else [str(path) for path in frame_path if str(path)]
    width, height = REFERENCE_SIZE
    product_h = height // 2 if len(frame_paths) <= 1 else 620
    product_panel = _contain_on_panel(product_image, (width, product_h))
    frames_panel = _frame_panel(frame_paths, (width, height - product_h))
    canvas = Image.new("RGB", REFERENCE_SIZE, (248, 250, 252))
    canvas.paste(product_panel, (0, 0))
    canvas.paste(frames_panel, (0, product_h))
    return _png_bytes(canvas)


def build_product_analysis_context(
    product: Any,
    *,
    product_title: str,
    product_url: str,
    main_image_url: str,
) -> str:
    price_text = _product_price_text(product)
    return "\n".join(
        line for line in [
            "<产品核心理解>",
            f"- 商品标题：{product_title}",
            f"- 商品链接：{product_url}",
            f"- 价格线索：{price_text}" if price_text != "未提取到价格" else "",
            "<使用方式>",
            "- 以商品图片和精选视频帧中的可见动作/安装/操作关系为准；不要臆造不可信的用途。",
            "<外观与结构>",
            f"- 商品主图：{main_image_url}",
            "- 产品外观、颜色、材质、比例和可见功能部件必须以 product_image_* 为最高优先级。",
        ] if line
    )


def build_video_analysis_context(video_info: dict[str, Any] | None = None) -> str:
    info = video_info or {}
    resolution = str(info.get("resolution") or "").strip()
    duration = float(info.get("duration") or 0.0)
    parts = ["精选视频帧来自上传视频，用于参考真实使用动作、拍摄角度、手部位置、构图和生活化质感。"]
    if resolution:
        parts.append(f"视频分辨率：{resolution}")
    if duration > 0:
        parts.append(f"视频时长：{duration:.1f}s")
    return "\n".join(parts)


def build_product_analysis_prompt(
    product: Any,
    *,
    product_title: str,
    main_image_url: str,
) -> str:
    return (
        PRODUCT_ANALYSIS_PROMPT_TEMPLATE
        .replace("{title}", product_title)
        .replace("{description}", _product_description(product))
        .replace("{image_url}", main_image_url)
        .replace("{price_info}", _product_price_text(product))
    )


def build_video_analysis_prompt(
    *,
    product_title: str,
    product_url: str,
    main_image_url: str,
    video_info: dict[str, Any] | None = None,
) -> str:
    meta = build_video_analysis_context(video_info)
    return (
        VIDEO_ANALYSIS_PROMPT_TEMPLATE
        .replace("{product_title}", product_title)
        .replace("{product_url}", product_url)
        .replace("{main_image_url}", main_image_url)
        .replace("{video_meta}", meta)
    )


def build_ad_copy_prompt(
    *,
    product_title: str = "",
    main_image_url: str = "",
    product_analysis: str,
    video_analysis: str,
    current_date: str,
) -> str:
    return (
        AD_COPY_PROMPT_TEMPLATE
        .replace("{product_title}", product_title or "未提供")
        .replace("{main_image_url}", main_image_url or "未提供")
        .replace("{product_analysis}", product_analysis)
        .replace("{video_analysis}", video_analysis)
        .replace("{current_date}", current_date)
    )


def _clip_for_cover_brief(value: Any, limit: int = 320) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "...[已截断]"


def build_cover_brief(
    *,
    product_title: str,
    main_image_url: str = "",
    product_analysis: str,
    video_analysis: str,
    reference_frames: list[dict[str, Any]] | None = None,
) -> str:
    frame_lines = []
    for frame in reference_frames or []:
        timestamp = str(frame.get("timestamp") or "").strip()
        frame_type = str(frame.get("type") or frame.get("source") or "").strip()
        visual = str(frame.get("visual_content") or frame.get("reason") or "").strip()
        if timestamp or frame_type or visual:
            frame_lines.append(f"- {timestamp} {frame_type}: {_clip_for_cover_brief(visual, 120)}".strip())
    if not frame_lines:
        frame_lines.append("- 未提取到结构化关键帧，参考图使用视频首帧兜底。")
    return "\n".join(
        [
            "<cover_brief>",
            f"product_title: {product_title}",
            f"main_image_url: {main_image_url or '未提供'}",
            f"product_analysis: {_clip_for_cover_brief(product_analysis)}",
            f"video_analysis: {_clip_for_cover_brief(video_analysis)}",
            "reference_frames:",
            *frame_lines,
            "</cover_brief>",
        ]
    )


def _strip_json_fence(text: str) -> str:
    raw = (text or "").strip()
    if raw.startswith("```"):
        parts = raw.split("```")
        raw = parts[1] if len(parts) > 1 else raw
        if raw.strip().lower().startswith("json"):
            raw = raw.strip()[4:]
    return raw.strip()


COPY_TEXT_FIELDS = ("title", "message", "description")
LEGACY_COPY_TEXT_FIELDS = {
    "title": "headline",
    "message": "body_text",
    "description": "cta",
}


def _normalize_copy_text_fields(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    normalized: dict[str, str] = {}
    for key in COPY_TEXT_FIELDS:
        text = value.get(key)
        if text is None:
            text = value.get(LEGACY_COPY_TEXT_FIELDS[key])
        normalized[key] = str(text or "").strip()
    return normalized


def _normalize_ad_copy_item(item: Any, *, index: int, require_translation: bool = True) -> dict[str, Any]:
    if not isinstance(item, dict):
        raise VideoCoverGenerationError(f"文案创作失败：第 {index} 组字段不完整")
    missing_top = {"id", "angle", "english", "chinese_translation", "usage_note"} - set(item)
    if missing_top:
        raise VideoCoverGenerationError(f"文案创作失败：第 {index} 组字段不完整")
    english = _normalize_copy_text_fields(item.get("english"))
    missing_english = [key for key in COPY_TEXT_FIELDS if not english.get(key)]
    if missing_english:
        raise VideoCoverGenerationError(f"文案创作失败：第 {index} 组 english 字段不完整")
    chinese = _normalize_copy_text_fields(item.get("chinese_translation"))
    if require_translation:
        missing_chinese = [key for key in COPY_TEXT_FIELDS if not chinese.get(key)]
        if missing_chinese:
            raise VideoCoverGenerationError(f"文案创作失败：第 {index} 组 chinese_translation 字段不完整")
    normalized = dict(item)
    normalized["english"] = english
    normalized["chinese_translation"] = chinese
    return normalized


def normalize_ad_copy_payload(ad_copy_payload: dict[str, Any] | None, *, require_five: bool = False) -> dict[str, Any]:
    if not isinstance(ad_copy_payload, dict):
        if require_five:
            raise VideoCoverGenerationError("文案创作失败：ad_copy_sets 必须包含 5 组文案")
        return {"ad_copy_sets": []}
    ad_copy_sets = ad_copy_payload.get("ad_copy_sets")
    if not isinstance(ad_copy_sets, list) or (require_five and len(ad_copy_sets) != 5):
        raise VideoCoverGenerationError("文案创作失败：ad_copy_sets 必须包含 5 组文案")
    normalized_items = [
        _normalize_ad_copy_item(item, index=idx, require_translation=True)
        for idx, item in enumerate(ad_copy_sets, start=1)
    ]
    payload = dict(ad_copy_payload)
    payload["ad_copy_sets"] = normalized_items
    return payload


def _parse_ad_copy_response(response: dict[str, Any]) -> dict[str, Any]:
    payload = response.get("json")
    if not isinstance(payload, dict):
        raw = _strip_json_fence(str(response.get("text") or ""))
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise VideoCoverGenerationError("文案创作失败：模型未返回合法 JSON") from exc
    return normalize_ad_copy_payload(payload, require_five=True)


def generate_product_analysis(
    *,
    product: Any,
    product_title: str,
    main_image_url: str,
    product_image_path: str | os.PathLike[str] | None = None,
    provider: str | None = None,
    model: str | None = None,
    user_id: int | None = None,
    task_id: str | None = None,
    invoke_generate_fn: Callable[..., dict] = llm_client.invoke_generate,
) -> str:
    selection = resolve_text_model_selection("product_analysis", provider, model)
    prompt = build_product_analysis_prompt(
        product,
        product_title=product_title,
        main_image_url=main_image_url,
    )
    try:
        response = invoke_generate_fn(
            "video_cover.product_analysis",
            prompt=prompt,
            media=str(product_image_path) if product_image_path else None,
            user_id=user_id,
            project_id=task_id,
            provider_override=selection.provider,
            model_override=selection.model,
            temperature=0.2,
            max_output_tokens=3600,
            billing_extra={"source": "video_cover"},
        )
    except VideoCoverGenerationError:
        raise
    except Exception as exc:
        raise VideoCoverGenerationError(f"产品分析失败：{exc}") from exc
    if isinstance(response.get("json"), dict):
        text = json.dumps(response["json"], ensure_ascii=False)
    else:
        text = str(response.get("text") or "").strip()
    if not text:
        raise VideoCoverGenerationError("产品分析失败：模型未返回内容")
    return text


def generate_video_analysis(
    *,
    video_path: str,
    product_title: str,
    product_url: str,
    main_image_url: str,
    product_image_path: str | os.PathLike[str] | None = None,
    video_info: dict[str, Any] | None = None,
    provider: str | None = None,
    model: str | None = None,
    user_id: int | None = None,
    task_id: str | None = None,
    invoke_generate_fn: Callable[..., dict] = llm_client.invoke_generate,
) -> str:
    selection = resolve_text_model_selection("video_analysis", provider, model)
    prompt = build_video_analysis_prompt(
        product_title=product_title,
        product_url=product_url,
        main_image_url=main_image_url,
        video_info=video_info,
    )
    media = prepare_video_for_llm(
        video_path,
        REVIEW_480P_AUDIO,
        output_dir=Path(video_path).parent,
    )
    try:
        media_inputs: list[str] | str = media.llm_path
        if product_image_path:
            media_inputs = [media.llm_path, str(product_image_path)]
        response = invoke_generate_fn(
            "video_cover.video_analysis",
            prompt=prompt,
            media=media_inputs,
            user_id=user_id,
            project_id=task_id,
            provider_override=selection.provider,
            model_override=selection.model,
            temperature=0.2,
            max_output_tokens=3600,
            billing_extra={
                "source": "video_cover",
                "media_optimization": media_debug_snapshot(media),
            },
        )
    except VideoCoverGenerationError:
        raise
    except Exception as exc:
        raise VideoCoverGenerationError(f"视频分析失败：{exc}") from exc
    finally:
        cleanup_optimized_media(media)
    if isinstance(response.get("json"), dict):
        text = json.dumps(response["json"], ensure_ascii=False)
    else:
        text = str(response.get("text") or "").strip()
    if not text:
        raise VideoCoverGenerationError("视频分析失败：模型未返回内容")
    return text


def generate_ad_copy_sets(
    *,
    product_title: str = "",
    main_image_url: str = "",
    product_analysis: str,
    video_analysis: str,
    current_date: str,
    provider: str | None = None,
    model: str | None = None,
    user_id: int | None = None,
    task_id: str | None = None,
    invoke_chat_fn: Callable[..., dict] = llm_client.invoke_chat,
) -> dict[str, Any]:
    selection = resolve_text_model_selection("ad_copy", provider, model)
    prompt = build_ad_copy_prompt(
        product_title=product_title,
        main_image_url=main_image_url,
        product_analysis=product_analysis,
        video_analysis=video_analysis,
        current_date=current_date,
    )
    try:
        response = invoke_chat_fn(
            "video_cover.ad_copy",
            messages=[
                {"role": "system", "content": "你只输出一个合法 JSON 对象，不输出解释、Markdown 或代码块。"},
                {"role": "user", "content": prompt},
            ],
            user_id=user_id,
            project_id=task_id,
            provider_override=selection.provider,
            model_override=selection.model,
            temperature=0.4,
            max_tokens=2400,
            response_format={"type": "json_object"},
            billing_extra={"source": "video_cover"},
        )
    except VideoCoverGenerationError:
        raise
    except Exception as exc:
        raise VideoCoverGenerationError(f"文案创作失败：{exc}") from exc
    return _parse_ad_copy_response(response)


def build_platform_prompt(
    spec: PlatformSpec,
    *,
    product_title: str,
    product_url: str,
    main_image_url: str = "",
    product_analysis: str,
    video_analysis: str,
    ad_copy_sets: str,
    reference_frames: list[dict[str, Any]] | None = None,
) -> str:
    cover_brief = build_cover_brief(
        product_title=product_title,
        main_image_url=main_image_url,
        product_analysis=product_analysis,
        video_analysis=video_analysis,
        reference_frames=reference_frames,
    )
    return CREATIVE_DIRECTOR_PROMPT_TEMPLATE.format(
        product_title=product_title,
        product_url=product_url,
        main_image_url=main_image_url,
        cover_brief=cover_brief,
        ad_copy_sets=ad_copy_sets,
    )


def _resolve_local_image_credentials() -> tuple[str, str]:
    cfg = get_provider_config(LOCAL_IMAGE_PROVIDER_CODE)
    api_key = str(getattr(cfg, "api_key", "") or "").strip() if cfg else ""
    base_url = str(getattr(cfg, "base_url", "") or "").strip() if cfg else ""
    if not api_key:
        raise VideoCoverGenerationError(
            f"封面生成失败：缺少供应商配置 {LOCAL_IMAGE_PROVIDER_CODE}.api_key，请在设置页配置本地接口 API key"
        )
    return api_key, (base_url or LOCAL_IMAGE_BASE_URL_DEFAULT).rstrip("/")


def _decode_image_response_payload(
    payload: dict[str, Any],
    *,
    get_fn: Callable[..., Any] = requests.get,
) -> tuple[bytes, str]:
    data = payload.get("data") if isinstance(payload, dict) else None
    first = data[0] if isinstance(data, list) and data else None
    if not isinstance(first, dict):
        raise VideoCoverGenerationError("封面生成失败：本地接口未返回图像数据")
    b64_json = first.get("b64_json")
    if b64_json:
        try:
            return base64.b64decode(str(b64_json), validate=False), "image/png"
        except Exception as exc:
            raise VideoCoverGenerationError(f"封面生成失败：本地接口图像 base64 解析失败：{exc}") from exc
    image_url = first.get("url") or ((first.get("image_url") or {}).get("url") if isinstance(first.get("image_url"), dict) else "")
    if image_url:
        try:
            response = get_fn(str(image_url), timeout=30)
        except requests.RequestException as exc:
            raise VideoCoverGenerationError(f"封面生成失败：本地接口图片下载失败：{exc}") from exc
        if getattr(response, "status_code", 0) >= 400:
            raise VideoCoverGenerationError(f"封面生成失败：本地接口图片下载失败（HTTP {response.status_code}）")
        return response.content, response.headers.get("Content-Type", "image/png") if hasattr(response, "headers") else "image/png"
    raise VideoCoverGenerationError("封面生成失败：本地接口未返回 b64_json 或 url")


def generate_local_cover_image(
    prompt: str,
    *,
    source_image: bytes,
    source_mime: str,
    model: str,
    api_key: str | None = None,
    base_url: str | None = None,
    post_fn: Callable[..., Any] = requests.post,
    get_fn: Callable[..., Any] = requests.get,
) -> tuple[bytes, str]:
    if api_key is None or base_url is None:
        resolved_api_key, resolved_base_url = _resolve_local_image_credentials()
        api_key = api_key if api_key is not None else resolved_api_key
        base_url = base_url if base_url is not None else resolved_base_url
    if not api_key:
        raise VideoCoverGenerationError(f"封面生成失败：缺少供应商配置 {LOCAL_IMAGE_PROVIDER_CODE}.api_key")
    api_base = (base_url or LOCAL_IMAGE_BASE_URL_DEFAULT).rstrip("/")
    form_data = {
        "model": (model or DEFAULT_IMAGE_MODEL).strip(),
        "prompt": prompt,
        "n": "1",
        "size": "1024x1536",
    }
    files = {
        "image": ("reference.png", source_image, source_mime or "image/png"),
    }
    try:
        response = post_fn(
            f"{api_base}/images/edits",
            headers={
                "Authorization": f"Bearer {api_key}",
            },
            data=form_data,
            files=files,
            timeout=360,
        )
    except requests.RequestException as exc:
        raise VideoCoverGenerationError(f"封面生成失败：本地接口请求失败：{exc}") from exc

    try:
        response_json = response.json()
    except Exception:
        response_json = {}
    if getattr(response, "status_code", 0) >= 400:
        error = response_json.get("error") if isinstance(response_json, dict) else None
        if isinstance(error, dict):
            message = str(error.get("message") or "").strip()
        else:
            message = str(error or "").strip()
        message = message or str(getattr(response, "text", "") or "").strip() or f"HTTP {response.status_code}"
        raise VideoCoverGenerationError(f"封面生成失败：本地接口请求失败（HTTP {response.status_code}）：{message}")
    return _decode_image_response_payload(response_json, get_fn=get_fn)


def generate_cover_image(
    prompt: str,
    *,
    source_image: bytes,
    source_mime: str,
    selection: ModelSelection,
    user_id: int | None = None,
    task_id: str | None = None,
    image_generate_fn: Callable[..., tuple[bytes, str]] | None = None,
) -> tuple[bytes, str]:
    if image_generate_fn is not None:
        return image_generate_fn(
            prompt,
            source_image=source_image,
            source_mime=source_mime,
            model=selection.model,
            user_id=user_id,
            project_id=task_id,
            service="video_cover.generate",
            channel=selection.provider,
        )
    if selection.provider == "local":
        return generate_local_cover_image(
            prompt,
            source_image=source_image,
            source_mime=source_mime,
            model=selection.model,
        )
    if selection.provider == "openrouter":
        return gemini_image.generate_image(
            prompt,
            source_image=source_image,
            source_mime=source_mime,
            model=selection.model,
            user_id=user_id,
            project_id=task_id,
            service="video_cover.generate",
            channel="openrouter",
        )
    if selection.provider == "gemini_vertex_adc":
        return gemini_image.generate_image(
            prompt,
            source_image=source_image,
            source_mime=source_mime,
            model=selection.model,
            user_id=user_id,
            project_id=task_id,
            service="video_cover.generate",
            channel="cloud_adc",
        )
    raise VideoCoverGenerationError(f"封面生成失败：不支持的供应商 {selection.provider}")


def _object_key(user_id: int | None, task_id: str, filename: str) -> str:
    uid = str(user_id or "anonymous")
    return f"artifacts/video_cover/{uid}/{task_id}/{filename}"


def _write_png_artifact(user_id: int | None, task_id: str, filename: str, payload: bytes) -> str:
    key = _object_key(user_id, task_id, filename)
    local_media_storage.write_bytes(key, payload)
    return key


def normalize_image_count(value: Any, default: int = 2) -> int:
    try:
        count = int(value)
    except (TypeError, ValueError):
        return default
    return count if count in ALLOWED_IMAGE_COUNTS else default


def _json_object_from_text(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    raw = _strip_json_fence(str(value or ""))
    if not raw:
        return {}
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _video_analysis_nested(payload: dict[str, Any]) -> dict[str, Any]:
    nested = payload.get("video_analysis")
    return nested if isinstance(nested, dict) else {}


def _reference_frame_specs(video_analysis_text: Any) -> list[dict[str, Any]]:
    payload = _json_object_from_text(video_analysis_text)
    nested = _video_analysis_nested(payload)
    keyframes = payload.get("keyframes")
    if not isinstance(keyframes, list):
        keyframes = nested.get("keyframes") if isinstance(nested.get("keyframes"), list) else []

    specs: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in keyframes:
        if not isinstance(item, dict):
            continue
        timestamp = str(item.get("timestamp") or "").strip()
        if not timestamp or timestamp in seen:
            continue
        seen.add(timestamp)
        specs.append({
            "timestamp": timestamp,
            "type": str(item.get("type") or "").strip(),
            "source": "keyframes",
            "visual_content": str(item.get("visual_content") or "").strip(),
            "reason": str(item.get("reason") or "").strip(),
        })

    cover_reference = payload.get("cover_reference")
    if not isinstance(cover_reference, dict):
        cover_reference = nested.get("cover_reference") if isinstance(nested.get("cover_reference"), dict) else {}
    best_timestamp = str(
        (cover_reference or {}).get("best_cover_reference_timestamp")
        or (cover_reference or {}).get("timestamp")
        or ""
    ).strip()
    if best_timestamp and best_timestamp not in seen:
        specs.append({
            "timestamp": best_timestamp,
            "type": "Best Cover Reference",
            "source": "cover_reference",
            "visual_content": "",
            "reason": str((cover_reference or {}).get("why_best_for_cover") or "").strip(),
        })
    return specs[:4]


def _extract_reference_frames(
    *,
    video_path: str,
    output_dir: str,
    specs: list[dict[str, Any]],
    reference_frame_extractor: Callable[..., str | None],
) -> list[dict[str, Any]]:
    frames: list[dict[str, Any]] = []
    for index, spec in enumerate(specs, start=1):
        timestamp = str(spec.get("timestamp") or "").strip()
        if not timestamp:
            continue
        try:
            frame_path = reference_frame_extractor(
                video_path,
                output_dir,
                timestamp=timestamp,
                index=index,
            )
        except Exception:
            frame_path = None
        if not frame_path or not Path(frame_path).is_file():
            continue
        item = dict(spec)
        item["path"] = str(frame_path)
        item["index"] = index
        frames.append(item)
    return frames


def _reference_frame_public_meta(frames: list[dict[str, Any]]) -> list[dict[str, Any]]:
    keys = ("index", "timestamp", "type", "source", "visual_content", "reason", "path", "object_key")
    return [{key: frame.get(key) for key in keys if frame.get(key) not in (None, "")} for frame in frames]


def _persist_reference_frame_artifacts(
    *,
    user_id: int | None,
    task_id: str,
    frames: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    persisted: list[dict[str, Any]] = []
    for frame in frames:
        next_frame = dict(frame)
        frame_path = Path(str(frame.get("path") or ""))
        if frame_path.is_file():
            suffix = frame_path.suffix.lower() if frame_path.suffix else ".jpg"
            filename = f"reference_frame_{int(frame.get('index') or len(persisted) + 1)}{suffix}"
            key = _object_key(user_id, task_id, filename)
            local_path = local_media_storage.write_bytes(key, frame_path.read_bytes())
            next_frame["object_key"] = key
            next_frame["path"] = str(local_path)
        persisted.append(next_frame)
    return persisted


def _ad_copy_items(ad_copy_payload: dict[str, Any] | None) -> list[dict[str, Any]]:
    try:
        payload = normalize_ad_copy_payload(ad_copy_payload, require_five=False)
    except VideoCoverGenerationError:
        return []
    return [item for item in payload.get("ad_copy_sets", []) if isinstance(item, dict)]


def _copy_for_cover(ad_copy_payload: dict[str, Any] | None, index: int) -> dict[str, Any]:
    items = _ad_copy_items(ad_copy_payload)
    if not items:
        return {}
    return dict(items[(index - 1) % len(items)])


def _cover_hook(copy_item: dict[str, Any]) -> str:
    english = copy_item.get("english") if isinstance(copy_item.get("english"), dict) else {}
    return str(english.get("title") or "").strip()


def format_ad_copy_text(copy_item: dict[str, Any]) -> str:
    english = copy_item.get("english") if isinstance(copy_item.get("english"), dict) else {}
    return "\n".join((
        f"标题: {str(english.get('title') or '').strip()}",
        f"文案: {str(english.get('message') or '').strip()}",
        f"描述: {str(english.get('description') or '').strip()}",
    ))


def generate_video_covers(
    *,
    product_url: str,
    video_path: str,
    video_filename: str,
    product_title: str | None = None,
    main_image_url: str | None = None,
    product_image_path: str | os.PathLike[str] | None = None,
    user_id: int | None = None,
    task_id: str | None = None,
    cover_provider: str = DEFAULT_IMAGE_CHANNEL,
    cover_model: str = DEFAULT_IMAGE_MODEL,
    cover_execution_mode: str | None = DEFAULT_COVER_EXECUTION_MODE,
    product_analysis_provider: str | None = None,
    product_analysis_model: str | None = None,
    video_analysis_provider: str | None = None,
    video_analysis_model: str | None = None,
    ad_copy_provider: str | None = None,
    ad_copy_model: str | None = None,
    product_analysis_text: str | None = None,
    video_analysis_text: str | None = None,
    ad_copy_payload: dict[str, Any] | None = None,
    product_fetch_fn: Callable[[str], Any] = fetch_product_analysis,
    image_fetch_fn: Callable[[str], bytes] = _fetch_product_image,
    thumbnail_extractor: Callable[..., str | None] = extract_thumbnail,
    reference_frame_extractor: Callable[..., str | None] = extract_frame_at_timestamp,
    image_generate_fn: Callable[..., tuple[bytes, str]] | None = None,
    invoke_generate_fn: Callable[..., dict] = llm_client.invoke_generate,
    ad_copy_invoke_fn: Callable[..., dict] = llm_client.invoke_chat,
    current_date: str | None = None,
    image_count: int = 1,
    on_cover_done: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    product_url = _validate_http_url(product_url)
    _validate_video_path(video_path, video_filename)
    video_info = probe_media_info(video_path)
    image_count = normalize_image_count(image_count, default=1)

    provided_product_title = str(product_title or "").strip()
    provided_main_image_url = str(main_image_url or "").strip()
    needs_product_fetch = (
        not provided_product_title
        or not provided_main_image_url
        or not (product_analysis_text or "").strip()
    )
    if needs_product_fetch:
        product = product_fetch_fn(product_url)
        product_title = (provided_product_title or _product_value(product, "title")).strip()
        main_image_url = (provided_main_image_url or _product_value(product, "main_image_url")).strip()
    else:
        product_title = provided_product_title
        main_image_url = provided_main_image_url
        product = {"title": product_title, "main_image_url": main_image_url, "product_url": product_url}
    if not product_title:
        raise VideoCoverGenerationError("无法从商品链接提取商品标题")
    if not main_image_url:
        raise VideoCoverGenerationError("无法从商品链接提取商品主图")

    if product_image_path and Path(product_image_path).is_file():
        raw_image_bytes = Path(product_image_path).read_bytes()
    else:
        raw_image_bytes = image_fetch_fn(main_image_url)
    image_bytes = normalize_product_image_jpg(raw_image_bytes)
    task_id = (task_id or uuid.uuid4().hex).strip()
    product_selection = resolve_text_model_selection("product_analysis", product_analysis_provider, product_analysis_model)
    video_selection = resolve_text_model_selection("video_analysis", video_analysis_provider, video_analysis_model)
    ad_copy_selection = resolve_text_model_selection("ad_copy", ad_copy_provider, ad_copy_model)
    cover_selection = resolve_cover_model_selection(cover_provider, cover_model)
    execution_mode = normalize_cover_execution_mode(cover_selection.provider, cover_execution_mode)

    with tempfile.TemporaryDirectory(prefix="video_cover_") as work_dir:
        normalized_product_image_path = Path(work_dir) / "product_image.jpg"
        normalized_product_image_path.write_bytes(image_bytes)
        product_analysis = (product_analysis_text or "").strip() or generate_product_analysis(
            product=product,
            product_title=product_title,
            main_image_url=main_image_url,
            product_image_path=normalized_product_image_path,
            provider=product_selection.provider,
            model=product_selection.alias,
            user_id=user_id,
            task_id=task_id,
            invoke_generate_fn=invoke_generate_fn,
        )
        video_analysis = (video_analysis_text or "").strip() or generate_video_analysis(
            video_path=video_path,
            product_title=product_title,
            product_url=product_url,
            main_image_url=main_image_url,
            product_image_path=normalized_product_image_path,
            video_info=video_info,
            provider=video_selection.provider,
            model=video_selection.alias,
            user_id=user_id,
            task_id=task_id,
            invoke_generate_fn=invoke_generate_fn,
        )
        frame_specs = _reference_frame_specs(video_analysis)
        reference_frames = _extract_reference_frames(
            video_path=video_path,
            output_dir=work_dir,
            specs=frame_specs,
            reference_frame_extractor=reference_frame_extractor,
        )
        if reference_frames:
            reference_bytes = build_reference_image(raw_image_bytes, [frame["path"] for frame in reference_frames])
        else:
            thumbnail_path = thumbnail_extractor(video_path, work_dir, scale="1080:-1")
            if not thumbnail_path or not Path(thumbnail_path).is_file():
                raise VideoCoverGenerationError("视频抽帧失败")
            reference_frames = [{
                "index": 1,
                "timestamp": "",
                "type": "Thumbnail Fallback",
                "source": "thumbnail_fallback",
                "path": str(thumbnail_path),
            }]
            reference_bytes = build_reference_image(raw_image_bytes, thumbnail_path)
        reference_frames = _persist_reference_frame_artifacts(
            user_id=user_id,
            task_id=task_id,
            frames=reference_frames,
        )

    reference_key = _write_png_artifact(user_id, task_id, "reference.png", reference_bytes)
    raw_copy_payload = ad_copy_payload or generate_ad_copy_sets(
        product_title=product_title,
        main_image_url=main_image_url,
        product_analysis=product_analysis,
        video_analysis=video_analysis,
        current_date=current_date or date.today().isoformat(),
        provider=ad_copy_selection.provider,
        model=ad_copy_selection.alias,
        user_id=user_id,
        task_id=task_id,
        invoke_chat_fn=ad_copy_invoke_fn,
    )
    copy_payload = normalize_ad_copy_payload(raw_copy_payload, require_five=False)
    cover_jobs = []
    image_prompts = []
    covers: list[dict[str, Any]] = []
    spec = SOCIAL_REELS_SPEC

    def ordered_covers() -> list[dict[str, Any]]:
        return sorted(covers, key=lambda item: int(item.get("index") or 0))

    def build_result() -> dict[str, Any]:
        return {
            "task_id": task_id,
            "product": {
                "title": product_title,
                "main_image_url": main_image_url,
                "product_url": product_url,
            },
            "reference": {
                "object_key": reference_key,
                "frames": _reference_frame_public_meta(reference_frames),
            },
            "inputs": {
                "product_analysis": product_analysis,
                "video_analysis": video_analysis,
                "ad_copy_sets": copy_payload,
            },
            "model": {
                "channel": cover_selection.provider,
                "model_id": cover_selection.model,
                "execution_mode": execution_mode,
            },
            "image_count": image_count,
            "image_prompts": list(image_prompts),
            "models": {
                "product_analysis": {
                    "provider": product_selection.provider,
                    "model_id": product_selection.model,
                },
                "video_analysis": {
                    "provider": video_selection.provider,
                    "model_id": video_selection.model,
                },
                "ad_copy": {
                    "provider": ad_copy_selection.provider,
                    "model_id": ad_copy_selection.model,
                },
                "cover_generation": {
                    "provider": cover_selection.provider,
                    "model_id": cover_selection.model,
                    "execution_mode": execution_mode,
                },
            },
            "covers": ordered_covers(),
        }

    for index in range(1, image_count + 1):
        copy_item = _copy_for_cover(copy_payload, index)
        selected_copy_payload = {
            "selected_ad_copy": copy_item,
            "variant_index": index,
            "variant_count": image_count,
        }
        prompt = build_platform_prompt(
            spec,
            product_title=product_title,
            product_url=product_url,
            main_image_url=main_image_url,
            product_analysis=product_analysis,
            video_analysis=video_analysis,
            ad_copy_sets=json.dumps(selected_copy_payload, ensure_ascii=False, indent=2),
            reference_frames=reference_frames,
        )
        if image_count > 1:
            prompt += f"\n\n本次需要生成 {image_count} 张候选封面。当前是第 {index} 张，请基于 selected_ad_copy 做出不同构图或使用瞬间。"
        image_prompts.append({
            "index": index,
            "prompt": prompt,
            "source_ad_copy_id": copy_item.get("id"),
            "reference_frames": _reference_frame_public_meta(reference_frames),
        })
        cover_jobs.append({"index": index, "prompt": prompt, "copy_item": copy_item})

    def build_cover(job: dict[str, Any]) -> dict[str, Any]:
        index = int(job["index"])
        prompt = str(job["prompt"])
        copy_item = job["copy_item"] if isinstance(job.get("copy_item"), dict) else {}
        try:
            generated_bytes, _mime = generate_cover_image(
                prompt,
                source_image=reference_bytes,
                source_mime="image/png",
                user_id=user_id,
                task_id=task_id,
                selection=cover_selection,
                image_generate_fn=image_generate_fn,
            )
        except VideoCoverGenerationError:
            raise
        except Exception as exc:
            raise VideoCoverGenerationError(f"封面生成失败：{exc}") from exc

        png, width, height = normalize_cover_png(generated_bytes)
        platform = spec.platform if image_count == 1 else f"{spec.platform}_{index}"
        key = _write_png_artifact(user_id, task_id, f"{platform}.png", png)
        return {
            "platform": platform,
            "label": spec.label if image_count == 1 else f"{spec.label} #{index}",
            "index": index,
            "object_key": key,
            "width": width,
            "height": height,
            "source_ad_copy_id": copy_item.get("id"),
            "hook": _cover_hook(copy_item),
            "copy": copy_item,
            "formatted_copy": format_ad_copy_text(copy_item),
            "prompt": prompt,
        }

    def record_cover(cover: dict[str, Any]) -> None:
        covers.append(cover)
        if on_cover_done:
            on_cover_done(build_result())

    if execution_mode == "parallel" and len(cover_jobs) > 1:
        with ThreadPoolExecutor(max_workers=len(cover_jobs)) as executor:
            future_map = {executor.submit(build_cover, job): job for job in cover_jobs}
            for future in as_completed(future_map):
                record_cover(future.result())
    else:
        for job in cover_jobs:
            record_cover(build_cover(job))

    return build_result()
