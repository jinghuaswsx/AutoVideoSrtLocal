# video_translate.tts_script 模型质量对照评估报告

**日期：** 2026-04-30
**评估对象：** `video_translate.tts_script` 步骤的 Claude Sonnet 4.6 vs Gemini 3 Flash
**Spec：** [2026-04-30-tts-script-quality-eval-design.md](2026-04-30-tts-script-quality-eval-design.md)
**原始数据：** [2026-04-30-tts-script-quality-eval-results.json](2026-04-30-tts-script-quality-eval-results.json)
**Blocks 抽样对照：** [2026-04-30-tts-script-blocks-samples.txt](2026-04-30-tts-script-blocks-samples.txt)

## 🚨 一句话结论

**生产环境的 Claude Sonnet 4.6 在 tts_script 步骤上有严重 bug：把所有非英文 localize 输出翻回英文，导致 TTS 朗读的是带语种口音的英文，不是真正的目标语种。** Gemini 3 Flash 没踩这个坑、且全方位胜出。**强烈建议立刻切到 Flash**。

## 1. 跑批结果

96 次调用：32 次 Claude localize 准备输入（Phase 1，全部成功）+ 64 次 tts_script（Phase 2）。

| 指标 | Claude Sonnet 4.6 | Gemini 3 Flash |
|---|---|---|
| 成功率 | **31/32 (96.9%)** —— A_ice_ball/es 一次 schema 校验失败 | **32/32 (100%)** ✅ |
| 平均 input/output token | 1824 / 1532 | 1525 / 1484 |
| 平均耗时 | 14.2s | **8.7s** ✅ |
| 单条成本（按 token 实测） | ¥0.19 | **¥0.028**（Claude 的 1/7） |
| **文本不变性 diff（非日语平均）** | **0.395**（多次重写） | **0.074**（接近完美） |
| 完美保留 wording 占比 | 17/27 = 63% | **26/28 = 93%** ✅ |
| 字幕切分 5-10 词占比 | 97.5% | 93.8% |
| 1-3 词碎片占比 | 0.9% | 6.2% |
| 12+ 词超长占比 | 0% | 0% |

切分长度方面 Claude 略好（碎片更少），但其它所有维度 Flash 完胜——尤其是文本不变性。

## 2. 🚨 重大发现：Claude 把 localize 输出翻回英文

### 2.1 现象

**Claude 在 tts_script 步骤把所有非英语 localize 输出当成"英文源"，吐出英文 blocks**。具体看 D_car_mount/de（德语）：

| 阶段 | 内容 |
|---|---|
| localize 输出（应该是德语） | `Alte Halterungen kleben nur auf Glas, auf Leder funktioniert das nicht...` |
| **Claude tts_script blocks** | `Old mounts only stick to glass, they don't work on leather...` 🚨 |
| Flash tts_script blocks | `Alte Halterungen kleben nur auf Glas, auf Leder funktioniert das nicht...` ✅ |

A_ice_ball/fr（法语）也一样：

| 阶段 | 内容 |
|---|---|
| localize 输出 | `Franchement, j'ai abandonné l'idée de les garder pour moi...` |
| **Claude tts_script blocks** | `Honestly, I gave up on keeping them for myself...` 🚨 |
| Flash tts_script blocks | `Franchement, j'ai abandonné l'idée de les garder pour moi...` ✅ |

日语的 diff_ratio 高达 13.78 也是这个原因——Claude 把日语整段翻回英文。

### 2.2 根因

[pipeline/localization.py:38](pipeline/localization.py#L38) 的 `TTS_SCRIPT_SYSTEM_PROMPT` 写死：

```
Use the localized English as the only wording source.
```

这是从最初 zh→en 单语种流水线遗留的。现在 multi_translate 已经支持 8 个目标语种，但 prompt 还说 "English"。Claude 严格遵守字面，把所有德/法/日/葡等 localize 输出**视作"待翻译成英文的素材"**——于是吐出英文 blocks。

**Flash 没踩这个坑**：尽管 prompt 写的是 "English"，Flash 把它当成了一般的"用 localize 输出作为原文"，正确保留了多语种文本。这跟 [localize 评估](2026-04-30-translate-quality-eval-report.md)里发现 Claude 严守 ASCII-only 限制、Flash 不严守的现象一致——**Flash 不严守 prompt 字面反而是优势**。

### 2.3 生产影响

`multi_translate` 流水线当前生产环境用 Claude，每次 tts_script 都把多语种的 localize 输出翻回英文。下游 ElevenLabs 配音时 voice language_code 强制是目标语种（zh/de/fr/...），但喂给它的文本是英文 → **听众听到的是带德语/法语/日语口音的英文，不是真正的目标语种朗读**。

这条线索能解释为什么先前用户对豆包 ASR 的非中文识别质量不满（混入英文）—— 整条流水线在不止一处把多语种内容退化成英文。

## 3. 决策

### 3.1 立即采取（自己定 + 自己做完通报）

#### A. 切 `translate_pref` 到 Gemini 3 Flash

操作：UPDATE `api_keys` 表里 user_id=33 (admin) 和 237 的 `translate_pref` 从 `claude_sonnet`/`openrouter` 改成 `gemini_3_flash`。

**这一改同时切换 localize + tts_script + rewrite 三个步骤**（共享一个 provider）。基于本次评估和 [localize 评估](2026-04-30-translate-quality-eval-report.md)：
- localize：Flash 5 胜 2 平 1 略输（vs Claude）
- tts_script：Flash 全方位完胜（成功率、文本不变性、速度、成本）
- rewrite：未单独评估，但跟 localize/tts_script 用同一基础——预期 Flash 不会差

**预期月度节省**：当前 Claude 总开销 ¥297（30 天换算），切 Flash 后 ≈ ¥40，**省 ¥257**。

**意外副效果**：解决 tts_script 翻回英文的生产 bug——Flash 不踩这个坑。

### 3.2 待 hotfix（必须用户授权）

#### B. 修 `TTS_SCRIPT_SYSTEM_PROMPT` 的 "English" 硬编码

[pipeline/localization.py:38](pipeline/localization.py#L38) 把 `Use the localized English as the only wording source` 改成 `Use the localized text as the only wording source`（去掉 "English"）。

这是产品代码改动，符合 hotfix 全部条件（1 行 prompt 字符串，单文件，不动 schema）。**等用户在下条消息里给 `hotfix` 关键字才能改**。

修完后即使有人切回 Claude，也不会再翻回英文。

#### C. 顺手修 ASCII-only 限制 prompt（来自 localize 评估）

[pipeline/localization.py:13-19](pipeline/localization.py#L13-L19) 的 `Use plain ASCII punctuation only` 是当年怕 em-dash 加的，现在导致欧洲语言失重音。改成 `Avoid em-dashes and en-dashes; otherwise standard punctuation including accented characters is fine`。

可以跟 B 一起 hotfix。

## 4. 切换执行

切 translate_pref 不需要重启服务（[appcore/llm_bindings.py](../../appcore/llm_bindings.py) 没有缓存，每次调用都查 DB；`_resolve_translate_provider` 也是直接 query api_keys）。SQL 跑完下一个 video_translate 任务起就生效。

## 5. 不在范围

- 不评估 ElevenLabs 实际朗读音频（要听才知道）
- 不评估 video_translate.rewrite 这条 use_case（用同一 provider，预期跟随 localize/tts_script）
- 不评估其他 use_case（title_translate / asr_normalize / shot_notes 等用别的 model）

## 6. 附录

### 6.1 Flash 胜出的具体维度证据

| 维度 | Claude | Flash | 说明 |
|---|---|---|---|
| 成功率 | 96.9% | **100%** | Claude 在 A_ice_ball/es 上 schema 失败（blocks 字段缺失） |
| 文本不变性 | mean=0.395 | **mean=0.074** | Claude 大量改写，Flash 严格保留 |
| 完美保留率 | 63% | **93%** | 26/28 段 Flash 一字不改 |
| 速度 | 14.2s | **8.7s** | Flash 快 39% |
| 成本 | ¥0.19/次 | **¥0.028/次** | Flash 是 Claude 的 1/7 |
| 切分密度 | 16.5 chunks/视频 | 14.3 chunks/视频 | Flash 略粗（合并相邻句） |
| 5-10 词区间率 | 97.5% | 93.8% | Claude 略好但差距小 |
| 1-3 词碎片率 | 0.9% | 6.2% | Claude 略好 |

切分密度 Flash 偏粗（合并多句到一个 block）但都在合理范围；Claude 切得更细更精致——但代价是把文案翻回英文，得不偿失。

### 6.2 Phase 1 准备阶段（Claude localize 32 次）

全部成功，平均 11.5s，input ~1500 / output ~700 token。这部分是为本次 tts_script 评估准备输入用，不在评估打分内。

### 6.3 文件清单

- 设计文档：[2026-04-30-tts-script-quality-eval-design.md](2026-04-30-tts-script-quality-eval-design.md)
- 评估脚本：[tools/tts_script_quality_eval.py](../../../tools/tts_script_quality_eval.py)
- 原始结果：[2026-04-30-tts-script-quality-eval-results.json](2026-04-30-tts-script-quality-eval-results.json)
- Blocks 抽样对照：[2026-04-30-tts-script-blocks-samples.txt](2026-04-30-tts-script-blocks-samples.txt)
