# Omni 合并任务交付总结（2026-05-07）

- **目标**：把 `omni_translate` / `sentence_translate` / `translate_lab` 三个实验模块合并到 `/omni-translate/` 实验大本营，做成插件式可抽拔（preset + 8 分组能力点 + 互斥/依赖联动）。**multi 不动**。
- **设计文档**：[`docs/superpowers/specs/2026-05-07-omni-translate-merge-design.md`](../specs/2026-05-07-omni-translate-merge-design.md)
- **实施计划**：[`docs/superpowers/plans/2026-05-07-omni-translate-merge.md`](../plans/2026-05-07-omni-translate-merge.md)
- **状态**：✅ Phase 0-7 全部上线，本机即生产部署完毕。

---

## 各 Phase 上线情况

| Phase | 范围 | master commit | 上线 |
|---|---|---|---|
| 0 | 基础抽象到位（PR1-PR6 + design/plan docs） | `9ea61933` | ✅ |
| 1 | DB schema + DAO + CRUD API + 4 baseline preset seed | `b50b72c1` | ✅ |
| 2 | omni runner plugin_config-driven dispatch + 5 算法体物理复制 + projects.plugin_config 字段 | `fe97c875` | ✅ |
| 3 | POST `/api/omni-translate/start` 接收 plugin_config / preset_id | `168d331d` | ✅ |
| 4 | 新建任务弹窗 UI（preset selector + 8 分组能力点 + 互斥/依赖联动 + 另存为/删除）| `b565f98b` | ✅ |
| 5 | `/settings?tab=omni_preset` admin tab（系统级 + 用户级 CRUD + 全站默认）| `5e16bbf1` | ✅ |
| 6 | translate_lab UI deprecate（banner + 按钮 disabled + POST 410；代码不删） | `5725cd4c` | ✅ |
| 7 | 4 套等价 preset E2E smoke 验收（9 测试全过；本文档） | TBD（本 PR） | ✅ |

---

## 4 套等价 preset 验收

按 spec §7.1 + plan §7，4 个系统级 baseline preset 各自的 step 顺序 + 完整性走过自动化 smoke 测试。每一步详细行为由 Phase 2 的 `test_runtime_omni_dispatch.py` (20 tests) 覆盖，本 phase 聚焦"端到端 step list 完整 + 不变量"。

| Preset | step 数 | 顺序 |
|---|---|---|
| `multi-like` | 12 | extract → asr → separate → asr_normalize → voice_match → alignment → translate → tts → loudness_match → subtitle → compose → export |
| `omni-current`（全站默认） | 12 | extract → asr → separate → **asr_clean** → voice_match → alignment → translate → tts → loudness_match → subtitle → compose → export |
| `av-sync-current` | 11 | extract → asr → separate → asr_normalize → voice_match → translate → tts → loudness_match → subtitle → compose → export（**跳过 alignment**） |
| `lab-current` | 13 | extract → asr → separate → **shot_decompose** → asr_normalize → voice_match → alignment → translate → tts → loudness_match → subtitle → compose → export |

DB seed 时 system_settings 的 `omni_translate.default_preset_id` 写入 `omni-current` 的 id（admin 可在 `/settings?tab=omni_preset` 改）。

---

## 测试覆盖

总计 **288 测试** 在最终 sweep 全过：

- 抽象基础（PR1-PR7）：translate_profiles / tts_engines / tts_strategies / 现有 runner 行为对齐
- Phase 1 (75)：plugin_config validator (35) + DAO (17) + API (23)
- Phase 2 (20)：plugin_config-driven dispatch + 4 baseline cfg step list
- Phase 3 (12)：创建任务接口接 plugin_config / preset_id / fallback / silent fix
- Phase 4 (15)：新建任务弹窗模板/JS 静态断言 + Jinja parse + 零紫色 CSS
- Phase 5 (16)：settings tab nav + body + admin guard + 普通 user 拒绝
- Phase 6 (10)：deprecation banner + 按钮 disabled + POST 410 + sidebar 已无入口
- Phase 7 (9)：4 套 preset 端到端 step 顺序 + 不变量 smoke

---

## 怎么用（用户视角）

### 使用预设跑实验任务

1. 进 `/omni-translate/`
2. 点「+ 新建任务」
3. 默认勾选 = 全站默认 preset（admin 在 settings 设的，初始是 `omni-current`）
4. 顶部 dropdown 切其他 preset，能力点表单自动刷新
5. 任意改能力点 → 顶部出现「(已修改)」+「+ 另存为」按钮亮起
6. 点「+ 另存为」→ 输入名字 + 说明 → 创建用户级 preset，下次直接选
7. 用户级 preset 在 dropdown hover 可重命名/删除（admin 还能在 settings 里看到自己的用户级 preset）

### admin 管理预设

1. 进 `/settings?tab=omni_preset`（仅 superadmin 可见）
2. 上方：全站默认 preset dropdown（只列系统级），改后立刻影响所有人新建任务的初始勾选
3. 中间：系统级 preset 表，可编辑 / 删除（当前默认行删除被拒）
4. 下方：admin 自己的用户级 preset 表

### 加新 TTS provider / 新收敛策略

跟主线无关，PR5/PR6 已落地：

```python
# 1. 新建 appcore/tts_engines/<name>.py
class MyEngine(TtsEngine):
    code = "my_engine"
    def synthesize_full(...): ...
    def regenerate_with_speed(...): ...
    def get_audio_duration(...): ...

# 2. appcore/tts_engines/__init__.py:
register_engine(MyEngine())

# 3. 哪个 profile 想用就改 tts_engine_code = "my_engine"
```

新收敛策略走 `appcore/tts_strategies/` 同样模式。

### 加新能力点（未来扩展）

`appcore/omni_plugin_config.py` 的 `CAPABILITY_GROUPS` 是 single source of truth：
- 加新 entry → 前端弹窗 + admin tab 自动多一组（无前端修改）
- 后端 dispatch 在 `appcore/translate_profiles/omni_profile.py` 或 `appcore/runtime_omni.py` 加分支

---

## 已知 quirks / 后续清理候选

1. **`POST /api/translate-lab` 在生产返回 400 而非 410**：CSRF middleware 在路由 handler 之前先校验 token；curl 不带 token 直接撞 400。带 CSRF 的 admin / 真用户访问会得到 410（unit test 用 `authed_client_no_db` 覆盖到）。功能上等同于"拒绝"，spec 严格性差异不影响行为。
2. **`POST /api/translate-lab` 函数 body 在 410 返回后留有 dead code**（spec §6 防御性保留要求；以后想恢复 translate_lab 创建功能直接删那条 `return` 即可）。Python 静态分析可能提示 unreachable，无运行影响。
3. **PR4b/PR4c/PR7 跳过历史**：原 `refactor/omni-tts-pluggable` 分支上有 3 个搬运 commit（multi/omni 算法 body 进 profile + voice_match hook），跟 master 同期 llm_debug 注入冲突；本任务为避免大规模 conflict 解决而跳过，备份在 `refactor/omni-tts-pluggable-backup` 分支。如未来想接通这些可以从备份 cherry-pick + 手动解 conflict。
4. **omni 内 `_step_translate` 老方法已删**：替换为 `_step_translate_standard`（带 `source_anchored` 参数）。若有外部代码直接调 `runner._step_translate(task_id)`（非 OmniProfile.translate），需要换成 `_step_translate_standard(task_id, source_anchored=...)`。当前仓内 grep 无此类调用。
5. **`av_sentence` translate / `sentence_units` subtitle / `sentence_reconcile` tts 复用而非复制**：这三个直接调 `AvSyncProfile` 和 `SentenceReconcileStrategy`（它们本来就是 omni 抽象的一部分，不是 production runtime 模块）。这是 spec §6.2 的有意决策——只有 multi/V2 这种"另一套 production runtime"才需要物理复制隔离。
6. **手动验收**（spec §7.1）尚未跑：用同一段真测试视频跑 4 个 preset、对比每个的最终合成 mp4 + SRT 跟原 omni / sentence_translate / translate_lab 模块产物 functionally equivalent。**建议在合适时间做一次**，自动化 smoke 只覆盖 step 列表 + 不变量。

---

## 改动总规模

```
分支：feat/omni-merge-experimental → merged to master via fast-forward push
新增文件：
  appcore/omni_plugin_config.py        (+167)
  appcore/omni_preset_dao.py           (+165)
  appcore/runtime_omni_steps.py        (+623)
  db/migrations/2026_05_07_omni_translate_presets.sql
  db/migrations/2026_05_07_projects_plugin_config.sql
  web/routes/omni_preset_api.py        (+205)
  tests/test_omni_*.py × 5             (+1700+)
  tests/test_settings_omni_preset_tab.py (+159)
  tests/test_translate_lab_deprecated_ui.py (+146)
  tests/test_web_routes_omni_create_modal.py (+118)
  docs/superpowers/specs/.../*-design.md
  docs/superpowers/plans/.../*.md
  docs/superpowers/handoff/.../*.md (本文档)
修改文件：
  appcore/runtime_omni.py            （删 _step_translate 改 dispatch + 5 shim）
  appcore/translate_profiles/omni_profile.py（dispatch by cfg）
  web/routes/omni_translate.py       （Phase 3 接 plugin_config）
  web/routes/settings.py             （Phase 5 加 omni_preset tab）
  web/templates/omni_translate_list.html （Phase 4 弹窗）
  web/templates/settings.html        （Phase 5 admin tab）
  web/templates/translate_lab_list.html （Phase 6 banner）
  web/routes/translate_lab.py        （Phase 6 POST 410）
  web/app.py                         （Phase 1 注册 omni_preset_api blueprint）
保留不动：
  appcore/runtime_multi.py（multi 完全不动）
  appcore/runtime_v2.py / runtime_sentence_translate.py（防御性保留）
  ja / de / fr runner / blueprint（已废弃，不动）
```

约 **15 个 commit、3500+ 行新代码（含 spec/plan/handoff 文档）+ 288 测试**。
