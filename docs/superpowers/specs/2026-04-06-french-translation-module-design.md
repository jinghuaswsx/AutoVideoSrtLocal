# 视频翻译（法语）模块设计

## 1. 目标

新增独立的"视频翻译（法语）"板块，支持将中文或英文短视频翻译本地化为法语视频。与现有英文翻译、德语翻译流水线平行，复用 pipeline 层，新建 web 层。

**第一版目标**：完成基础功能，跑通"上传视频 → ASR → 分段 → 法语翻译 → TTS → 字幕 → 合成/导出"全流程。

## 2. 关键决策

| # | 决策 | 选择 | 原因 |
|---|------|------|------|
| 1 | 架构定位 | 独立板块（平行于英文/德语翻译） | 与德语模块保持一致架构，不影响现有流水线 |
| 2 | 源语言 | 中文 + 英文 | 用户明确需求 |
| 3 | ASR 方案 | 豆包 v3（不区分语言） | 零额外依赖，与德语模块一致 |
| 4 | TTS 模型 | eleven_multilingual_v2 | 法语质量最佳，母语级发音准确率 95-98% |
| 5 | 默认音色 | Antoine(男) / Jeanne(女) | 调研筛选的高质量巴黎法语音色 |
| 6 | 字幕规则 | 42字符/行，法语弱边界词，法语标点空格规则 | Netflix 法语字幕规范 |
| 7 | 敬语策略 | 默认 vous（可配置 tu） | 面向大众内容用 vous 更安全 |

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
[自动] LLM 翻译本土化 → 法语 (法语专属 Prompt)
    ↓
★ [人工确认] 法语译文
    ↓
[自动] ElevenLabs TTS (model=eleven_multilingual_v2, language_code="fr")
    ↓
[自动] 字幕格式化 (42字符/行，法语标点规则)
    ↓
[自动] 视频合成 (软硬字幕) + 剪映导出
    ↓
完成，下载成品
```

## 4. 新增/修改文件清单

### 新增文件

| 文件 | 说明 |
|------|------|
| `pipeline/localization_fr.py` | 法语翻译 Prompt 集（系统提示、TTS 脚本提示、变体定义） |
| `appcore/runtime_fr.py` | 法语流水线编排，继承 PipelineRunner 核心逻辑 |
| `web/routes/fr_translate.py` | 法语翻译路由蓝图 |
| `web/templates/fr_translate_list.html` | 项目列表页 |
| `web/templates/fr_translate_detail.html` | 工作台页面 |

### 修改文件

| 文件 | 改动内容 |
|------|---------|
| `pipeline/translate.py` | 与德语模块共享：`generate_localized_translation()` 和 `generate_tts_script()` 接受 `localization_module` 参数，法语传入 `localization_fr` |
| `pipeline/tts.py` | 与德语模块共享：`generate_segment_audio()` 和 `generate_full_audio()` 接受 `language_code` 和 `model_id` 参数 |
| `pipeline/subtitle.py` | `wrap_text()` 接受 `max_chars`、`weak_starters` 和 `punctuation_rules` 参数（法语标点空格处理） |
| `web/app.py` | 注册 fr_translate 蓝图 |
| `web/templates/layout.html` | 侧边栏新增"视频翻译（法语）"导航项 |

> **注意**：如果德语模块已先完成 `translate.py`、`tts.py`、`subtitle.py` 的参数化改造，法语模块只需传入法语配置即可，无需重复修改。

## 5. 法语翻译 Prompt 设计

### 5.1 核心指令

角色：法国本土内容创作者（不是翻译器）
调性：clair et authentique（清晰真实）
词汇水平：B1-B2 法语
卖点策略：品质 > 价格，理性 > 感性，克制优雅
敬语：默认 vous（正式），可切换为 tu（非正式/年轻受众）
禁止：夸大承诺、aggressive CTA、美式煽情风格

### 5.2 系统提示结构

```
- 角色设定：Tu es un créateur de contenu français...
- 输出格式：JSON 数组，与英文版/德语版 schema 完全一致
- 风格要求：
  · 克制理性，降低推销语气（"amazing" → 适度表达）
  · 保持法语性数配合正确（阴阳性、单复数一致）
  · 默认使用 vous 敬语，除非指定 tu
  · 科技类术语可保留英文原文（marketing, startup 等）
- 长度约束：
  · 控制文本膨胀（英译法通常膨胀 15-30%），每句目标 6-12 词
  · 优先压缩句子长度以匹配原始音频时长
- 标点约束：
  · 问号/感叹号前加不间断空格（\u00A0）：「Bonjour !」「Pourquoi ?」
  · 冒号前后加空格：「Note : important」
  · 使用 guillemets 引号：« ... »（内侧加不间断空格）
  · 大写字母必须带重音：É, À, Ç
```

### 5.3 变体

第一版只做 normal 变体，不做 hook-CTA（法国市场与德国类似，对 aggressive CTA 反感）。

### 5.4 与德语 Prompt 的关键差异

| 维度 | 德语 | 法语 |
|------|------|------|
| 调性关键词 | sachlich und authentisch | clair et authentique |
| 词汇水平 | B1 | B1-B2 |
| 敬语 | Sie/du（德语无此配置） | vous/tu（可配置参数） |
| 名词大写 | 所有名词首字母大写 | 仅句首和专有名词大写 |
| 标点特殊处理 | 无 | 问号/感叹号/冒号前加空格，guillemets 引号 |
| 英语外来词 | 较少使用 | 科技/商业术语可保留英文 |

## 6. TTS 配置

| 参数 | 值 |
|------|-----|
| model_id | `eleven_multilingual_v2` |
| language_code | `"fr"` |
| output_format | `mp3_44100_128`（不变） |

### 默认音色

| 角色 | 音色名 | Voice ID | 说明 |
|------|--------|----------|------|
| 默认男声 | Antoine | 待确认 | 年轻巴黎男声，适合有声书和旁白 |
| 默认女声 | Jeanne | 待确认 | 年轻专业巴黎女声，适合叙述 |

> **Voice ID 确认方式**：上线前通过 ElevenLabs API `/v1/voices` 接口搜索确认。也可考虑 Benjamin（温暖沉稳男声）、Victoria（富有感染力女声）作为备选。

### 语速注意事项

法语比英语长 15-30%，TTS 输出时长可能超过原始音频。处理策略：
1. 翻译阶段压缩句子长度（Prompt 已约束）
2. TTS 输出后检测时长偏差，必要时 ffmpeg 加速 5-10%
3. 不超过 10% 加速，否则影响自然度

## 7. 字幕法语适配

### 7.1 基本参数

| 规则 | 英文值 | 德语值 | 法语值 |
|------|--------|--------|--------|
| max_chars_per_line | 42 | 38 | 42 |
| max_lines | 2 | 2 | 2 |
| max_reading_speed | 无限制 | 无限制 | 17 字符/秒（Netflix 规范） |
| weak_starters | and, or, to, of... | und, oder, der... | et, ou, de, du, des, le, la, les, un, une, pour, avec, dans, mais, aussi, que, qui |

### 7.2 法语标点空格规则（关键差异）

这是法语字幕与英语/德语的**最大区别**，需要在 `subtitle.py` 中新增处理：

| 标点 | 规则 | 示例 |
|------|------|------|
| `?` | 前加不间断空格 (U+00A0) | `Pourquoi\u00A0?` |
| `!` | 前加不间断空格 | `Bonjour\u00A0!` |
| `:` | 前后各加不间断空格 | `Note\u00A0:\u00A0important` |
| `;` | 前加不间断空格 | `oui\u00A0; non` |
| `«` `»` | 内侧加不间断空格 | `«\u00A0Bonjour\u00A0»` |
| `…` | 使用单字符 U+2026 | `Alors…` |

实现方式：在 `wrap_text()` 或 `build_srt_from_chunks()` 之后，增加一个 `apply_french_punctuation(text)` 后处理函数。

### 7.3 大写重音

法语大写字母必须保留重音符号：É, È, Ê, Ë, À, Â, Ç, Ô, Ù, Û, Ü, Î, Ï。LLM 输出通常已正确处理，但字幕渲染时需确保字体支持。

## 8. Web 路由

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/fr-translate` | 项目列表 |
| GET | `/fr-translate/<task_id>` | 工作台 |
| POST | `/api/fr-translate/start` | 上传视频 + 源语言，启动流水线 |
| POST | `/api/fr-translate/<task_id>/confirm-alignment` | 确认分段 |
| POST | `/api/fr-translate/<task_id>/confirm-segments` | 确认 ASR 分段 |
| POST | `/api/fr-translate/<task_id>/confirm-translate` | 确认译文 |
| POST | `/api/fr-translate/<task_id>/export` | 导出合成 |
| POST | `/api/fr-translate/<task_id>/resume/<step>` | 断点续跑 |
| GET | `/api/fr-translate/<task_id>/download/<artifact>` | 下载成品 |

## 9. 数据模型

### projects 表

- `type` 字段新增值：`"fr_translate"`
- `state_json` 中新增字段：`source_language`（"zh" 或 "en"），`target_language`（固定 "fr"）
- 新增字段 `formality`：`"vous"`（默认）或 `"tu"`，控制翻译敬语

### user_voices 表

无需改动。第一版在代码中硬编码 Antoine/Jeanne 作为法语默认音色。

## 10. 法语特有的质量优化

### 10.1 文本膨胀控制

英译法膨胀 15-30%，中译法更大。应对策略：
- Prompt 层面约束每句词数
- TTS 后做时长对齐检查
- 超出阈值时 ffmpeg 微调语速（≤10%）

### 10.2 敬语一致性校验

翻译后可做简单的正则校验：
- vous 模式下不应出现 tu/te/ton/ta/tes
- tu 模式下不应出现 vous/votre/vos
- 发现不一致时标记警告（不阻断流程）

### 10.3 性数配合

法语性数配合复杂（形容词、过去分词、冠词都要一致），纯靠 LLM 翻译可能偶有错误。第一版不做自动校验，依赖人工确认步骤。未来可引入语法检查工具（如 LanguageTool API）。

### 10.4 法语 SEO 本土化（可选增强）

- 标题/描述用法语本地关键词重写，不直译
- YouTube 支持多语言标题，可同时保留原标题 + 法语标题
- 上传法语 SRT 字幕有助于 YouTube 索引
- 混合使用法语 + 英语标签

## 11. 不做的事情（第一版）

- 不做 hook-CTA 变体（法国市场不适用）
- 不做音色智能匹配（用默认音色）
- 不做法语 ASR 降级到 Whisper（先试豆包）
- 不做法语 copywriting 模块（只做视频翻译）
- 不做多语言通用框架（只做法语）
- 不改数据库 schema（用现有字段 + state_json 扩展）
- 不做自动语法校验（依赖人工确认）
- 不做口型同步（lip sync）
- 不做法语背景音乐自动替换

## 12. 与德语模块的协同

法语模块与德语模块架构完全对称，共享以下改造：

| 共享改造 | 说明 |
|---------|------|
| `translate.py` 参数化 | `localization_module` 参数，德语传 `localization_de`，法语传 `localization_fr` |
| `tts.py` 参数化 | `language_code` + `model_id` 参数 |
| `subtitle.py` 参数化 | `max_chars` + `weak_starters` 参数 |
| PipelineRunner 基类 | 德语/法语 runtime 继承同一基类 |

**建议实施顺序**：先完成德语模块（含 pipeline 层参数化改造），法语模块直接复用改造结果，只需新增法语专属配置和 Prompt。

## 13. 法语模块独有的额外工作

相比德语模块，法语模块额外需要：

| 工作项 | 原因 |
|--------|------|
| 法语标点空格处理函数 | 问号/感叹号/冒号前加空格，guillemets 引号格式 |
| 敬语参数（formality） | tu/vous 切换，德语暂无此需求 |
| 敬语一致性校验 | 确保全文敬语统一 |
| 阅读速度限制 | 17 字符/秒，Netflix 法语规范 |
