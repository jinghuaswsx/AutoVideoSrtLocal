# 多语种视频翻译模块设计

## 1. 目标与边界

新建一个**统一的视频翻译功能块**，处理 6 种目标语言：`de / fr / es / it / ja / pt`。源语言自动从 `zh / en` 二选一检测。语言通过胶囊按钮切换。

**边界：**
- 不动英文视频翻译模块（现有）
- 不动老 `de_translate` / `fr_translate` 模块代码和数据；侧边栏删入口，URL 仍可直达
- 不做声音克隆 / 品类自动识别 / 卖点清单 / 禁用词 / 市场合规规则 / 变体（只有 normal）
- 不做单任务 prompt 临时覆盖

**设计哲学：简单且可靠 > 聪明但偶尔翻车。**

## 2. 架构

```
Web 层：/multi-translate（列表）、/multi-translate/<task_id>（工作台）
Runner 层：单一 MultiTranslateRunner（继承 PipelineRunner）
Prompt 配置层：llm_prompt_configs 表 + resolver（管理员后台可视化编辑）
语言规则层：pipeline/languages/<lang>.py（字幕 / TTS 语言码 / 标点后处理）
领域插件：pipeline/domains/ecommerce.py（单一共享 prompt 片段，可在后台编辑）
Pipeline 步骤：asr → voice_match → align → translate → tts → subtitle → compose
```

**关键点：不为每种语言写 Runner 子类。**一个 `MultiTranslateRunner` + 每语言一份规则配置文件 + 一张 prompt 配置表，扩展第 7 种语言只加 1 个语言规则文件 + DB 里 4 行 prompt。

## 3. 数据模型

### 复用现有表
- `projects.type = 'multi_translate'`（所有 6 语言共用这一个值）
- `projects.state_json.target_lang` = `'de' | 'fr' | 'es' | 'it' | 'ja' | 'pt'`
- `projects.state_json.source_lang` = `'zh' | 'en'`（ASR 后自动检测写入）
- `projects.state_json.voice_match_candidates` = `[{voice_id, similarity, name, preview_url, gender}] × 3`
- `projects.state_json.selected_voice_id` = 用户最终选的
- `elevenlabs_voices`（含 `audio_embedding` BLOB）
- `media_video_translate_profiles`（字体 / 字幕大小 / 颜色等 12 项参数三层回填）

### 新增表：`llm_prompt_configs`

```sql
CREATE TABLE llm_prompt_configs (
  id INT AUTO_INCREMENT PRIMARY KEY,
  slot VARCHAR(64) NOT NULL,          -- 'base_translation' | 'base_tts_script' | 'base_rewrite' | 'ecommerce_plugin'
  lang VARCHAR(8),                    -- 'de'/'fr'/.../'ja'，'ecommerce_plugin' 用 NULL
  model_provider VARCHAR(32) NOT NULL,
  model_name VARCHAR(128) NOT NULL,
  content MEDIUMTEXT NOT NULL,
  enabled TINYINT DEFAULT 1,
  updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  updated_by INT,
  UNIQUE KEY uk_slot_lang (slot, lang)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
```

## 4. Prompt / 模型可视化配置

**硬规定：prompt 内容和模型选择绝不写死在代码里。**

### 运行时 resolver

```python
cfg = resolve_prompt_config(slot="base_translation", lang="de")
# 返回 {provider, model, content}
# DB 为空或 DEFAULT 记录被删，fallback 到代码里的 DEFAULTS 常量，并 seed 写回 DB
```

所有 LLM 调用点（翻译、TTS 脚本、rewrite）统一走 resolver。

### DEFAULTS 常量

`pipeline/languages/prompt_defaults.py` 维护一份出厂默认，仅用于：
- 空库冷启动 seed
- 管理后台"恢复此项默认"按钮的数据源

### 管理员后台页面 `/admin/prompts`

- 网格视图：`(slot × lang)` 单元格展示是否已配置，点击进入编辑器
- 编辑器：模型供应商下拉 + 模型名下拉 + prompt textarea + 占位符提示（`{source_full_text}` 等）
- 按钮：`[保存]` `[恢复默认]` `[预览最终 prompt]`
- 仅 `user.is_admin` 可访问

### 业务流程内透明化

工作台翻译步骤卡片右上角有 `ⓘ` 图标，悬停展示：
- 本次使用的模型（供应商 + 名称）
- 本次使用的 prompt 槽位 + 语言
- 最后修改时间 + 修改人
- `[查看完整 prompt]`（只读预览）

**不提供单任务覆盖入口。**

## 5. Pipeline 流程

```
上传 → 选目标语言
  ↓
ASR（豆包 v3）+ 源语言自动检测（zh/en）
  ↓
[并行] 音色向量匹配
  ├─ 从 ASR utterances 挑一段作为采样：优先取单个 utterance 时长最长的，时长不足 8s 时拼接相邻 utterance 直到 ≥8s
  ├─ resemblyzer 提取 256 维 embedding
  ├─ 查 elevenlabs_voices（language=target_lang, audio_embedding IS NOT NULL）
  ├─ 余弦相似度 Top-3，写入 state_json.voice_match_candidates
  └─ 若 Top-1 相似度 < 0.4 或结果为空：fallback 走 resolve_default_voice(lang)
  ↓
镜头检测 + 语义分段（复用 alignment.py）
  ↓
★ 人工确认分段
  ↓
翻译：resolve_prompt_config('base_translation', target_lang).content
     + resolve_prompt_config('ecommerce_plugin', None).content
     → 调 LLM → JSON 译文
  ↓
★ 人工确认译文 + 选音色
    · 默认选中 Top-1
    · 展示 Top-3 卡片（相似度% + 试听按钮）
    · 原声采样片段可试听
    · 底部"🔍 查看全部 {N} 个 {lang} 音色"打开完整库
    · Top-1 < 0.4 时顶部提示"原声与库内音色差异较大"
  ↓
TTS（ElevenLabs multilingual_v2，用选中 voice_id）
  ↓
字幕：按 pipeline/languages/<lang>.py 配置走断句 + CPS 校验 + 后处理
  ↓
合成 → 导出
```

## 6. 语言规则文件结构

`pipeline/languages/<lang>.py` 只装**不依赖 prompt 的规则**（prompt 都在 DB 里）：

```python
# TTS 配置
TTS_MODEL_ID = "eleven_multilingual_v2"
TTS_LANGUAGE_CODE = "de"

# 字幕规则
MAX_CHARS_PER_LINE = 38           # de=38, fr/es/pt/it=42, ja 按全角=21
MAX_CHARS_PER_SECOND = 17         # Latin 17, ja 13 (Netflix 规范)
MAX_LINES = 2
WEAK_STARTERS = {...}             # 弱边界词集合
WEAK_STARTER_PHRASES = [...]      # 弱边界短语（fr "à partir de" 不拆）

# 前后处理（可选）
def pre_process(text: str) -> str: ...    # es 自动加 ¿ ¡；fr 保护缩合词
def post_process_srt(srt: str) -> str: ...# fr/pt 标点前加 nbsp + guillemets
```

### 各语言字幕参数汇总

| lang | max_chars | CPS | 特殊前处理 | 特殊后处理 |
|---|---|---|---|---|
| de | 38 | 17 | 无 | 无 |
| fr | 42 | 17 | 保护缩合词 `qu'/c'/l'/d'` | `? ! : ;` 前加 nbsp；`«  »` guillemets |
| es | 42 | 17 | 疑问/感叹句首补 `¿ ¡` | 无 |
| it | 42 | 17 | 保护缩合词 `l'/d'/c'` | 无 |
| pt | 42 | 17 | 保护缩合词 `d'/n'` | 无 |
| ja | 21（全角） | 13 | fugashi 分词 + 助词边界切 | 无 |

## 7. 音色匹配改造

**复用现有 `pipeline/voice_match.py` + `appcore/voice_match_tasks.py`，两处小改：**

1. 新增 `extract_sample_clip_from_utterances(utterances, min_duration=8)`：从 ASR utterances 里挑最长连续说话段做采样（替代原"中间 10 秒"硬切）
2. 匹配结果持久化到 `state_json.voice_match_candidates`，任务中断恢复或字幕重跑不用重新匹配

**不做性别过滤**——embedding 相似度天然倾向同性别候选，显式过滤会让"声线相近但性别特殊"的案例（比如女低音）匹配不到。若用户不满意 Top-3 可打开全库兜底。候选结果里仍携带 `gender` 字段，用于 UI 卡片显示图标，不参与过滤逻辑。

## 8. 前端 UI

### 列表页 `/multi-translate`
- 顶部胶囊按钮：`[全部] [🇩🇪 德语] [🇫🇷 法语] [🇪🇸 西语] [🇮🇹 意语] [🇯🇵 日语] [🇵🇹 葡语]`
- 点击过滤当前列表（按 `state_json.target_lang`）
- 新建按钮默认带上当前选中语言
- 首次进入默认选 "全部"；后续记住上次选择（localStorage）

### 工作台 `/multi-translate/<task_id>`
- 复用现有 `_task_workbench.html` 骨架
- 顶部新增 `目标语言：🇩🇪 德语` 只读徽章
- 翻译步骤加 `ⓘ` 图标（第 4 节）
- 音色选择步骤：Top-3 卡片（§5）+ 全库兜底入口

### 侧边栏调整
- **新增**：`[🌐 多语种视频翻译]`
- **删除导航**：`[🇩🇪 视频翻译（德语）]`、`[🇫🇷 视频翻译（法语）]`（路由仍保留）
- **保留**：`[🎬 视频翻译]`（英文）、其他功能不动

## 9. 分批实施

| 批次 | 范围 | 验收 |
|---|---|---|
| **第 1 批** | 架构骨架 + `llm_prompt_configs` 表 + 管理后台 prompt 编辑页 + resolver + `MultiTranslateRunner` + 语言规则配置文件（de/fr）+ 音色匹配改造 + 前端列表页/工作台 | de/fr 两种语言跑通全流程；Top-3 向量匹配可用；管理员能通过后台编辑 prompt 立即生效（无需重部署） |
| **第 2 批** | 扩展到 es / it / pt | 每种语言只需 DB 里加 4 行 prompt + `pipeline/languages/<lang>.py` 一份规则；第 1 批骨架零改动 |
| **第 3 批** | ja | 引入 `fugashi`（MeCab Python 绑定），若 Windows 安装受阻改用 `tiny-segmenter`；日语分词 + 助词边界断句；CPS=13；max_chars 按全角 21 |

每批独立可发布。第 2 批上线不阻塞。第 3 批可延后。

## 10. 关键技术债修复（本期顺手做）

修复共享模块里的设计缺陷，**采用"加参数 + 默认值"策略保持老 DE/FR 行为不变**：

- `wrap_text()` / `format_subtitle_chunk_text()` 新增可选参数 `weak_boundary_words=None` 和 `max_chars=42`。老调用方不传 = 老行为；新模块调用方从语言配置读取传入
- `pipeline/subtitle.py::apply_french_punctuation` 泛化为 `apply_punctuation_spacing(text, rules)`，法语调用方传入法语规则字典；保留 `apply_french_punctuation` 作为薄包装确保老 FR 模块不断
- 新模块不走 `pipeline/voice_library.py` 旧的 `user_voices` 表，改走 `appcore/voice_library_browse.py` + `elevenlabs_voices` 表

## 11. 不做的事

- 不做多 prompt profile（A/B 方案）
- 不做声音克隆
- 不做品类自动识别、卖点清单、禁用词、变体
- 不做市场合规规则
- 不做单任务 prompt 临时覆盖
- 不做数据迁移（老 DE/FR 和英文模块的历史数据原样保留）
- 不做 ja 以外的 CJK 语言（ko / zh-TW 等）

## 12. 风险

- `elevenlabs_voices.audio_embedding` 对 es / it / pt / ja 的覆盖率需先确认；覆盖率不足时向量匹配自动 fallback 到 `resolve_default_voice(lang)`
- ja 的 `fugashi` 依赖在 Windows 上可能需要额外 MeCab dict；第 3 批单独评估，备用方案 `tiny-segmenter`
- 管理员修改了 prompt 后正在进行中的任务不受影响（现有任务的 prompt 已经 render 到 LLM 消息里），下一个新任务才用新 prompt
