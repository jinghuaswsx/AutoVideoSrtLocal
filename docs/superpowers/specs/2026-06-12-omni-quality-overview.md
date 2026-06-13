# 全能视频翻译 · 翻译质量提升系列 — 总览

- **日期**: 2026-06-12
- **状态**: Approved（待 Codex 分块实施）
- **来源**: 2026-06-12 全能视频翻译功能全面审查（分支 `audit/video-translate-quality`）
- **审查结论一句话**: 现有流水线在"时长收敛"上工业级精细，但"翻译质量"只靠初译 prompt 一次性保证——收敛循环、tts_script 切分、批量翻译、截断兜底四个环节都可能在初译之后无声损伤译文，且没有质量维度的硬校验。

## 分块与优先级

按依赖与风险拆成 5 个独立块，**每块单独一个分支、单独实施、单独验收，严禁一次做多块**：

| 块 | 优先级 | 主题 | Spec | Plan |
|---|---|---|---|---|
| Block 1 | P0 | Prompt 正确性 + 首句 Hook / 尾句 CTA 职责 | [specs/2026-06-12-omni-quality-block1-prompt-correctness-design.md](2026-06-12-omni-quality-block1-prompt-correctness-design.md) | [plans/2026-06-12-omni-quality-block1-prompt-correctness.md](../plans/2026-06-12-omni-quality-block1-prompt-correctness.md) |
| Block 2 | P0 | tts_script 防静默改写 + asr_clean 可靠性 | [specs/2026-06-12-omni-quality-block2-deterministic-guards-design.md](2026-06-12-omni-quality-block2-deterministic-guards-design.md) | [plans/2026-06-12-omni-quality-block2-deterministic-guards.md](../plans/2026-06-12-omni-quality-block2-deterministic-guards.md) |
| Block 3 | P0/P1 | 收敛循环质量守门 + 首尾完整性 + 压缩重译兜底 | [specs/2026-06-12-omni-quality-block3-convergence-guard-design.md](2026-06-12-omni-quality-block3-convergence-guard-design.md) | [plans/2026-06-12-omni-quality-block3-convergence-guard.md](../plans/2026-06-12-omni-quality-block3-convergence-guard.md) |
| Block 4 | P1 | 产品上下文注入 + 长视频批间上下文 | [specs/2026-06-12-omni-quality-block4-context-enrichment-design.md](2026-06-12-omni-quality-block4-context-enrichment-design.md) | [plans/2026-06-12-omni-quality-block4-context-enrichment.md](../plans/2026-06-12-omni-quality-block4-context-enrichment.md) |
| Block 5 | P2 | 质量评估闭环升级（裁判 / 首尾维度 / 低分标红 / 聚合） | [specs/2026-06-12-omni-quality-block5-eval-loop-design.md](2026-06-12-omni-quality-block5-eval-loop-design.md) | [plans/2026-06-12-omni-quality-block5-eval-loop.md](../plans/2026-06-12-omni-quality-block5-eval-loop.md) |

建议实施顺序：Block 1 → 2 → 3 → 4 → 5。Block 1/2 互相独立可并行；Block 3 依赖 Block 1 的 rewrite prompt 保护段；Block 4/5 不依赖 3 但建议在 3 之后做以减少冲突。

## 全系列硬性红线（每块都必须遵守）

1. **音画对齐红线（最高优先级）**：
   - 最终接受窗口 `[video_duration − 1s, video_duration]`（`_run_tts_duration_loop` 的 `video_cap_lo/hi`）**一个字符都不许改**。
   - Stage-1 窗口（±10% / per-target speedup_window）、变速候选采样、段级候选拼装、`analyze_asr_window_gaps` / `apply_asr_window_audio_schedule` 时间轴调度、subtitle `asr_realign` 二次 ASR 对齐、loudness 匹配——全部不动。
   - 一切新增的"质量守门"只允许在**已满足字数/时长窗口的候选集合内部**做选择或拒绝，**绝不允许为了质量放宽任何时长约束**。压缩重译产物也必须通过与普通轮次完全相同的实测时长判定才能采纳。
2. **multi_translate 模块零改动**（仓库铁律）。`appcore/runtime_multi.py` 只允许"新增带默认值的可选参数"这种零行为变化的兼容性触碰，且本系列已设计为不需要改它。
3. **`pipeline/duration_reconcile*.py`（av_sentence 句级链路）不在本系列范围**，不碰。
4. **Prompt 双写一致性**：所有 prompt 改动必须同时落在 ① `pipeline/languages/prompt_defaults.py`（出厂默认）② DB `llm_prompt_configs`（运行时优先读 DB，改代码默认值**不会自动生效**）。Block 1 会交付 `scripts/reseed_prompt_defaults.py` 同步工具，后续块的 prompt 改动一律复用它。
5. **测试规则**：默认 `python3 scripts/pytest_related.py --base origin/master --run`，不跑全量；prompt/schema 改动必须有文本断言测试。
6. **每块收尾**：跑相关测试 → commit（分块小提交）→ push 分支 → 停下等人工验收，**不自行合并 master、不部署**。

## 业务背景速记（给实施者）

- 「全能视频翻译」= `/omni-translate`（V1 实验）+ `/omni-translate-v2`（V2 生产稳定版，本系列主要服务对象）。V2 固定 plugin_config：`asr_clean + standard + source_anchored + five_round_rewrite + asr_realign`。
- 链路：extract → ASR（豆包 zh/en，Scribe 其他）→ 人声分离 → asr_clean（同语言 LLM 纯净化）→ voice_match → alignment → translate（整段一次性，>18 段分批）→ TTS 5 轮收敛（字数预检 rewrite → tts_script 切分 → ElevenLabs → 实测时长）→ 响度 → 字幕二次 ASR 对齐 → 合成 → 导出 →（异步）质量评估。
- 产品诉求（用户原话级要求）：**开头前 3 秒必须是钩子（hook），结尾必须保留收尾/CTA 意图，二者在任何改写、压缩、兜底环节都不允许丢失**；同时全程保证音画对齐。
- 现有策略：译文**不发明新 CTA**（成片会另拼通用 CTA 片尾），但**源里已有的 CTA/收尾意图必须保留**。本系列所有"保 CTA"的表述均指后者，不改变"不发明 CTA"的既有策略。

## 上线运维清单（全部块合并部署后必须执行，否则部分改动不生效）

按顺序执行，每步贴输出留痕：

1. **Prompt DB 重 seed**（Block 1 交付；DB 优先级高于代码默认值，不跑则 runtime 仍读旧 prompt）：
   - `python3 scripts/reseed_prompt_defaults.py`（dry-run）→ 人工核对 DIFF/MISSING 清单；
   - 确认无管理员自定义需保留后 `python3 scripts/reseed_prompt_defaults.py --apply --yes`；
   - 再跑一次 dry-run 确认全部 SAME（退出码 0）。
2. **模型绑定同步**（`/settings?tab=bindings`，现网 DB 已有绑定行不受代码默认值影响）：
   - `asr_clean.purify_fallback` → `openrouter / anthropic/claude-sonnet-4.6`（Block 2）；
   - `translation_quality.assess` → `openrouter / google/gemini-3.5-flash`（Block 5）。
3. **测试环境真任务冒烟**（对照 Block 3 spec 验收标准 3，三个场景）：
   - 正常 zh→en omni V2 任务：跑通全程，Duration 面板可见 guard 记录，行为与现状一致；
   - 难收敛任务（长口播+短视频）：观察压缩重译终轮触发、瞄准 video−0.5s、首尾句保留；
   - 临时关 `OMNI_COMPRESS_RETRANSLATE_ENABLED` 强制触发截断：详情页出现尾部截断警示条（含被删句预览），验完恢复开关；
   - 附加检查：任务详情的初始翻译 prompt artifact 含 PRODUCT CONTEXT（批量任务，Block 4）、评估卡含 hook_strength / ending_integrity 分项（Block 5）。
4. **成本回归**：抽一条任务统计 rewrite_guard 调用次数（应 ≤ 每轮 3 次 × 轮数）。
