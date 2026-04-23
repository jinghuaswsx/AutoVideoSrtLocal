# 日语视频翻译并入共享详情壳设计

## Goal

把现有 `视频翻译（日语）` 从旧版英语翻译工作台风格，重构为与 `多语言视频翻译` 基本一致的详情页结构、布局和流程体验，同时保留日语专用的翻译、时长收敛和字幕切段内核。

最终效果：

- 日语任务继续保留独立入口、独立任务类型、独立 runner
- 日语详情页改为复用多语言详情页的共享壳
- 用户看到的步骤组织、音色选择、字幕样式、预览、结果下载、Duration Loop 收敛展示，与多语言版本保持一致
- 日语继续使用字符预算、助词约束、日语 chunk 规则，而不是退回按英文词数处理

## Non-Goals

- 不重写现有 `multi_translate` 的翻译与收敛内核
- 不把日语硬塞进 `MultiTranslateRunner`
- 不修改其他语种当前已经稳定的翻译策略
- 不重做整个任务系统、事件系统或 artifact 下载体系

## Confirmed Decisions

- 采用“共享流程外壳，保留日语内核”的方案
- 保留 `/ja-translate` 路由与 `ja_translate` 任务类型
- 保留 `JapaneseTranslateRunner`
- 详情页改为共享详情壳，而不是继续维护单独的日语旧页脚本
- 日语的 Duration Loop 展示完全对齐多语言体验，但底层计量单位仍为日语字符预算
- 声音分离显示在音色选择上方，且与 ASR 展示顺序按日语需求调整
- 其他语种以当前多语言模块为基线，不允许因本次重构改变现有行为

## Problem Statement

当前日语模块虽然已经具备独立的翻译和配音能力，但详情页仍然主要依附旧 `_task_workbench` 逻辑，和多语言模块相比存在三类问题：

1. 页面壳落后
   日语页仍依赖旧 `voicePanel/configPanel` 结构，而多语言页已经切到新的音色选择器、字幕样式预览和结果对照布局。

2. 流程体验不一致
   多语言页的音色确认、候选库浏览、默认音色、试听、重匹配、字幕样式、生成前预览都已经形成顺手的一套流程；日语页仍然是旧工作台节奏。

3. 收敛过程可读性不足
   日语 runner 已经有收敛逻辑，但中间协议和命名没有完全对齐多语言壳，导致用户不能稳定看到与多语言版本同等级的收敛曲线、轮次原因、Prompt/译文/TTS script 入口。

## Target Architecture

重构后分为三层：

1. 共享详情壳
   负责详情页模板、公共脚本、步骤布局、音色选择器、字幕样式预览、Duration Loop 展示、artifact 预览与下载入口。

2. 任务协议适配层
   负责把 `multi_translate` 和 `ja_translate` 统一成同一种前端契约，包括步骤状态、音色候选、确认音色、round-file、artifact、收敛轮次字段形状。

3. 语言内核 runner
   `MultiTranslateRunner` 和 `JapaneseTranslateRunner` 各自保留自己的翻译、rewrite、TTS script、字幕切段、收敛目标计算逻辑。

关键原则：

- 前端只依赖统一协议，不依赖某个具体任务类型的内部细节
- 日语特化全部收敛在协议适配层和日语 runner，不向其他语种泄漏行为变化
- 共享壳抽取必须先保证 `multi_translate` 页面行为前后一致，再让 `ja_translate` 接入

## Shared Shell Design

新增一个“共享翻译详情壳”的概念，实际可以表现为共享模板组合和共享脚本行为，而不是一定要做成单文件。

共享壳负责以下区域：

- 顶部任务标题、返回链接、目标语种 badge
- 声音分离 / 音频提取卡片
- ASR 卡片
- 新音色选择器卡片
- 分段确认卡片
- 翻译确认卡片
- TTS / 字幕 / 合成 / 导出步骤卡片
- 字幕样式与视频预览
- Duration Loop 面板
- 结果下载区

### 日语模式的可见顺序

统一后的日语详情页视觉顺序固定为：

1. 声音分离 / 音频提取
2. ASR 文案结果
3. 音色选择
4. 字幕样式与预览
5. 分段确认
6. 翻译结果
7. Duration Loop 收敛过程
8. 字幕生成
9. 合成与导出

这与其他语种共享同一套壳，但允许根据任务模式对步骤显示顺序做轻量调整。

## Unified Workflow

共享壳下的标准主链为：

`extract -> asr -> voice_match -> alignment -> translate -> tts -> subtitle -> compose -> export`

### 其他语种

- 继续沿用现有多语言流程
- 保持现有 `voice_match` 等待用户选音色后继续的方式

### 日语

- 新增显式 `voice_match` 阶段，取代“旧页面顶部先选音色再开跑”的旧交互
- 上传后先跑 `extract` 和 `asr`
- ASR 完成后展示音色选择器
- 用户确认音色与字幕样式后，从 `alignment` 继续向下执行
- `translate -> tts -> subtitle -> compose -> export` 继续由日语 runner 驱动

这样做后，日语会与多语言在交互节奏上统一，但不丢失日语自己的翻译和收敛方法。

## Protocol Unification

这是本次设计的核心。

### 1. Shared detail shell must consume one API shape

共享壳统一只认当前任务的 `apiBase`，不能在脚本里写死 `/api/multi-translate/...`。所有这些能力都必须通过 `TASK_WORKBENCH_CONFIG.apiBase` 或等价配置拼出来：

- `GET /<task>/<id>`
- `GET /<task>/<id>/subtitle-preview`
- `GET /<task>/<id>/artifact/<name>`
- `GET /<task>/<id>/round-file/<round>/<kind>`
- `GET /<task>/<id>/voice-library`
- `POST /<task>/<id>/rematch`
- `POST /<task>/<id>/confirm-voice`
- `PUT /user-default-voice` 或共享默认音色接口

### 2. Step protocol must be normalized

两类任务都需要提供一致的步骤键和状态值：

- `extract`
- `asr`
- `voice_match`
- `alignment`
- `translate`
- `tts`
- `subtitle`
- `compose`
- `export`

状态值继续沿用现有公共约定：

- `pending`
- `running`
- `waiting`
- `done`
- `error`

### 3. Duration Loop payload must be normalized

共享 Duration Loop 面板需要稳定读取这些字段：

- `tts_duration_rounds`
- `tts_duration_status`
- `tts_final_round`
- `tts_final_reason`
- `tts_final_distance`

每一轮统一支持：

- `round`
- `audio_duration`
- `video_duration`
- `duration_lo`
- `duration_hi`
- `direction`
- `artifact_paths`

日语可继续附带：

- `ja_char_count`
- `tts_char_count`
- `target_chars`
- `next_target_chars`

共享壳优先读取通用字段；若任务模式为 `ja_translate`，则在文案层把“词数”替换为“可见字符数”。

### 4. Round artifact naming must become compatible

日语 runner 当前输出的中间文件命名需要补一层兼容，使共享壳能够直接读取：

- 初始翻译 prompt
- rewrite prompt
- 每轮译文
- 每轮 TTS script
- 每轮完整音频

允许内部继续保留现有日语专属文件名，但对共享壳暴露时，必须通过 `round-file` 路由映射到通用 kind。

## Japanese-Specific Logic To Preserve

以下能力必须完整保留，不允许为了并入共享壳而退化：

### 翻译生成

- 日语本土化翻译仍走 `ja_translate.localize`
- 继续依据日语习惯输出适合配音的口语文案

### 时长收敛

- 继续使用字符预算而不是英文词数预算
- 继续使用日语 speech rate / cps 估计
- 继续使用日语 rewrite 逻辑决定扩写或缩写

### TTS script

- 继续由日语专用 `build_ja_tts_script` 生成
- 继续按日语朗读节奏拆分 block

### 字幕切段

- 继续使用日语 chunk 规则
- 避免助词落在 chunk 开头
- 保留较短、适合日语阅读的字幕片段

## Multi-Language Compatibility Guardrails

为避免影响原有其他语种，重构必须遵守以下护栏：

1. `multi_translate` 后端逻辑只做兼容抽取，不改核心行为
2. 共享脚本新增的分支必须由任务类型或配置显式控制
3. 默认情况下，现有多语言页面渲染结果应保持不变
4. 日语新增协议必须补齐到共享壳要求，不能要求多语言为日语让步
5. 任何字段重命名都必须保留兼容读法，直到所有消费方迁完

## Implementation Slices

实现时按四块拆：

### Slice A. Shared shell extraction

- 提炼 `multi_translate_detail` 里的壳层组合
- 清理脚本中的硬编码多语言路径
- 让共享壳通过配置控制任务模式

### Slice B. Japanese route protocol completion

- 为 `ja_translate` 补齐 `voice-library / rematch / confirm-voice / round-file`
- 补齐 `voice_match` 阶段状态流转
- 让日语详情页接入共享音色选择器和共享 round-file 协议

### Slice C. Japanese duration payload normalization

- 统一日语轮次数据的通用字段
- 映射中间文件 kind
- 让共享 Duration Loop 面板直接工作

### Slice D. Regression hardening

- 固定其他语种不变
- 固定日语新页面顺序与收敛展示
- 补齐路由、协议、模板和 runner 级测试

## Error Handling

### 音色候选未就绪

- 页面应显示正在等待 `voice_match` 的状态，而不是空白列表
- 日语与多语言都使用同一等待文案机制

### round-file 尚未生成

- 共享壳允许按钮不存在或返回 “File not ready”
- 不允许因为某个 round artifact 缺失导致整个详情页渲染失败

### 日语缺少某些多语言专属字段

- 共享壳必须容忍缺省字段
- 日语模式通过 mode 分支决定展示哪些摘要文案

### 收敛失败或 5 轮未命中

- 共享面板必须继续展示最终采用轮次和原因
- 日语与多语言统一使用“最终采用原因”区块

## Testing Strategy

### Back-end

- `ja_translate` 新增 `voice-library / rematch / confirm-voice / round-file` 路由测试
- 共享 detail shell 所需配置项测试
- 日语 `tts_duration_rounds` 兼容字段测试
- 多语言回归测试，确认原逻辑不变

### Front-end template / script

- 日语详情页改为共享壳组合后的模板断言
- 共享音色选择器脚本不再写死 `/api/multi-translate`
- 日语模式下步骤顺序断言
- Duration Loop 在日语模式下显示字符型摘要

### End-to-end smoke

- 其他语种打开现有多语言任务详情，关键控件不回归
- 日语任务能跑到音色确认、继续执行、展示收敛轮次

## Rollout Plan

1. 先在共享壳上做到 `multi_translate` 页面前后无行为变化
2. 再让 `ja_translate` 接入共享壳
3. 先跑测试环境回归
4. 再用真实样例视频跑日语链路
5. 最后再发布线上

## Risks

### 风险 1：共享脚本抽取时误伤其他语种页面

缓解：

- 先写模板和脚本回归测试
- 以 `multi_translate` 当前页面为快照基线

### 风险 2：日语收敛数据与共享面板字段仍有错位

缓解：

- 先定义通用 round payload
- 通过 route 层映射文件和字段，不在前端写临时判断堆补丁

### 风险 3：日语引入 `voice_match` 阶段后状态流转不顺

缓解：

- 把 `voice_match` 明确纳入可恢复步骤
- 确认日语 runner 在确认音色后从 `alignment` 继续，而不是重复前序步骤

## Success Criteria

满足以下条件视为完成：

- 日语详情页整体结构、布局和大体流程与多语言版本一致
- 日语页面里声音分离、ASR、音色、字幕预览、收敛过程都采用共享壳逻辑
- 日语仍保留字符预算、日语 rewrite、日语字幕 chunk 内核
- 其他语种现有多语言流程无行为回归
- 日语真实任务能成功完成，并展示清晰的 Duration Loop 收敛过程
