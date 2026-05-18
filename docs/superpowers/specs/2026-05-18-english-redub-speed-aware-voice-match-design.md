# 英语视频重新配音与语速感知音色匹配设计

- 日期：2026-05-18
- 模块：英语视频重新配音（新功能）
- 目标 project_type：`english_redub`
- 目标路由：`/english-redub`、`/api/english-redub/...`

## 文档锚点

- `AGENTS.md`：文档驱动、隔离 worktree、路由守卫、CSRF、验证顺序。
- `docs/superpowers/specs/2026-05-07-omni-translate-merge-design.md`：Omni `plugin_config`、动态 step、能力点调度。
- `docs/superpowers/specs/2026-05-13-omni-asr-primary-compact-timeline-design.md`：短视频口播以 ASR 为主时间线，镜头只作为辅助上下文。
- `docs/superpowers/specs/2026-05-13-tts-deferred-adaptive-speedup-design.md`：TTS 先文案收敛，再只在温和范围内做 native speed 微调。
- `docs/superpowers/specs/2026-05-13-tts-segment-candidate-assembly-design.md`：TTS 分段候选和最终音频选择诊断。
- `web/templates/CLAUDE.md`、`web/static/CLAUDE.md`：详情壳继承规则、CSRF、前端视觉约束。

## 背景

现有 Omni 已能组合 ASR、音色匹配、分镜、句级 TTS 收敛、字幕和合成，用于多语言翻译实验。用户现在需要一个独立菜单功能，专门处理“输入英文视频，输出仍为英文”的重新配音场景。目标不是翻译语言，而是让英文配音、字幕和原视频口播节奏大致一致。

参考任务 `3e045fbc-b895-4d5a-841a-d1148e5a7598` 暴露出两个问题：

1. 现有音色推荐只看 timbre embedding，相似度高的声音不一定语速合适。
2. 极短 ASR 片段和高密度短视频口播，即使选到快节奏音色，也仍依赖句级收敛、字幕跟随音频时间线和必要的 fallback。

因此本设计新增独立功能入口，并在该入口内启用可配置的语速感知音色推荐；现有 Omni / Multi 线上路径不改变。

## 目标

1. 新增“英语视频重新配音”菜单，放在视频翻译相关入口内。
2. 新模块固定 `source_language=en`、`target_lang=en`，输入和输出语言都是英文。
3. 新模块复用 Omni 的成熟流水线能力，但拥有独立路由、project_type、权限、列表页和详情页配置。
4. 新模块新增语速感知音色匹配策略：先按音色取候选池，再按 preview 语速与原视频 ASR 语速重排。
5. 管理员后台提供开关：旧推荐逻辑和新推荐逻辑二选一；默认旧逻辑。
6. 旧的 `/omni-translate`、`/multi-translate`、`/ja-translate` 行为完全不动。

## 非目标

- 不把现有 Omni 默认推荐逻辑改成语速感知。
- 不做跨语言翻译。
- 不承诺解决所有极短片段的自然配音问题。极短段仍由句级 TTS 收敛、裁切、合并或 fallback 机制处理。
- 第一版不做复杂后台参数面板；只暴露总开关。权重、候选池大小等先用常量或隐藏 system setting。
- 第一版不重新设计声音库同步任务 UI，只在需要时补充 preview 语速缓存。

## 用户可见行为

### 菜单

侧边栏视频翻译相关入口新增：

- 文案：`英语视频重新配音`
- 建议位置：`多语种视频翻译` 和 `全能视频翻译` 附近；如果后续侧边栏整理为“视频翻译”集合菜单，本入口归入该集合。
- 权限：新增 `english_redub` 权限，默认 admin / translator 可见，保持与翻译类功能一致。

### 列表页

`/english-redub` 使用与 Omni 列表页相近的密度和交互：

- 新建项目上传英文视频。
- 不显示目标语言选择，固定英文。
- 可显示说明：本功能只做英文重配音，不做语言翻译。
- 项目卡片显示时长、状态、创建人、创建时间。

### 详情页

详情页复用 `_translate_detail_shell.html` 工作台：

- `api_base = /api/english-redub`
- `pipeline_kind = english_redub`
- 返回列表链接指向 `/english-redub`
- 声音选择卡显示候选的音色相似度和语速匹配信息。

## 流水线设计

第一版固定使用最适合短视频英文重新配音的 Omni 能力组合：

```text
extract
asr
separate
asr_clean 或 asr_normalize
voice_match
alignment
shot_decompose
translate
tts
av_sync_audit
loudness_match
subtitle
compose
export
```

推荐默认能力快照：

```json
{
  "asr_post": "asr_clean",
  "shot_decompose": true,
  "translate_algo": "shot_char_limit",
  "source_anchored": true,
  "tts_strategy": "sentence_reconcile",
  "subtitle": "sentence_units",
  "voice_separation": true,
  "loudness_match": true,
  "av_sync_audit": "report_only"
}
```

行为要求：

- 创建任务时强制写入 `source_language="en"`、`target_lang="en"`。
- 前端不允许改目标语言。
- ASR 文本是事实来源；分镜只用于英文文案适配和画面上下文。
- `translate` 阶段在本模块内语义为“英文文案适配”，不是语言翻译：允许压缩、扩写、拆句、改写口播节奏，但禁止新增原视频没有的事实。

## 隔离策略

采用“外层隔离，底层复用”的方案。

独立部分：

- `web/routes/english_redub.py`
- `web/templates/english_redub_list.html`
- `web/templates/english_redub_detail.html`
- `appcore/runtime_english_redub.py` 或 `EnglishRedubRunner`
- `web/services/english_redub_pipeline_runner.py`
- project_type：`english_redub`
- 权限 code：`english_redub`
- API 前缀：`/api/english-redub`

复用部分：

- Omni 动态 step 解析和能力点实现。
- `_translate_detail_shell.html` 和任务工作台组件。
- `appcore.translate_profiles.omni_profile` 中可复用的 dispatch 思路。
- `sentence_reconcile` TTS 策略、字幕生成、loudness、compose/export。
- 现有声音库查询和 embedding 匹配基础能力。

硬隔离要求：

- 不修改 `omni_translate` 的默认 preset、路由创建逻辑和旧 `match_candidates` 行为。
- 新语速排序只在 `EnglishRedubRunner._step_voice_match` 或其调用的专用函数里生效。
- 老任务读取旧 state 时不受 `english_redub` 新字段影响。

## 语速感知音色推荐

### 策略开关

新增 system setting：

```text
english_redub_voice_match_strategy = legacy | timbre_speed
```

默认：`legacy`。

- `legacy`：完全复用原音色 embedding top10。
- `timbre_speed`：音色 top100 后按语速重排，输出 top10。

隐藏默认参数：

```text
candidate_pool_size = 100
result_top_k = 10
timbre_weight = 0.75
speed_weight = 0.25
min_similarity_floor = top1_similarity - 0.08
```

`min_similarity_floor` 用于防止语速合适但音色明显差的声音进入前排。实际实现可根据测试数据微调。

### 原视频语速

输入：ASR `utterances` 和可用 `words` 时间戳。

算法：

1. 只统计英文 utterance。
2. 优先使用 word-level timestamps，按 `word_count / speech_duration` 计算。
3. 没有 words 时，用 utterance 文本分词数除以 `end_time - start_time`。
4. 忽略异常段：
   - duration <= 0
   - duration < 0.35s 且 word_count <= 2
   - word_count <= 0
5. 使用 trimmed mean 或 median 降低极短段干扰。

输出：

```json
{
  "source_words_per_second": 3.8,
  "source_chars_per_second": 18.2,
  "sample_utterance_count": 6,
  "ignored_utterance_count": 1
}
```

### 声音 preview 语速

新增缓存表，避免和现有 `voice_speech_rate` 混用：

```sql
CREATE TABLE IF NOT EXISTS voice_preview_speech_rate (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  voice_id VARCHAR(64) NOT NULL,
  language VARCHAR(32) NOT NULL,
  preview_url_hash VARCHAR(64) NOT NULL,
  words_per_second DECIMAL(8,4) DEFAULT NULL,
  chars_per_second DECIMAL(8,4) DEFAULT NULL,
  duration_seconds DECIMAL(10,3) DEFAULT NULL,
  sample_text TEXT DEFAULT NULL,
  confidence DECIMAL(5,4) DEFAULT NULL,
  source VARCHAR(32) NOT NULL DEFAULT 'preview_asr',
  updated_at DATETIME NOT NULL,
  UNIQUE KEY uq_voice_preview_rate (voice_id, language, preview_url_hash),
  KEY idx_language_rate (language, words_per_second)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
```

缓存计算：

- 输入：`elevenlabs_voices` 或 `elevenlabs_voice_variants` 的 `preview_url`。
- 下载 preview 到缓存目录。
- 用现有 ASR adapter 或轻量英文 ASR 识别 preview。
- 计算 `words_per_second` / `chars_per_second`。
- 写入 `voice_preview_speech_rate`。

失败策略：

- 单条失败不中断推荐。
- 候选缺 preview rate 时保留原音色排序，`speed_score=null`。
- 如果 top100 中有效 preview rate 少于阈值（例如 10 条），整体退回 legacy 排序，并记录 `voice_match_strategy_effective="legacy_fallback"`.

### 打分

先计算音色相似度：

```text
timbre_score = cosine_similarity
```

再计算语速匹配：

```text
ratio = candidate_wps / source_wps
speed_score = 1 - min(abs(log(ratio)) / log(1.6), 1)
```

解释：

- `ratio=1.0` 得分 1。
- 语速差异越大，得分越低。
- log 比例比直接相减更稳，不会偏袒高语速或低语速。

最终：

```text
final_score = timbre_score * 0.75 + speed_score * 0.25
```

排序输出前 10，并在每个 candidate 上附加：

```json
{
  "similarity": 0.8479,
  "speed_score": 0.92,
  "final_score": 0.8659,
  "source_words_per_second": 3.8,
  "preview_words_per_second": 3.6,
  "voice_match_strategy": "timbre_speed"
}
```

## 后台配置

新增配置位置有两种可接受实现：

1. 在 `/settings?tab=omni_preset` 的现有 Omni 实验预设区域追加“英语重配音策略”卡片。
2. 新增 `/settings?tab=video_translate_strategy`，集中管理视频翻译策略开关。

第一版推荐方案 1，改动小，符合该功能复用 Omni 实验能力的定位。

配置项：

- 标题：`英语视频重新配音`
- 字段：`音色推荐策略`
- 选项：
  - `旧逻辑：只按音色匹配`
  - `新逻辑：音色 top100 + 语速重排`
- 保存：写入 `system_settings.english_redub_voice_match_strategy`

## 路由与权限

新增权限：

```python
("english_redub", GROUP_BUSINESS, "英语视频重新配音", True, True)
```

新增路由必须满足：

- 页面路由：`@login_required + @permission_required("english_redub")`
- 写操作 API：`@login_required`，并验证 owner/admin 可操作。
- mutating fetch 带 `X-CSRFToken`；如果 blueprint 被全局 CSRF exempt，仍复用现有翻译模块请求风格。

主要路由：

```text
GET    /english-redub
GET    /english-redub/<task_id>
POST   /api/english-redub/start
GET    /api/english-redub/<task_id>
POST   /api/english-redub/<task_id>/start
POST   /api/english-redub/<task_id>/restart
POST   /api/english-redub/<task_id>/resume
PUT    /api/english-redub/<task_id>/alignment
PUT    /api/english-redub/<task_id>/segments
PUT    /api/english-redub/<task_id>/voice
GET    /api/english-redub/<task_id>/voice-library
POST   /api/english-redub/<task_id>/rematch
POST   /api/english-redub/<task_id>/confirm-voice
GET    /api/english-redub/<task_id>/download/<file_type>
GET    /api/english-redub/<task_id>/artifact/<name>
DELETE /api/english-redub/<task_id>
```

可先实现 Omni 等价必要子集；未用到的扩展路由可后置，但详情工作台引用到的 API 必须齐全。

## 文件改动范围

### 新增

- `docs/superpowers/specs/2026-05-18-english-redub-speed-aware-voice-match-design.md`
- `db/migrations/2026_05_18_voice_preview_speech_rate.sql`
- `appcore/voice_preview_speech_rate.py`
- `pipeline/voice_match_speed.py`
- `appcore/runtime_english_redub.py`
- `web/routes/english_redub.py`
- `web/templates/english_redub_list.html`
- `web/templates/english_redub_detail.html`
- `web/services/english_redub_pipeline_runner.py`
- `tests/test_english_redub_routes.py`
- `tests/test_english_redub_voice_match.py`

### 修改

- `web/app.py`：注册新 blueprint，必要时加入 CSRF exempt 集合。
- `web/templates/layout.html`：新增菜单入口。
- `appcore/permissions.py`：新增权限和默认角色映射。
- `web/templates/_translate_detail_shell.html`：如需识别 `/english-redub` 的返回链接和 API base。
- `web/templates/_task_workbench.html` / `_task_workbench_scripts.html`：如有 hard-coded `/omni-translate` / `/multi-translate` 判断，补 `english_redub`。
- `web/routes/settings.py` 或相关设置服务：新增策略开关保存/读取。
- `web/templates/settings.html`：新增后台配置控件。

## 兼容与回滚

兼容：

- 旧 project_type 不读取 `english_redub_voice_match_strategy`。
- 新表只被新模块读取；迁移失败时新语速策略降级为 legacy。
- 语速缓存缺失不阻断 voice_match。

回滚：

- 菜单入口和 blueprint 可单独下线。
- 删除或隐藏 `english_redub` 权限即可阻止用户进入。
- system setting 改回 `legacy` 即可停用新推荐逻辑。

## 风险

1. Preview ASR 成本和耗时：首次命中大量候选时可能慢。对策：异步/懒加载缓存；voice_match 当次只计算 top100 缺失项，失败降级。
2. 语速不是音色：权重过大会牺牲音色。对策：先 top100，再低权重重排，并设置 similarity floor。
3. 英文重配音不是翻译：现有 translate UI 文案可能误导。对策：新模块页面使用“文案适配 / 英文重写”措辞；底层字段名可沿用 `translate` step 以减少改动。
4. 极短片段无法自然配音：语速推荐只改善声音候选，不能替代句级收敛和 fallback。详情页要保留 warning。
5. 工作台 hard-coded API：现有模板多处判断 `/api/omni-translate` 和 `/api/multi-translate`，实现时必须系统性补齐 `english_redub`。

## 验证计划

单元测试：

- `tests/test_english_redub_voice_match.py`
  - legacy 策略完全按 similarity 排序。
  - timbre_speed 先保留 top100，再按 final_score 输出 top10。
  - preview rate 缺失时不报错并降级。
  - source ASR 语速忽略极短异常段。
- `tests/test_english_redub_routes.py`
  - 未登录页面 302。
  - 登录后列表页 200。
  - start 强制写入 `source_language=en`、`target_lang=en`。
  - confirm/rematch 写权限只允许 owner/admin。
- `tests/test_permissions.py` 或现有权限测试：新权限默认角色可见。

回归测试：

- `tests/test_omni_translate_routes.py`
- `tests/test_multi_translate_routes.py`
- `tests/test_runtime_omni_dispatch.py`
- `tests/test_translate_detail_protocol.py`
- `tests/test_web_routes.py`
- `tests/test_av_sync_menu_routes.py`

手工 QA：

1. 打开 `/english-redub`，未登录 302，登录后 200。
2. 上传英文短视频，任务固定显示输入/输出英文。
3. voice_match 阶段停在声音选择，候选展示语速字段。
4. 后台关闭策略后重新 rematch，候选回到 legacy 排序。
5. 后台开启策略后重新 rematch，top10 有 `speed_score/final_score`。
6. 任务跑到 export，字幕和配音跟随最终音频时间线。
7. `/omni-translate` 新建和老任务详情行为不变。

## 实施顺序

1. 写实现计划，拆出路由/runner/UI/语速服务/测试步骤。
2. 新增 preview 语速缓存服务和纯函数测试。
3. 新增 speed-aware voice match 专用函数，不改原 `match_candidates` 默认行为。
4. 新增 EnglishRedubRunner，固定语言和能力配置。
5. 新增路由、服务注册、菜单、权限。
6. 接入设置页开关。
7. 跑相关 pytest。
8. 起 dev server，验证未登录 302、登录 200、创建和 voice_match 行为。
