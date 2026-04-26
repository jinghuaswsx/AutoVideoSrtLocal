# 多语言 ASR 重构 + 语言污染清理（设计文档）

- 日期：2026-04-26
- 分支：`feature/multilingual-asr-refactor`
- 状态：草案，待实施

## 1. 背景与目标

### 1.1 现状
- 视频翻译 pipeline 当前用两个 ASR：
  - **豆包 SeedASR v3**（`pipeline/asr.py`）：`zh / en` 主用
  - **ElevenLabs Scribe v2**（`pipeline/asr_scribe.py`，已是 v2）：其他语言兜底
- 路由是各 runtime 类内部硬编码（`runtime_omni._step_asr` 按 source_language 分发）
- 没有统一的 ASR adapter 抽象，新增 provider 要每处改

### 1.2 痛点
1. **多语言准确度不足**：豆包对 zh/en 强，但 es/pt/de 等场景不优；Scribe v2 在某些语言场景也不是最优解
2. **语言污染**：豆包识别西语视频时，静音/杂音段被错误识别为中文，污染下游 LLM 翻译
3. **路由硬编码**：管理员无法在 `/settings` 调整 ASR 选择

### 1.3 目标
1. 引入 **Cohere Transcribe**（cohere-transcribe-03-2026，14 语言 SOTA）作为新 provider
2. 抽出统一 ASR adapter 层（仿照 `appcore/llm_providers/`）
3. 建立"主 ASR + fallback ASR"路由表，默认硬编码 + 管理员可在 `/settings` 覆盖
4. 加入"语言污染检测 + fallback 重转"机制：
   - 每个 utterance 跑 fast-langdetect
   - 非主语言段 → ffmpeg 切片 → fallback ASR（force language=主语言）→ 二次检测
   - 仍污染 → 删除 + 时间合并到相邻段
5. 用户前端零感知，路由完全在后端

### 1.4 非目标
- **不接** OpenAI `gpt-audio-mini`：无时间戳，多语言能力一般，已确认放弃
- 不自建 Cohere 模型推理（走官方托管 API）
- 不增加用户级 ASR 选择 UI（保持自动路由）
- 不改 `_step_asr_normalize` 现有 LLM 翻译逻辑

## 2. 架构

```
┌──────────────────────────────────────────────┐
│  Pipeline (runtime / runtime_omni /          │
│            runtime_multi / runtime_de/fr)    │
│  └─ _step_asr() → asr_router.transcribe(...) │
└──────────────────┬───────────────────────────┘
                   │
                   ▼
┌──────────────────────────────────────────────┐
│  ASR Router (appcore/asr_router.py)          │
│  ├─ resolve_route(source_language)           │
│  │  → (primary_adapter, fallback_adapter)    │
│  ├─ transcribe(audio_path, source_language)  │
│  │  调主 → 污染检测 → 切片重转 → 时间合并    │
│  └─ load_route_config()                      │
│     从 system_settings.asr_route_config       │
│     合并 DEFAULT_ROUTE_TABLE                  │
└──────────────────┬───────────────────────────┘
                   │
                   ▼
┌──────────────────────────────────────────────┐
│  ASR Providers (appcore/asr_providers/)      │
│  ├─ base.py        BaseASRAdapter            │
│  ├─ doubao.py      迁移自 pipeline/asr.py    │
│  ├─ scribe.py      迁移自 pipeline/asr_scribe│
│  ├─ cohere.py      新                        │
│  └─ __init__.py    REGISTRY                  │
└──────────────────────────────────────────────┘
```

## 3. 核心组件

### 3.1 BaseASRAdapter

`appcore/asr_providers/base.py`

```python
from dataclasses import dataclass
from pathlib import Path
from typing import List, TypedDict


class WordTimestamp(TypedDict):
    text: str
    start_time: float
    end_time: float
    confidence: float


class Utterance(TypedDict):
    text: str
    start_time: float
    end_time: float
    words: list[WordTimestamp]


@dataclass(frozen=True)
class ASRCapabilities:
    supports_force_language: bool       # decoder 可强制目标语言
    supported_languages: frozenset[str] # ISO-639-1（"*" 表示全部）
    accepts_local_file: bool            # True=直传本地，False=要先上传 URL


class BaseASRAdapter:
    provider_code: str           # 与 llm_provider_configs 表一致
    display_name: str
    capabilities: ASRCapabilities
    default_model_id: str

    def __init__(self, model_id: str | None = None) -> None:
        self.model_id = model_id or self.default_model_id

    def transcribe(
        self,
        local_audio_path: Path,
        language: str | None = None,    # 主语言 ISO-639-1（如 "es"）
    ) -> List[Utterance]:
        raise NotImplementedError
```

### 3.2 Adapter 实现

| Adapter | provider_code | 默认 model_id | 强制语言 | 支持语言 |
|---|---|---|---|---|
| DoubaoAdapter | `doubao_asr` | `bigmodel` | ❌ | zh, en（其他语言可调但效果差） |
| ScribeAdapter | `elevenlabs_tts` | `scribe_v2` | ✅（`language_code` 参数） | 99 语言（"*"）|
| CohereAdapter | `cohere_asr` | `cohere-transcribe-03-2026` | ✅ | en, de, fr, it, es, pt, el, nl, pl, ar, vi, zh, ja, ko |

实现要点：
- **DoubaoAdapter**：保留原有"上传 → 提交 → 轮询"流程，封装为类
- **ScribeAdapter**：保留原有 multipart 上传 + word-level → sentence 聚合，`language_code` 透传
- **CohereAdapter**：调 `https://api.cohere.com/v2/transcribe`（路径以官方文档为准，实施时确认）；输入本地 base64 音频或 URL；输出对齐 Utterance 格式

### 3.3 ASR Router

`appcore/asr_router.py`

```python
def transcribe(
    audio_path: Path,
    source_language: str,
    *,
    project_id: str | None = None,
    user_id: int | None = None,
) -> list[Utterance]:
    primary, fallback = resolve_route(source_language)
    primary_lang = source_language if primary.capabilities.supports_force_language else None
    utterances = primary.transcribe(audio_path, language=primary_lang)
    if fallback is None:
        return utterances
    return purify_language(
        utterances,
        audio_path=audio_path,
        source_language=source_language,
        fallback=fallback,
    )


def resolve_route(source_language: str) -> tuple[BaseASRAdapter, BaseASRAdapter | None]:
    cfg = load_route_config()  # DEFAULT_ROUTE_TABLE 合并 system_settings 覆盖
    entry = cfg.get(source_language) or cfg["__default__"]
    primary = build_adapter(entry["primary"], entry.get("primary_model"))
    fallback = build_adapter(entry["fallback"], entry.get("fallback_model")) if entry.get("fallback") else None
    return primary, fallback
```

### 3.4 默认路由表

`appcore/asr_router.py::DEFAULT_ROUTE_TABLE`

| source_language | primary | fallback |
|---|---|---|
| zh | doubao_asr | cohere_asr |
| en | doubao_asr | cohere_asr |
| de / es / pt / fr / it / nl / pl / el / ar / vi / ja / ko | cohere_asr | elevenlabs_tts (scribe_v2) |
| 其他（兜底） | elevenlabs_tts | cohere_asr（仅当语言在 Cohere 14 支持内）|
| auto / 空 | doubao_asr | cohere_asr |

落库覆盖：`system_settings` 表 key=`asr_route_config`，JSON 形如：
```json
{
  "es": {"primary": "cohere_asr", "fallback": "elevenlabs_tts"},
  "zh": {"primary": "doubao_asr", "fallback": "cohere_asr"}
}
```

### 3.5 语言污染清理

`appcore/asr_purify.py`

```python
def purify_language(
    utterances: list[Utterance],
    *,
    audio_path: Path,
    source_language: str,
    fallback: BaseASRAdapter,
) -> list[Utterance]:
    suspicious_idx: list[int] = []
    for i, utt in enumerate(utterances):
        if _too_short_to_judge(utt):  # < 8 字符 或 < 1.5 秒
            continue
        detected = detect_language(utt["text"])  # fast-langdetect
        if detected and detected != source_language:
            suspicious_idx.append(i)

    if not suspicious_idx:
        return utterances

    log.info("[ASR-Purify] 检测到 %d 个可疑段，触发 fallback 重转", len(suspicious_idx))
    fallback_lang = source_language if fallback.capabilities.supports_force_language else None

    for i in suspicious_idx:
        utt = utterances[i]
        try:
            with _slice_audio(audio_path, utt["start_time"], utt["end_time"]) as clip:
                retried = fallback.transcribe(clip, language=fallback_lang)
        except Exception:
            log.exception("[ASR-Purify] fallback 重转失败 idx=%d", i)
            utterances[i] = None  # 兜底删除
            continue

        if not retried:
            utterances[i] = None
            continue
        new_text = " ".join(r["text"] for r in retried).strip()
        if not new_text:
            utterances[i] = None
            continue
        # 二次检测
        re_detected = detect_language(new_text)
        if re_detected and re_detected != source_language:
            log.warning("[ASR-Purify] 二次检测仍非主语言 idx=%d 删除", i)
            utterances[i] = None
            continue
        utterances[i] = _rebase_timestamps(retried, utt["start_time"], utt["end_time"])

    return _merge_adjacent(utterances)  # 删除 None + 把孤段时间并入前一段
```

策略细节：
- **太短不判**：utterance 文本 < 8 字符 或 时长 < 1.5 秒 → 一律保留（避免误杀短词如 "OK", "Sí"）
- **检测置信度低**：`fast-langdetect.detect(...)` 返回的 score < 0.5 视为"无法判定"，保留
- **二次失败**：fallback 也输出非主语言 → 标记删除
- **删除 + 时间合并**：被删 utterance 的 (start_time, end_time) 时间区间并入**前一段**（如无前段则并入后段）；最终保证字幕时间线连续

### 3.6 ffmpeg 切片

`appcore/asr_purify.py::_slice_audio` — 上下文管理器

```python
@contextmanager
def _slice_audio(audio_path: Path, start: float, end: float) -> Iterator[Path]:
    pad = 0.15  # 前后各加 150ms 避免 word boundary 截断
    s = max(0.0, start - pad)
    duration = max(0.05, end - s + pad)
    fd, out_path = tempfile.mkstemp(suffix=audio_path.suffix or ".wav")
    os.close(fd)
    try:
        subprocess.run([
            "ffmpeg", "-y", "-loglevel", "error",
            "-ss", f"{s:.3f}", "-i", str(audio_path),
            "-t", f"{duration:.3f}", "-c", "copy",
            out_path,
        ], check=True, timeout=30)
        yield Path(out_path)
    finally:
        try:
            os.unlink(out_path)
        except OSError:
            pass
```

### 3.7 凭据存储

`appcore/llm_provider_configs.py::_KNOWN_PROVIDERS` 新增：
```python
"cohere_asr":            ("Cohere Transcribe",                  GROUP_ASR),
```
`elevenlabs_tts` 已存在（Scribe 复用其 key），无需新增。

`PROVIDER_CREDENTIAL_MAP` 新增：
```python
"cohere_asr":      ("cohere_asr",           None),
```

### 3.8 use_case 注册

`appcore/llm_use_cases.py` 修改：
- `video_translate.asr` 保留作"主入口" use_case，但其 `default_provider` / `default_model` 仅用于"兜底无路由覆盖时"。实际路由走 `asr_router`
- 不再为每个 ASR provider 单独注册 use_case（路由表本身就是 use_case 的细化）

### 3.9 fast-langdetect 集成

`appcore/asr_purify.py::detect_language(text: str) -> str | None`

依赖：`fast-langdetect>=0.2.0` (PyPI 名 `fast-langdetect`，wraps fasttext lid176)

```python
def detect_language(text: str) -> tuple[str, float] | None:
    if not text or len(text.strip()) < 4:
        return None
    try:
        from fast_langdetect import detect
        result = detect(text.replace("\n", " "), low_memory=True)
        return result["lang"], result["score"]
    except Exception:
        log.exception("[ASR-Purify] fast-langdetect 失败")
        return None
```

> 注：fast-langdetect 输出 ISO-639-1，"zh-Hans" 等需归一化为 "zh"（项目主语言代码体系）。归一化函数 `_normalize_lang_code()`。

## 4. 数据流

```
audio_file
   │
   ▼
asr_router.transcribe(audio, source_language=es)
   ├─ resolve_route("es") → (cohere, scribe_v2)
   ├─ cohere.transcribe(audio, language="es")  ← force decoder
   ├─ utterances（已基本无中文污染）
   ├─ purify_language:
   │  ├─ for each utt: fast-langdetect
   │  │  └─ 若 detected != "es" 且 not too_short: 标记可疑
   │  ├─ for each suspicious:
   │  │  ├─ ffmpeg 切片 → temp file
   │  │  ├─ scribe_v2.transcribe(clip, language="es")  ← fallback
   │  │  ├─ 二次 detect_language → 仍污染则删除
   │  │  └─ 替换 utterance 内容（时间戳保持 [start, end]）
   │  └─ _merge_adjacent: 删除 None + 把空段时间并入前一段
   ▼
purified utterances
   ▼
（pipeline 后续步骤不变：_step_asr_normalize / _step_alignment / ...）
```

## 5. /settings 「ASR 路由」配置 UI

新增 tab：`/settings?tab=asr_routing`

布局（遵循 Frontend Design System，Ocean Blue Admin）：
- 表格：每行一个语言，列为「源语言 / 主 ASR / 主模型 / Fallback ASR / Fallback 模型 / 操作」
- 主 ASR / Fallback 下拉：选项来自 `appcore/asr_providers.REGISTRY`
- 「恢复默认」按钮：清空当前语言的覆盖，回到 `DEFAULT_ROUTE_TABLE`
- 「新增语言」按钮：添加新行（用户输入 ISO-639-1 代码）
- 提交：POST `/settings/asr_routing`，更新 `system_settings.asr_route_config`

UI 样式严格遵循深海蓝侧栏 + 海洋蓝按钮 + 大圆角卡片 + 零紫色（hue 200-240）。

## 6. 错误处理

| 场景 | 行为 |
|---|---|
| 主 ASR 调用失败 | 重试 2 次（指数退避 2s/4s），仍失败抛出 → pipeline 标记 task 失败 |
| fallback 调用失败 | 退化为「删除 + 时间合并」 |
| ffmpeg 切片失败 | 跳过该段 fallback，保留原文本（不标记删除）|
| fast-langdetect 抛错 | 视为主语言（保守，不删除）|
| Cohere API 凭据缺失 | 启动时不报错；运行时若被路由命中 → ProviderConfigError，pipeline 失败并提示去 `/settings` 配置 |
| 路由表 JSON 损坏 | 退回 DEFAULT_ROUTE_TABLE，记录 warning |

## 7. 测试

### 7.1 单元测试

| 文件 | 覆盖 |
|---|---|
| `tests/test_asr_providers_doubao.py` | DoubaoAdapter mock 提交+轮询；输出 Utterance 格式 |
| `tests/test_asr_providers_scribe.py` | ScribeAdapter mock multipart；word→sentence 聚合 |
| `tests/test_asr_providers_cohere.py` | CohereAdapter mock HTTP；强制语言参数透传 |
| `tests/test_asr_router.py` | resolve_route 默认表 + system_settings 覆盖；各语言映射 |
| `tests/test_asr_purify.py` | 污染检测；短段不误杀；fallback 重转；二次失败删除；时间合并 |
| `tests/test_asr_purify_slice.py` | _slice_audio mock subprocess（不真跑 ffmpeg）|
| `tests/test_asr_router_integration.py` | runtime 集成层：mock adapters，验证 purify 流程被触发 |

### 7.2 现有测试回归

- `tests/test_asr_normalize.py` 等不受影响（不动 `_step_asr_normalize`）
- `tests/test_asr_scribe.py` 适配新 ScribeAdapter 接口

### 7.3 手工验证

- 准备 3 个西语视频（含豆包易误判段落），分别走旧/新 pipeline，对比 utterance 中文污染情况
- 凭据 + 路由覆盖：在 `/settings?tab=asr_routing` 切换 zh→cohere，跑一个中文视频验证生效

### 7.4 测试命令

```bash
# 本地
python -m pytest tests/test_asr_*.py -q

# 服务器
ssh -i C:/Users/admin/.ssh/CC.pem -o StrictHostKeyChecking=no root@172.30.254.14 \
  'cd /opt/autovideosrt-test && git pull && systemctl restart autovideosrt-test \
   && sleep 3 \
   && /opt/autovideosrt/venv/bin/python -m pytest tests/test_asr_*.py -q 2>&1 | tail -30'
```

## 8. 部署

1. **依赖**：`requirements.txt` 新增 `fast-langdetect>=0.2.0,<1.0`（cohere 走 raw HTTP via `requests`，无需 SDK）
2. **凭据**：管理员在 `/settings` 配置 `cohere_asr.api_key`（用户已答应回头给）
3. **路由覆盖**：首次启动 `system_settings.asr_route_config` 为空 → 走 DEFAULT_ROUTE_TABLE，无需 migration
4. **服务器命令**：
   ```bash
   ssh ... 'cd /opt/autovideosrt-test && git pull && \
            /opt/autovideosrt/venv/bin/pip install -r requirements.txt && \
            systemctl restart autovideosrt-test'
   ```

## 9. 实施顺序

1. 加 fast-langdetect 依赖 + 测试可 import
2. 新建 `appcore/asr_providers/` 包：base + doubao + scribe（迁移现有逻辑，不改行为）
3. 新建 cohere adapter（mock 测试通过，等 key 后做集成验证）
4. 新建 `appcore/asr_router.py` + DEFAULT_ROUTE_TABLE
5. 新建 `appcore/asr_purify.py` + ffmpeg 切片
6. 把 `runtime_omni._step_asr` / `runtime._step_asr` / `runtime_multi` ASR 入口替换为 `asr_router.transcribe`
7. `/settings` 加 ASR 路由 tab + 后端路由
8. 测试 + 部署

## 10. 风险与回滚

| 风险 | 缓解 |
|---|---|
| Cohere API 限速/down | fallback 到 scribe_v2；管理员可在 settings 临时把 cohere 换成 scribe |
| fast-langdetect 误判（特别是短句、混合句） | "太短不判"阈值 + "二次检测仍非主语言才删" 双层保护 |
| ffmpeg 切片性能 | 串行处理；30 分钟视频典型只有 5-15 段污染，加时 < 30 秒 |
| 现有 doubao/scribe 行为被破坏 | adapter 化是纯重构，不改外部行为；旧测试沿用 |

回滚：完整回滚 = 把 `runtime_omni._step_asr` 等入口改回直接 import `pipeline.asr` / `pipeline.asr_scribe`，删 `appcore/asr_*.py`。最坏情况下，路由表里把所有语言的 primary 都设为 doubao_asr 即可逻辑回退到旧行为。

## 11. 附录：决策记录

| # | 决策 | 选项 |
|---|---|---|
| 1 | 不接 gpt-audio-mini | 因其无时间戳，不适配视频翻译 pipeline |
| 2 | Cohere 走官方 API | OpenRouter 不托管 ASR；自建需 GPU 不划算 |
| 3 | provider 选择走 A+C | 代码自动路由 + 管理员可全局覆盖；用户前端不感知 |
| 4 | 污染清理走选项 4 | 源头强制语言 + fast-langdetect 后处理双保险 |
| 5 | adapter 抽统一包 | 新增 cohere 为第 3 个 provider，正是抽象甜蜜点 |
| 6 | scribe v1/v2 model_id 可配 | scribe 已用 v2，不必 v1/v2 共存；通过 `model_id` 字段保留切换能力 |
| 7 | 污染段触发 D 方案（重转）| 最准；用户接受性能开销 |
| 8 | fallback 优先级硬编码 + settings 覆盖 | 与现有 use_case binding 哲学一致 |
| 9 | fast-langdetect | 速度优先（< 1ms），准确度足够 |
| 10 | Cohere 用 raw HTTP | 与现有 doubao/scribe 风格一致，不增加 SDK 依赖 |
| 11 | 路由表存 system_settings JSON | 复用现有机制（参考 shopify_image_localizer_release）|
| 12 | 短段阈值 < 8 字符或 < 1.5 秒 | 经验值，避免误杀 "OK"/"Sí" |
| 13 | fallback 重转串行 | 保持简单，性能足够 |
