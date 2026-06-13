# Block 3 — 收敛循环质量守门 + 首尾完整性 + 压缩重译兜底（P0/P1）

- **日期**: 2026-06-12
- **状态**: Approved（待实施）
- **总览**: [2026-06-12-omni-quality-overview.md](2026-06-12-omni-quality-overview.md)（音画对齐红线必读，本块是红线最相关的一块）
- **实施计划**: [plans/2026-06-12-omni-quality-block3-convergence-guard.md](../plans/2026-06-12-omni-quality-block3-convergence-guard.md)
- **前置**: Block 1 已合（base_rewrite 的 OPENING & ENDING PROTECTION 段是本块守门的 prompt 侧搭档）

## 背景与问题

`_run_tts_duration_loop`（`appcore/runtime/_pipeline_runner.py`）是 5 轮 rewrite + 变速 + bestpick 的时长收敛主体：

1. **接受标准只有字数/时长，没有语义质量守门**：rewrite 内循环 attempt 2 起温度拉到 1.0 求发散，候选只要词数落进容差窗口（`diff <= tolerance_abs`）就立即采纳送 TTS。高温下语义跑偏、钩子被削平、结尾被砍的候选没有任何拦截。
2. **物理截断砍尾**：所有轮次与变速都不达标时，`_truncate_audio_to_duration` 把音频按句从尾部截断到时长上限——带货视频的尾句（收尾/CTA）整句丢失，仅在 compose summary 留一条不显眼的记录。
3. **产品硬需求**：开头前 3 秒必须保持钩子功能、结尾必须保留收尾/CTA 意图，任何改写/压缩/兜底环节都不允许丢失。

## 目标

1. 字数落窗的 rewrite 候选在采纳前过一道**轻量 LLM 质量守门**（忠实度 + 首句 hook + 尾句收尾三项），不过门的候选被拒绝并把守门意见注入下一次 attempt 的反馈。
2. 5 轮全部未进最终时长窗时，在 bestpick 之前追加**一个"压缩重译"终轮**：按实测语速精确瞄准 `video_duration − 0.5s`，强制保首尾句，正常走 TTS 实测——把"走到物理截断"的概率压到最低。
3. 物理截断真发生时，**记录被截掉的句子文本并升级为任务级质量告警**，在任务详情页醒目展示。

## 非目标 / 红线

- **绝不修改**：最终接受窗 `[video−1s, video]`、stage-1 窗口、`_compute_next_target` 的既有 round 2/3+ 公式、变速采样、段级拼装、gap schedule、subtitle realign、loudness。守门只在"已满足字数窗口的候选集合"内做拒绝/选择；压缩重译轮的产物必须通过与普通轮**完全相同**的实测时长判定。
- 不动 `multi_translate`（本块改的 `_pipeline_runner.py` 是 multi/omni 共享基类——**所有新行为必须挂在 config 开关后，且开关只读全局 config**；默认开启对 multi 同样生效是可接受的预期行为，因为守门与压缩重译对任何 profile 都是纯增益，且不改变任何已收敛路径）。
- 不动 av_sentence / `duration_reconcile*.py` 句级链路。
- 守门不阻塞收敛：守门的定位是"多候选中提质"，attempt 耗尽时允许降级采纳（见 R1.4），**永不**因守门导致任务比现状更容易失败。

## 需求细则

### R1 rewrite 质量守门

**R1.1 新 use case**：`appcore/llm_use_cases.py` 注册 `video_translate.rewrite_guard`（"字数收敛重写守门"，默认 `gemini_vertex / gemini-3.1-flash-lite`，tokens 计费）。

**R1.2 新模块** `pipeline/rewrite_quality_guard.py`：

```
assess_rewrite_candidate(
    *, source_full_text: str, reference_translation_text: str,
    candidate_text: str, target_lang: str,
    task_id: str, user_id: int | None,
) -> dict
返回: {"fidelity": int 0-100, "hook_ok": bool, "ending_ok": bool,
       "issues": list[str], "passed": bool, "guard_error": bool,
       "_llm_debug_call": dict}
```

- system prompt（要素，措辞可润色）：质量守门员角色；对比 CANDIDATE（按时长改写的候选）vs REFERENCE（已验收的初译）与 SOURCE（原始转写）；输出 strict JSON `{fidelity, hook_ok, ending_ok, issues}`；`fidelity`=语义忠实（无捏造声明、无关键卖点丢失）；`hook_ok`=候选首句是否仍是合格的前 3 秒钩子（不要求逐词等于 REFERENCE）；`ending_ok`=候选尾句是否保留 REFERENCE 结尾的收尾/CTA 意图；issues ≤3 条中文短语。temperature=0.0，max_tokens=1000，json_schema strict。
- `passed = (fidelity >= 阈值) and hook_ok and ending_ok`。
- **fail-open**：LLM 调用异常 / 返回非 JSON → `passed=True, guard_error=True`（守门故障绝不阻塞生产），并 log.warning。

**R1.3 接入点**（内循环 `if diff <= tolerance_abs:` 处）：

- 开关 `config.OMNI_REWRITE_GUARD_ENABLED`（默认 `True`）、阈值 `config.OMNI_REWRITE_GUARD_MIN_FIDELITY`（默认 `75`）、每轮守门调用上限 `config.OMNI_REWRITE_GUARD_MAX_CALLS_PER_ROUND`(默认 `3`)。
- 流程：词数落窗 → 若守门关闭或本轮守门调用已达上限 → 按现状直接采纳（记 `guard_skipped`）。否则调守门：
  - `passed` → 采纳 break（现状行为）；
  - 不过 → 该候选不采纳，attempt 记录中追加 `guard` 结果（fidelity/hook_ok/ending_ok/issues），feedback_notes 注入：`QUALITY GATE FEEDBACK: the previous in-window candidate was REJECTED for quality: {issues}. Fix these while staying inside the word window. Keep sentence 1 as the hook and keep the final sentence's closing/CTA intent.`，继续下一 attempt。
- **R1.4 attempt 耗尽时的降级**：若存在"词数落窗但守门未过"的候选，选其中 fidelity 最高者采纳进 TTS（`round_record["guard_degraded"]=True`）。若没有任何落窗候选，保持现状（本轮跳过 TTS）。
- 全部守门结果写入 `round_record["rewrite_attempts"][i]["guard"]` 与轮级 `round_record["guard_summary"]`，debug 调用经 `_save_llm_prompt_debug` 落盘（与 rewrite attempt 同模式）。

### R2 压缩重译终轮（compress round）

- 触发条件：主 while 循环到达最后一轮结束仍无任何轮 `is_final`（即将走 bestpick），且 `config.OMNI_COMPRESS_RETRANSLATE_ENABLED`（默认 `True`）且 compress 轮未用过。
- 实现方式：仿照既有 `EXTRA_STAGE1_SPEEDUP_FALLBACK_ROUNDS` 先例**动态扩一轮**（`max_rounds_allowed += 1`），该轮标记 `round_record["compress_round"]=True`，与普通轮唯一差异：
  - target 计算旁路 `_compute_next_target`（不修改该函数的既有分支）：`target_duration = max(video_duration − 1.0, video_duration − 0.5)` 取 `video_duration − 0.5`，`target_words = max(3, round(target_duration × wps_measured))`（wps 用上一实测轮的 word_count/audio_duration，与现状口径一致）；direction 按 last_audio_duration 与 video_duration 比较（超长 shrink / 不足 expand）。
  - rewrite feedback 额外注入：`FINAL LENGTH-CRITICAL REWRITE: this is the last chance before hard audio truncation. Land inside the window. You MUST keep sentence 1 as the hook and keep the final sentence's closing/CTA intent; cut or expand only in the middle.`
  - 其余（守门、tts_script、TTS、实测、final 窗判定、stage1 变速路径）与普通轮完全一致——**复用同一循环体，不复制代码**。
- 轮数叠加上限：`MAX_ROUNDS + EXTRA_STAGE1_SPEEDUP_FALLBACK_ROUNDS + 1`，UI 轮次记录沿用现有结构（多一行 round 记录，前端无需改造）。

### R3 截断告警升级

- `_truncate_audio_to_duration`（或其调用方）：trim 结果增加 `removed_texts: list[str]`（被移除句段的 `tts_text`/`translated` 文本，按移除顺序）。
- 截断真正发生（`removed_count > 0`）时：
  - `task_state` 追加任务级告警字段 `quality_warnings`（list[dict]，append `{"type": "tail_truncated", "removed_count": N, "removed_texts": [...], "message": "尾部截断丢失 N 句，可能影响收尾/CTA 完整性"}`）；该字段不存在时初始化为空 list；
  - trim 的 round 记录（已有 `truncated` phase 事件）带上 `removed_texts` 前 3 句预览；
  - 任务详情页（omni / omni_v2 共用的 `_translate_detail_shell.html` 体系）在状态卡区域渲染醒目的黄色/红色警示条，文案：`⚠️ 配音尾部被截断 N 句（可能丢失收尾/CTA）：{前 1-2 句预览}`。前端实现跟随现有警示条模式（如已有 compose summary / fallback 提示的样式）。

### R4 可观测性

- `tts_generation_stats.finalize` 已汇总轮次——确认 compress 轮与 guard 字段不破坏其解析（如有字段白名单需补充）。
- 守门与 compress 的关键动作都要有 `self._emit_duration_round` 事件（沿用既有 phase 机制，新 phase 名：`quality_gate_rejected`、`compress_round`），前端 Duration Loop 面板按未知 phase 兼容展示（实施时确认前端对未知 phase 的容错，必要时在 JS 的 phase 文案表加两条中文文案）。

## 验收标准

1. 单测：守门 pass/fail/fail-open/每轮上限/降级采纳；compress 轮 target 计算；截断 `removed_texts` 与 `quality_warnings` 写入。`python3 scripts/pytest_related.py --base origin/master --run` 通过。
2. 红线自查：diff 中 `_tts_final_target_range`、`video_cap_lo/hi`、stage1 窗口、`_compute_next_target` 既有分支、变速/拼装/gap/subtitle/loudness 函数体零改动（compress 的 target 在调用处旁路计算）。
3. 人工验收（测试环境跑真任务）：
   - 正常任务：行为与现状一致（守门通过不增加轮次），Duration 面板可见 guard 记录；
   - 构造一条 5 轮难收敛任务（长译文短视频）：观察 compress 轮触发、瞄准 `video−0.5s`、首尾句保留；
   - 强制触发截断（临时把 compress 开关关掉）：详情页出现尾部截断告警条，含被删句预览。
4. 成本回归：guard 单次 flash-lite 调用 <1s/<0.01 元，最坏每轮 3 次 × 6 轮 ≤18 次，可接受；汇报中附实测一条任务的 guard 调用次数。
