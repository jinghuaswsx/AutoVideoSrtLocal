# 视频翻译（德语）模块设计

## 1. 目标

新增独立的"视频翻译（德语）"板块，支持将中文或英文短视频翻译本地化为德语视频。与现有英文翻译流水线平行，复用 pipeline 层，新建 web 层。

**第一版目标**：今晚完成基础功能，能跑通"上传视频 → ASR → 分段 → 德语翻译 → TTS → 字幕 → 合成/导出"全流程。

## 2. 关键决策

| # | 决策 | 选择 | 原因 |
|---|------|------|------|
| 1 | 架构定位 | 独立板块（平行于英文翻译） | 不影响现有流水线稳定性，最快上线 |
| 2 | 源语言 | 中文 + 英文 | 用户明确需求 |
| 3 | ASR 方案 | 豆包 v3（不区分语言） | 零额外依赖，先试后优化 |
| 4 | TTS 模型 | eleven_multilingual_v2 | 德语质量最佳 |
| 5 | 默认音色 | Toby(男) / Annika(女) | 调研筛选的高质量德语音色 |
| 6 | 字幕规则 | 38字符/行，德语弱边界词 | 德语词比英语长 30-40% |

## 3. 流水线流程

```
用户选择源语言(中文/英文) + 上传视频
    ↓
[自动] 音频提取 (ffmpeg，复用 extract.py)
    ↓
[自动] ASR 语音识别 (豆包 v3，同一接口)
    ↓
[自动] 镜头检测 + 语义分段 (复用 alignment.py)
    ↓
★ [人工确认] 分段结果
    ↓
[自动] LLM 翻译本土化 → 德语 (德语专属 Prompt)
    ↓
★ [人工确认] 德语译文
    ↓
[自动] ElevenLabs TTS (model=eleven_multilingual_v2, language_code="de")
    ↓
[自动] 字幕格式化 (38字符/行，德语断行规则)
    ↓
[自动] 视频合成 (软硬字幕) + 剪映导出
    ↓
完成，下载成品
```

## 4. 新增/修改文件清单

### 新增文件

| 文件 | 说明 |
|------|------|
| `pipeline/localization_de.py` | 德语翻译 Prompt 集（系统提示、TTS 脚本提示、变体定义） |
| `appcore/runtime_de.py` | 德语流水线编排，继承 PipelineRunner 核心逻辑 |
| `web/routes/de_translate.py` | 德语翻译路由蓝图 |
| `web/templates/de_translate_list.html` | 项目列表页 |
| `web/templates/de_translate_detail.html` | 工作台页面 |

### 修改文件

| 文件 | 改动内容 |
|------|---------|
| `pipeline/translate.py` | `generate_localized_translation()` 和 `generate_tts_script()` 接受可选的 `localization_module` 参数，默认为英文模块，德语传入 `localization_de` |
| `pipeline/tts.py` | `generate_segment_audio()` 和 `generate_full_audio()` 接受可选的 `language_code` 和 `model_id` 参数 |
| `pipeline/subtitle.py` | `wrap_text()` 和 `_choose_balanced_split()` 接受可选的 `max_chars` 和 `weak_starters` 参数 |
| `web/app.py` | 注册 de_translate 蓝图 |
| `web/templates/layout.html` | 侧边栏新增"视频翻译（德语）"导航项 |

## 5. 德语翻译 Prompt 设计

### 5.1 核心指令

角色：德国本土内容创作者（不是翻译器）
调性：sachlich und authentisch（客观真实）
词汇水平：B1 德语
卖点策略：质量 > 价格，数据 > 情感
禁止：夸大承诺、虚假紧迫感、前后对比暗示

### 5.2 系统提示结构

```
- 角色设定：Du bist ein deutscher Content Creator...
- 输出格式：JSON 数组，与英文版 schema 一致
- 风格要求：克制真诚，强调产品质量和实用价值
- 长度约束：译文长度与原文成比例
- 德语特有：保持名词大写，使用口语化但不过于随意的表达
```

### 5.3 变体

第一版只做 normal 变体，不做 hook-CTA（德语市场对 aggressive CTA 反感）。

## 6. TTS 配置

| 参数 | 值 |
|------|-----|
| model_id | `eleven_multilingual_v2` |
| language_code | `"de"` |
| output_format | `mp3_44100_128`（不变） |

### 默认音色

| 角色 | 音色名 | Voice ID | 说明 |
|------|--------|----------|------|
| 默认男声 | Toby | `eEmoQJhC4SAEQpCINUov` | 友好、自信、有吸引力的德语男声 |
| 默认女声 | Annika | `ViKqgJNeCiWZlYgHiAOO` | 平静、自信、愉悦的德语女声 |

## 7. 字幕德语适配

| 规则 | 英文值 | 德语值 |
|------|--------|--------|
| max_chars_per_line | 42 | 38 |
| max_lines | 2 | 2（不变） |
| weak_starters | and, or, to, of, for, with, the, a, an | und, oder, der, die, das, ein, eine, für, mit, von, zu, aber, auch, wenn, dass |

首字母大写规则不变 — LLM 输出的德语自然遵守名词大写。

## 8. Web 路由

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/de-translate` | 项目列表 |
| GET | `/de-translate/<task_id>` | 工作台 |
| POST | `/api/de-translate/start` | 上传视频 + 源语言，启动流水线 |
| POST | `/api/de-translate/<task_id>/confirm-alignment` | 确认分段 |
| POST | `/api/de-translate/<task_id>/confirm-segments` | 确认 ASR 分段 |
| POST | `/api/de-translate/<task_id>/confirm-translate` | 确认译文 |
| POST | `/api/de-translate/<task_id>/export` | 导出合成 |
| POST | `/api/de-translate/<task_id>/resume/<step>` | 断点续跑 |
| GET | `/api/de-translate/<task_id>/download/<artifact>` | 下载成品 |

## 9. 数据模型

### projects 表

- `type` 字段新增值：`"de_translate"`
- `state_json` 中新增字段：`source_language`（"zh" 或 "en"），`target_language`（固定 "de"）

### user_voices 表

无需改动。用户通过现有音色管理功能导入德语音色即可。第一版在代码中硬编码 Toby/Annika 作为德语默认音色。

## 10. 不做的事情（第一版）

- 不做 hook-CTA 变体（德语市场不适用）
- 不做音色智能匹配（用默认音色）
- 不做德语 ASR 降级到 Whisper（先试豆包）
- 不做德语 copywriting 模块（只做视频翻译）
- 不做多语言通用框架（只做德语）
- 不改数据库 schema（用现有字段 + state_json 扩展）
