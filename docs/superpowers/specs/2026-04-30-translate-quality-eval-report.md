# 视频翻译模型质量对照评估报告

**日期：** 2026-04-30
**评估对象：** `video_translate.localize` 步骤的三个候选模型
**Spec：** [2026-04-30-translate-quality-eval-design.md](2026-04-30-translate-quality-eval-design.md)
**原始数据：** [results.json](2026-04-30-translate-quality-eval-results.json)

## 一句话结论

**建议把 `video_translate.localize` 默认绑定从 Claude Sonnet 4.6 切换到 Gemini 3 Flash**。质量在 8 个目标语种中 5 个略胜 Claude、2 个平手、1 个略输（日语），同时**速度快 2 倍、成本只有 Claude 的 1/8、稳定性 100%**。Gemini 3.1 Pro 在这条 use_case 完全不能用——31% 失败率、慢 5-15 倍、output token 异常多反而比 Claude 更贵。

## 1. 执行情况

48 次翻译（2 段英文原稿 × 8 目标语种 × 3 模型）。

| 模型 | 成功 | 失败 | 平均耗时 | 平均 input/output token |
|---|---|---|---|---|
| **Claude Sonnet 4.6** | 16/16 (100%) | 0 | 8.7s | 1400 / 610 |
| **Gemini 3.1 Pro** | 11/16 (69%) | **5** | **48.1s**（最长 141s） | 1000 / **2991**（异常） |
| **Gemini 3 Flash** | 16/16 (100%) | 0 | **4.7s** | 1200 / **496** |

Pro 的 5 次失败：
- A_ice_ball/de、fr、it：`ValueError: localized_translation requires sentences` —— 返回的 JSON sentences 字段为空或格式不对
- B_utility_knife/it：同上
- B_utility_knife/pt：`TypeError: LLM 返回内容为 None` —— 直接返空响应

## 2. 成本对比（按本次实测 token）

按官方价 + USD_TO_CNY=6.8：

| 模型 | input $/1M | output $/1M | 实测平均单条成本 | 相对 Claude |
|---|---|---|---|---|
| Claude Sonnet 4.6 | $3 | $15 | ¥0.090 | 1.00× |
| Gemini 3.1 Pro | $1.25 | $10 | **¥0.214** | **2.4×（更贵）** |
| Gemini 3 Flash | $0.30 | $2.50 | **¥0.011** | **0.12×（省 88%）** |

**Pro 比 Claude 还贵的原因**：Pro 平均 output 2991 token（比 Claude 的 610 高 5 倍），加上慢 5 倍，单价名义便宜但实际用起来更贵。output token 异常说明它在生成 JSON 时大量重复或冗余，没有按 schema 高效输出。

## 3. 质量逐条评估

按 4 维（准确度 / 流畅度 / 本地化 / 广告语调）打分（1-5）。**单元格为 4 维总分（满分 20）**。

### 3.1 A_ice_ball（厨房用品 / 大冰球模具）

| 目标语 | Claude Sonnet 4.6 | Gemini 3.1 Pro | Gemini 3 Flash | 优胜 |
|---|---|---|---|---|
| de 德语 | 18 | FAIL | **19** | Flash |
| es 西班牙语 | 18 | 15 | **19** | Flash |
| fr 法语 | **20** | FAIL | 19 | Claude |
| it 意大利语 | 16⚠️ | FAIL | **20** | **Flash 大胜** |
| ja 日语 | **20** | 17 | 19 | Claude |
| nl 荷兰语 | 19 | 19 | **20** | Flash |
| pt 葡萄牙语 | 19 | 16 | **20** | Flash |
| sv 瑞典语 | 16 | 18 | **20** | Flash |
| **A 段平均** | **18.3** | 17.5 | **19.5** | |

### 3.2 B_utility_knife（户外工具 / 多功能美工刀）

| 目标语 | Claude Sonnet 4.6 | Gemini 3.1 Pro | Gemini 3 Flash | 优胜 |
|---|---|---|---|---|
| de 德语 | 17⚠️ | 19 | 19 | 平 |
| es 西班牙语 | **20** | 16 | 19 | Claude |
| fr 法语 | 19 | 18 | **20** | Flash |
| it 意大利语 | 16⚠️ | FAIL | **20** | **Flash 大胜** |
| ja 日语 | **20** | 19 | 19 | Claude |
| nl 荷兰语 | 19 | 19 | **20** | Flash |
| pt 葡萄牙语 | **19** | FAIL | 16⚠️ | Claude |
| sv 瑞典语 | 19 | 19 | 19 | 平 |
| **B 段平均** | **18.6** | 18.3 | **19.0** | |

### 3.3 综合得分（去掉 FAIL 行）

| 模型 | A 段平均 | B 段平均 | 总平均 |
|---|---|---|---|
| Gemini 3 Flash | 19.5 | 19.0 | **19.3** ✅ |
| Claude Sonnet 4.6 | 18.3 | 18.6 | 18.5 |
| Gemini 3.1 Pro | 17.5 | 18.3 | 17.9 |

## 4. 关键发现

### 4.1 Gemini 3 Flash 是最优解

8 个语种中：
- **5 胜**：de、es、it、nl、sv（A 段都赢，B 段大多赢）
- **2 平**：fr、pt（B 段 Claude 略好）
- **1 略输**：ja（B 段 Claude 略好）

Flash 的优势体现在：
- **结尾 CTA 更明确**：`Mis dit niet`（nl）、`Approfittane`（it）、`Aproveita`（pt）、`så passa på`（sv）等，Claude 经常省略最后一句"Don't miss out"
- **更短更紧凑**：output token 比 Claude 少 19%，更适合短视频字幕

### 4.2 Claude 受 prompt 拖累，意大利语/葡萄牙语严重失分 ⚠️

System prompt 写明 `Use plain ASCII punctuation only`——Claude 严格遵守，把 `é è ò à ç ñ ü ß` 全部转写或剥光：

- 意大利语：`È stata` → `E stata`，`più` → `piu'`（破读法很怪）
- 德语：`füllen` → `fuellen`、`ß` → `ss`
- 西班牙语：变音保留较好（前几句有重音，后几句没有）

**Gemini 系列没严守这个限制**——保留了重音字符。在意大利语等重音敏感语言上**Flash 比 Claude 读起来更地道、更自然**。

#### 这条限制本来是干什么的？

`LOCALIZED_TRANSLATION_SYSTEM_PROMPT` 在 [pipeline/localization.py:13-19](../../pipeline/localization.py#L13-L19) 里写的：
> Do not use em dashes or en dashes. Use plain ASCII punctuation only, preferring commas, periods, and question marks.

**意图**：避免 em-dash（—）这类字符给字幕排版/TTS 引擎找麻烦。
**副作用**：把所有非 ASCII 字符全禁了，包括欧洲语言必需的重音符号。

#### 这是个独立的 prompt bug

不属于本次模型评估的核心问题，但在切换 Flash 后会因为 Flash 不严守这条限制反而**意外修复**（或部分修复）这个问题。建议另起一个 hotfix 把限制改成 `Avoid em-dashes and en-dashes; otherwise standard punctuation including accented characters is fine`。

### 4.3 Gemini 3.1 Pro 完全不能用

不仅成功率低（69%），即使成功的样本也有问题：

- **句子被切得过碎**：A_ice_ball/es 被 Pro 切成 12 个短句（Claude 9 句、Flash 10 句），破坏 TTS 节奏
- **耗时不可控**：fr / sv 都跑出 130+ 秒的极端情况，B/fr 跑了 141 秒，几乎是 Claude 的 18 倍
- **output token 失控**：平均 2991 token，是 Claude 的 5 倍——明显在大量重复或思考输出
- **比 Claude 还贵**：实际单条成本 ¥0.214 vs Claude ¥0.090，名义降价但实际涨价

排除 Pro 走这条路。

### 4.4 速度差距实质上的影响

| 模型 | 平均耗时 | 极端耗时 | 用户感受 |
|---|---|---|---|
| Flash | 4.7s | 8.8s | 几乎瞬时 |
| Claude | 8.7s | 11s | 明显等待 |
| Pro | 48.1s | 141s | **不可接受** |

video_translate 是用户主动启动的批量任务，每个任务都包含 1 次 localize 调用。切到 Flash 单任务流水线少等 4 秒；Pro 则会让用户觉得"卡住了"。

## 5. 决策建议

### 5.1 立即行动（建议 hotfix）

**把 `video_translate.localize` 默认绑定切到 Gemini 3 Flash（OpenRouter）。**

操作：在 [/settings?tab=bindings](/settings?tab=bindings) 里把 `video_translate.localize` 改成 `openrouter / google/gemini-3-flash-preview`，或者直接 SQL：

```sql
UPDATE llm_use_case_bindings
SET provider = 'openrouter', model = 'google/gemini-3-flash-preview'
WHERE use_case_code = 'video_translate.localize';
```

预期影响：
- 单次 localize 成本：¥0.090 → ¥0.011（**省 88%**）
- 月度 localize 节省：¥31.72/月 → ≈ ¥4/月，省 ¥28
- 单任务 localize 步骤耗时 -4 秒

### 5.2 顺手处理（不强制）

- 改 `LOCALIZED_TRANSLATION_SYSTEM_PROMPT` 的 ASCII 限制，让重音字符通过（独立 hotfix）
- Sonnet 4.6 在日语和长文本西班牙语上稍好——如果有日语主推产品，可以考虑日语单独保留 Claude（按语种分流），但收益比较有限

### 5.3 顺势思考

`video_translate.tts_script` 这条 use_case 月度开销 ¥193（占 67%），用同样模型。**值得用同样方法评估它**：tts_script 输入 token 比 localize 大几倍（要把 localize 输出 + 切分约束都塞进 prompt），如果 Flash 在 tts_script 也能保持质量，月度可以再省 ¥150-170。

但 tts_script 跟 localize 不同——它对**输出格式严格性**要求更高（要切成 TTS-friendly 的 blocks + subtitle_chunks），Flash 是否能稳定按 schema 输出需要单独验证。本次 localize 评估的 schema 简单，Flash 跑了 16/16 都没出 schema 校验失败，但 tts_script 不一定。

## 6. 附录

### 6.1 数据文件

- 原始结果：[2026-04-30-translate-quality-eval-results.json](2026-04-30-translate-quality-eval-results.json)
- 对照视图（48 段并排）：[2026-04-30-translate-quality-eval-compare.txt](2026-04-30-translate-quality-eval-compare.txt)
- 评估脚本：[tools/translate_quality_eval.py](../../../tools/translate_quality_eval.py)
- 设计文档：[2026-04-30-translate-quality-eval-design.md](2026-04-30-translate-quality-eval-design.md)

### 6.2 评估方法

由我（Claude）逐条阅读译文，按 4 维评分：
- **准确度**：是否完整保留原意，无遗漏、无杜撰
- **流畅度**：目标语言语法是否地道，有无机翻味
- **本地化**：文化习语、量词、品牌口吻是否贴合目标地区
- **广告语调**：是否保持原文促销/描述/引导风格

打分有主观成分，但**横向对比**（同一原稿同一语种下三家比较）能降低主观偏差影响。

### 6.3 一些值得记录的对照样本

**意大利语（Flash 大胜 Claude 的最典型例子）**

原文：It melts way slower than regular ice cubes, and the cooling effect is incredible.

- Claude（ASCII）："Si scioglie molto piu lentamente dei cubetti normali, e rinfresca in modo incredibile" — `piu` 应该是 `più`
- Flash：`Si scioglie molto più lentamente dei classici cubetti e rinfresca tantissimo` — 重音保留 ✅

**法语（Claude 略胜 Flash 的例子）**

原文：I've honestly given up. Everyone who comes to my place wants to take this giant ice ball with them.

- Claude：`Franchement, j'ai abandonné l'idée de les garder pour moi. Tous mes invités repartent avec une de mes grosses boules de glace.` — 法语化的"放弃藏起来"
- Flash：`Franchement, j'abandonne. Tous mes amis veulent me piquer ces boules de glace géantes.` — 简洁直白
- 都好，Claude 在地道度上微胜
