# Meta热帖分析 (美国AI分析 + 欧洲AI分析) SKILL 设计规范

本设计规范是本功能的唯一事实来源 (Single Source of Truth)，详细定义了由 Antigravity 扮演推理引擎以直接回填 Meta 热帖美国及欧洲分析指标的业务规范和字段约束。

## 1. 业务背景

Meta 热帖大盘提供对全球爆款电商视频的监测。针对同一个热帖，评估其在美国市场的可复制性与在欧洲各市场的本地化难度，是跨境选品与广告投放决策的重要闭环。
本 SKILL 专门提供一个非 API 付费调用的评估流程，由 Antigravity 自身作为推理主体，先跑美国 AI 分析，再跑欧洲 AI 分析，并全自动将结果结构化回填。

---

## 2. 数据库回填架构

### 2.1 美国 AI 分析表：`meta_hot_post_video_copyability_analyses`

| 数据库字段 | 类型 / 格式 | 说明 | 示例值 |
|---|---|---|---|
| `status` | varchar | 评估状态 | `'done'` |
| `overall_score` | int | 综合排名评分 (0-100) | `85` |
| `copyability_score` | int | 可复制程度评分 (0-100) | `80` |
| `meta_us_ad_fit_score` | int | US 广告匹配度 (0-100) | `90` |
| `product_fit_score` | int | 视频与商品URL链接匹配度 | `85` |
| `compliance_risk_score` | int | 合规风险分数 (分数越高越安全) | `95` |
| `recommendation` | varchar | 建议决策枚举 | `'copy' \| 'adapt' \| 'avoid'` |
| `summary` | text | 英文兼容性摘要 | `"Strong hooks and clear feature demos..."` |
| `summary_zh` | text | 中文解读综述 | `"该视频具有极强的开场吸引力，展示了核心卖点..."` |
| `summary_zh_status` | varchar | 中文翻译状态 | `'done'` |
| `llm_provider` | varchar | LLM 服务商 | `'antigravity'` |
| `llm_model` | varchar | LLM 模型 | `'gemini-3.5-flash'` |
| `analysis_json` | json | 完整分析结构 (包含 winning_angles, copy_notes, risk_notes 等) | `{"overall_score": 85, ...}` |
| `analyzed_at` | datetime | 分析完成时间 | `NOW()` |

### 2.2 欧洲 AI 分析表：`meta_hot_post_europe_assessments`

| 数据库字段 | 类型 / 格式 | 说明 | 示例值 |
|---|---|---|---|
| `status` | varchar | 评估状态 | `'done'` |
| `suitability_score` | int | 欧洲整体适配评分 (0-100) | `82` |
| `recommendation` | varchar | 建议决策枚举 | `'translate_and_launch' \| 'adapt_before_translation' \| 'not_recommended'` |
| `direct_reuse` | tinyint | 是否可以直接复用 | `1 \| 0` |
| `best_countries_json` | json | 推荐欧洲国家 | `["Germany", "France"]` |
| `country_scores_json` | json | 欧洲四国细分评分 | `{"GERMANY": 85, "FRANCE": 80, "ITALY": 75, "SPAIN": 78}` |
| `strengths_json` | json | 英文优势项列表 | `["Clear visuals", "High click potential"]` |
| `risks_json` | json | 英文风险项列表 | `["Language barrier", "CE compliance requirement"]` |
| `required_changes_json`| json | 英文所需调整项列表 | `["Add German voiceover", "Translate overlays"]` |
| `reasoning` | text | 英文完整推理依据 | `"The video shows great product fit but requires strong localization..."` |
| `strengths_zh_json` | json | 中文优势项列表 (自动回填) | `["直观的画面展示", "高点击潜能"]` |
| `risks_zh_json` | json | 中文风险项列表 (自动回填) | `["语言阻碍", "德国CE合规风险"]` |
| `required_changes_zh_json`| json | 中文调整项列表 (自动回填) | `["添加德语配音", "翻译屏幕上的文字"]` |
| `reasoning_zh` | text | 中文推理依据 (自动回填) | `"视频画面契合度高，但需要在目标语种上进行深度的本地化..."` |
| `llm_provider` | varchar | LLM 服务商 | `'antigravity'` |
| `llm_model` | varchar | LLM 模型 | `'gemini-3.5-flash'` |
| `llm_response_json` | json | 完整的底层推理大模型返回结构 | `{"suitability_score": 82, ...}` |
| `video_optimization_json`| json | 视频优化元数据 (缺省为 `{}`) | `{}` |
| `assessed_at` | datetime | 分析完成时间 | `NOW()` |

---

## 3. 推理指令集 (Prompts)

### 3.1 美国 AI 分析 (US Creative Audit)
评估输入：帖子的文本、商品名、最新互动数据等。由 Antigravity 针对美国市场的受众心智、Meta 广告投放规则和合规性 (IP / 夸大宣传 / 虚假疗效) 给出专业的 JSON 分析：
```json
{
  "overall_score": 85,
  "copyability_score": 80,
  "meta_us_ad_fit_score": 90,
  "product_fit_score": 85,
  "compliance_risk_score": 95,
  "recommendation": "copy",
  "summary": "This video utilizes a compelling 3-second hook displaying the solution immediately, making it highly copyable.",
  "summary_zh": "此视频在前3秒迅速展示解决方案，文案痛点直击人心，极具复制价值。",
  "winning_angles": ["突显产品即时效果", "痛点引入法"],
  "copy_notes": ["复制其开场分屏对比结构", "使用英文本土配音"],
  "risk_notes": ["需注意涉及材质功效夸大的广告合规"]
}
```

### 3.2 欧洲 AI 分析 (Europe Localization & Fit)
针对 DE/FR/IT/ES 市场，评估语种依赖性、配音/字幕要求，输出：
```json
{
  "suitability_score": 82,
  "recommendation": "translate_and_launch",
  "direct_reuse": true,
  "translation_fit_score": 85,
  "best_countries": ["Germany", "France"],
  "country_scores": {"GERMANY": 85, "FRANCE": 80, "ITALY": 75, "SPAIN": 78},
  "source_language_detected": "English",
  "speech_dependency": "medium",
  "on_screen_text_dependency": "medium",
  "needs_subtitle_translation": true,
  "needs_voiceover_or_dubbing": true,
  "needs_screen_text_replacement": true,
  "localization_difficulty": "medium",
  "country_localization_notes": {
    "Germany": ["Requires precise spec sheet", "CE mark notice needed"],
    "France": ["EPR recycling law notice compliance required"]
  },
  "strengths": ["Clear product visual demo", "Strong localized language appeal"],
  "risks": ["High localization cost", "Regulatory CE declaration barrier"],
  "required_changes": ["Replace EN subtitle overlay with local languages", "Record German voiceover"],
  "reasoning": "Product-market fit in Germany and France is superb, though localized subtitles are required before testing.",
  "strengths_zh": ["直观的产品画面演示", "强大的本地语种吸引力"],
  "risks_zh": ["本地化成本较高", "潜在的欧洲CE标识声明门槛"],
  "required_changes_zh": ["将英文屏显字幕替换为本地语种", "录制德语配音"],
  "reasoning_zh": "在德法等国产品市场适配度极佳，但测试前必须完成本地化字幕替换。"
}
```

---

## 4. 回填机制与脚本

提供 `tools/meta_hot_posts_antigravity_analyzer.py` 工具。该工具的运行机制：
1. 传入参数 `post_id`，查询 `meta_hot_posts` 获取当前帖子描述。
2. 输出帖子的元数据并由 Antigravity 生成两个 JSON (US 和 Europe)。
3. 提供 `backfill_results(post_id, us_result, eu_result)` 核心方法，利用数据库的原子插入/更新机制更新状态，保证操作一次性回填成功，不留下半吊子脏数据。
