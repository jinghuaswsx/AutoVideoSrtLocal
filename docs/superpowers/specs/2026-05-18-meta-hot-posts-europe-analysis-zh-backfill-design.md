# Meta 热帖欧洲分析中文回填

日期：2026-05-18

## 背景

`meta_hot_post_europe_assessments` 已保存欧洲投放适配分析。历史结果中的 `strengths_json`、`risks_json`、`required_changes_json` 和 `reasoning` 可能是英文；后续新分析已要求直接返回中文，但存量欧洲 Top50 仍需要中文解读。

## 目标

- 为欧洲分析结果增加中文缓存字段，不覆盖原始英文分析。
- 新增 `meta_hot_posts.europe_fit_translate` LLM 用例，使用 Google ADC 通道 `gemini_vertex_adc` 与 `gemini-3.1-flash-lite`。
- 存量 `status='done'` 的欧洲分析按 2 秒间隔回填中文字段。
- 发现 429 / quota / resource exhausted 时，本批停止，后续间隔 `+1s`，并继续观察。
- 前端欧洲 Top50 优先显示中文缓存；缓存为空时兜底显示原始字段。

## 数据模型

在 `meta_hot_post_europe_assessments` 增加：

- `strengths_zh_json JSON NULL`
- `risks_zh_json JSON NULL`
- `required_changes_zh_json JSON NULL`
- `reasoning_zh TEXT NULL`
- `zh_status VARCHAR(16) NOT NULL DEFAULT 'pending'`
- `zh_attempts INT UNSIGNED NOT NULL DEFAULT 0`
- `zh_error MEDIUMTEXT NULL`
- `zh_translated_at DATETIME NULL`
- `idx_meta_hot_post_europe_assessments_zh_status`

仅 `status='done'` 且至少存在一个英文分析字段的记录进入回填队列。失败记录可重试，超过默认尝试次数后不再选取。

## 翻译输入与输出

输入由原始结构化字段拼接：

- `Recommendation`
- `Best countries`
- `Strengths`
- `Risks`
- `Required changes`
- `Reasoning`

模型返回 JSON：

```json
{
  "strengths": ["中文优势点"],
  "risks": ["中文风险点"],
  "required_changes": ["中文调整项"],
  "reasoning": "中文综合判断"
}
```

要求不新增判断，只翻译和压缩表达；Meta、Facebook、Instagram、Reels、SKU、ROAS 等术语保留原术语。

## 前端

`/xuanpin/meta-hot-posts` 的欧洲评估面板使用：

- `europe_fit_strengths_zh || europe_fit_strengths`
- `europe_fit_risks_zh || europe_fit_risks`
- `europe_fit_required_changes_zh || europe_fit_required_changes`

`reasoning_zh` 暂不新增展示块，只随接口返回用于后续扩展。

## 验证

- 存储层测试覆盖欧洲中文队列、running、finish 成功/失败。
- 服务层测试覆盖 hydrate 输出中文字段。
- 翻译模块测试覆盖 Flash-Lite 调用、JSON 解析、2 秒节奏和 429 识别。
- 路由/模板测试覆盖欧洲面板中文优先兜底。
