# 音画同步 V2 句级收敛重做设计

日期：2026-04-28

## 背景

本次只重做“视频翻译音画同步”链路，不改普通视频翻译、多语言视频翻译、素材管理等其他模块。

现有音画同步 v2 已有按句翻译、ElevenLabs 生成、时长检测和局部重写的雏形，但它仍偏向“画面笔记 + 本土化生成 + 事后补救”。这会让音画同步难以稳定：译文可能过长，重写后整段音频可能没有完全重建，变速范围也超过用户要求的 95%-105%。

基线测试还发现当前入口 `/video-translate-av-sync` 存在 500：`_task_workbench.html` 在入口页没有 `project` 变量时直接访问 `project.deleted_at`。这是现有问题，进入本次实现时先修复。

## 目标

1. 将音画同步 v2 的核心算法归零为句子级闭环。
2. 将音画同步本土化翻译 LLM 改为 OpenRouter `openai/gpt-5.5`。
3. 用强化后的单句本土化提示词，让每句都尽可能地道、口语化、目标市场化。
4. 先通过文案收敛把每句音频控制到目标时长的 95%-105%，再用 ElevenLabs speed 在 0.95-1.05 范围内微调。
5. 全流程中间结果可视化：能看见每步干了什么、收敛到什么程度、失败发生在哪一句和哪一步。

## 非目标

1. 不重做整站 UI。
2. 不改变普通视频翻译、多语言视频翻译和日语翻译链路。
3. 不引入本地 MySQL 或本地长期 Web 服务。
4. 不做全局自由重写式本土化。全局上下文只作为约束，最终输出必须保持句子级一一对应。
5. 不默认使用 `openai/gpt-5.5-pro`。先用 `openai/gpt-5.5` 验证质量和成本。

## 核心判断

句子级本土化是可行的，但它的上限不是“整段广告重写”的上限，而是“可对齐、可测量、可校对”的上限。音画同步优先级应是：

1. 句子对应关系稳定。
2. 语义和销售意图不丢。
3. 目标市场表达足够自然。
4. ElevenLabs 实测时长可收敛。
5. 人工能快速定位和校对问题句。

如果先做全局重写，整体本土化可能更自由，但句子时长、字幕落点、音频拼接都会失控。因此本次选择句级闭环。

## 新流程

音画同步 v2 使用以下流程：

1. 读取原视频 ASR 句子段，得到 `asr_index`、`source_text`、`start_time`、`end_time`、`target_duration`。
2. 生成或读取全局上下文：产品、卖点、目标市场、画面笔记、Hook/Demo/Proof/CTA 结构。
3. 调用 GPT-5.5 做句级本土化翻译。
4. 对每句计算目标字符区间。
5. ElevenLabs 按句生成首轮音频。
6. 测量每句真实 TTS 时长。
7. 对每句计算偏差比例：`tts_duration / target_duration`。
8. 如果落在 `0.95-1.05`，该句通过。
9. 如果短于 0.95，要求 GPT-5.5 扩写该句，但不得新增事实。
10. 如果长于 1.05，要求 GPT-5.5 压缩该句，优先删除废话、弱修饰、重复表达。
11. 每轮重写后重新生成该句 TTS 并测量时长。
12. 当文案收敛到 `0.95-1.05` 后，按需要用 ElevenLabs speed 微调，speed 只能在 `0.95-1.05`。
13. 用最终句音频重建 `tts_full.av.mp3`，再生成 `subtitle.av.srt`。
14. 页面展示每个环节和每句话的中间结果。

## GPT-5.5 UseCase

更新 `appcore/llm_use_cases.py`：

- `video_translate.av_localize`
  - provider: `openrouter`
  - model: `openai/gpt-5.5`
  - 用途：句级本土化首译

- `video_translate.av_rewrite`
  - provider: `openrouter`
  - model: `openai/gpt-5.5`
  - 用途：单句时长收敛重写

仍通过 `appcore.llm_client.invoke_chat(...)` 调用，不直接接 OpenAI SDK。

## 单句本土化提示词要求

首译提示词必须强调：

1. 每个原句必须返回一个目标语句，不合并、不拆分、不换序。
2. 允许本土化改写，但必须保留原句的业务意图、情绪功能和信息点。
3. 语言必须像目标市场真人短视频口播，不要翻译腔。
4. 保留句子的结构功能：Hook、痛点、卖点、证明、演示、CTA。
5. 不直译中文结构，不使用生硬书面表达。
6. 不新增原视频没有的信息，不夸大功效，不编造价格、材质、认证、承诺。
7. 尽量贴近目标字符区间；如果区间很窄，优先牺牲修饰语，不牺牲核心意思。
8. 句子应适合 TTS：短句优先，少用复杂从句，少用堆叠形容词。
9. 输出 JSON，包含 `asr_index`、`text`、`est_chars`、`localization_note`、`duration_risk`。

重写提示词必须强调：

1. 只改当前句，不改变其他句。
2. 当前句的意思、销售功能、画面对应关系必须不变。
3. 如果太长，压缩到更短、更口播、更直接。
4. 如果太短，扩写得更自然，但不能新增事实。
5. 目标是让 ElevenLabs 真实音频进入 `95%-105%`，不是机械凑字符。

## 数据结构

在 `variants.av` 下新增或规范化：

```json
{
  "sentences": [
    {
      "asr_index": 0,
      "source_text": "...",
      "text": "...",
      "final_text": "...",
      "start_time": 0.0,
      "end_time": 2.4,
      "target_duration": 2.4,
      "target_chars_range": [24, 32],
      "tts_duration": 2.45,
      "duration_ratio": 1.02,
      "speed": 1.0,
      "status": "ok",
      "issue": null,
      "attempts": []
    }
  ],
  "av_debug": {
    "model": "openai/gpt-5.5",
    "steps": [],
    "summary": {}
  }
}
```

每次重写追加 attempt：

```json
{
  "round": 1,
  "action": "shorten",
  "before_text": "...",
  "after_text": "...",
  "target_duration": 2.4,
  "tts_duration": 2.8,
  "duration_ratio": 1.17,
  "status": "needs_rewrite",
  "reason": "too_long"
}
```

## 可视化设计

参考多语言视频翻译的 artifact 和详情页展示方式，但音画同步 v2 新增专用“句级收敛面板”。

页面展示三层信息：

### 1. 流程级

显示当前阶段：

`ASR 原句 -> GPT-5.5 句级本土化 -> 首轮 TTS -> 时长检测 -> 文案收敛 -> 95%-105% 微调 -> 重建音频/字幕 -> 完成`

每个阶段显示状态、开始/完成时间、摘要、错误。

### 2. 句子级

表格列：

- 句号
- 原句
- 时间轴
- 目标时长
- 目标字符区间
- 首译
- 当前最终译文
- 首轮 TTS 时长
- 当前 TTS 时长
- 偏差百分比
- speed
- 状态
- 问题说明
- 操作：试听、查看轮次、手动重写

状态包括：

- `ok`
- `rewritten_ok`
- `speed_adjusted`
- `needs_rewrite`
- `warning_short`
- `warning_long`
- `tts_failed`
- `llm_failed`

### 3. 调试级

每个环节可展开：

- GPT-5.5 请求 prompt
- 模型原始 JSON
- 每轮重写输入和输出
- ElevenLabs 每句音频时长
- 最终选择原因
- 失败堆栈或用户可读错误

## 错误处理

1. LLM JSON 解析失败：标记 `llm_failed`，保留原始响应，页面可展开查看。
2. 某句 TTS 失败：标记 `tts_failed`，不中断所有已完成句子的可视化。
3. 多轮重写仍无法进入区间：标记 `warning_long` 或 `warning_short`，保留最终最接近版本，要求人工校对。
4. speed 计算超出 `0.95-1.05`：不执行越界变速，改为继续文案重写或提示人工处理。
5. 每次局部重写后必须重建整段音频和字幕，避免页面显示和成品不一致。

## 测试策略

单元测试：

- GPT-5.5 use case 默认绑定。
- 句级翻译 prompt 包含一一对应、本土化、时长约束。
- `classify_overshoot` 使用 95%-105%。
- speed 只允许 0.95-1.05。
- 重写轮次记录 attempts。
- 最终整段音频来自最新句音频。

路由/模板测试：

- `/video-translate-av-sync` 不再 500。
- 音画同步详情页能展示句级收敛面板。
- 有失败句时显示问题说明和调试入口。

聚焦回归：

```bash
pytest tests/test_av_translate.py tests/test_duration_reconcile.py tests/test_appcore_runtime.py tests/test_av_sync_menu_routes.py -q
```

必要时补充 web route 测试：

```bash
pytest tests/test_web_routes.py -q
```

## 实施顺序

1. 修复 `/video-translate-av-sync` 当前 500。
2. 更新 GPT-5.5 use case 默认模型。
3. 重写句级翻译 prompt 和 response schema。
4. 重写 duration reconcile 为句级闭环，收紧到 95%-105%。
5. 增加 attempts/debug 数据结构。
6. 确保最终整段音频和字幕总是从最新句音频重建。
7. 新增句级收敛面板。
8. 补测试并跑聚焦回归。

## 验收标准

1. 音画同步入口页可打开。
2. 新任务能用 GPT-5.5 生成句级本土化译文。
3. 每句都能看到目标时长、真实时长、偏差和收敛状态。
4. speed 不会超出 `0.95-1.05`。
5. 出错时能定位到具体阶段和具体句子。
6. 聚焦测试通过。
7. 测试环境 `http://172.30.254.14:8080/` 可验证页面和接口。
