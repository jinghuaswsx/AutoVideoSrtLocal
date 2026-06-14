# 全能视频翻译0615 — 全隔离 fork 设计

- **日期**: 2026-06-14
- **状态**: Approved（待写实施计划）
- **分支**: `feature/omni-translate-0615`（基于 master）
- **背景**: 见长期记忆 `video-translate-quality-next-phase` + `docs/superpowers/2026-06-12-omni-quality-handoff.md`

## 目标与初衷
下一阶段要从根本上抬高视频翻译质量基线，核心是优化「句级音频对齐链路」（一句最多 10 个音频候选，不惜成本换质量）。
**最高约束：绝不影响线上现有功能。** 因此先把生产主力模块完整复制成一个全隔离的新模块「全能视频翻译0615」，在副本上做优化，改任何东西都不可能波及线上。

## 克隆源（已用线上数据确认）
2026-06-14 登录公网线上环境 `http://14.103.60.217/` 实测两个 omni 入口的真实项目数：
- `/omni-translate`（V1「全能视频翻译」）= **73 个项目**（页面 2.88MB）← 生产实际主力
- `/omni-translate-v2`（V2）= 3 个项目（104KB）

按「哪个项目多以哪个为准」→ **克隆 V1（`/omni-translate`）**。
（注：文档曾称 V2 为稳定生产版，但真实使用数据是 V1 才是主力工作台。）

## 隔离策略：B（全物理复制）
用户选定 B（最大隔离，宁可代码重复）。核心边界：

### 复制（可随意改的层 → 全部进 `_0615` 命名空间）
| 线上原件 | → 0615 副本 |
|---|---|
| `web/routes/omni_translate.py`（Blueprint `omni_translate`，URL `/omni-translate`，`project_type="omni_translate"`，`@permission_required("omni_translate")`） | `web/routes/omni_translate_0615.py`（Blueprint `omni_translate_0615`，URL `/omni-translate-0615`，`project_type="omni_translate_0615"`，权限 `omni_translate_0615`） |
| `appcore/runtime_omni.py`（`OmniTranslateRunner(MultiTranslateRunner)`，680 行） | `appcore/runtime_omni_0615.py`（`OmniTranslate0615Runner`，**直接继承 `MultiTranslateRunner`，不继承 prod `OmniTranslateRunner`**；`project_type="omni_translate_0615"`，`profile_code="omni_0615"`） |
| `appcore/runtime_omni_steps.py`（959 行） | `appcore/runtime_omni_0615_steps.py` |
| `appcore/translate_profiles/omni_profile.py`（`OmniProfile`，`code="omni"`） | `appcore/translate_profiles/omni_0615_profile.py`（`Omni0615Profile`，`code="omni_0615"`） |
| `appcore/tts_strategies/sentence_reconcile.py` + `sentence_reconcile_v2.py` | `sentence_reconcile_0615.py`(+`_v2`)　← **候选生成/选择优化主战场** |
| `pipeline/duration_reconcile.py` + `duration_reconcile_v2.py` | `duration_reconcile_0615.py`(+`_v2`)　← **10 候选/时长收敛逻辑** |
| `appcore/translate_profiles/av_sync_profile.py`（`AvSyncProfile`，句级翻译路径） | `av_sync_0615_profile.py` |
| `web/templates/omni_translate_list.html` + 详情页壳 | `omni_0615_list.html` + 复制/参数化的详情壳 |

### 继承共享（不改、只用的深层基建——不复制，复制不现实）
- base `PipelineRunner`（`appcore/runtime/_pipeline_runner.py`，4462 行）
- `MultiTranslateRunner`（`appcore/runtime_multi.py`，1411 行，**仓库铁律零改动文件**）
- 通用媒体工具：`pipeline/extract`、`asr_router`、`ffutil`、`compose`、tts 引擎、`audio_*` 等
- **关键：继承 ≠ 修改**。0615 runner 继承 base 拿能力，但所有要改的逻辑都在复制出来的 `*_0615` 文件里，改它们碰不到线上 omni 文件。

## 接线（全是新增，不改原有行为）
1. `web/app.py`：加 `from web.routes.omni_translate_0615 import bp as omni_translate_0615_bp` + `app.register_blueprint(omni_translate_0615_bp)`（仿现有 `omni_translate_bp` 第 92-93、391-392 行）
2. `appcore/permissions.py`：加 3 处——权限定义 `("omni_translate_0615", GROUP_BUSINESS, "全能视频翻译0615", True, True)`（仿第 69 行）、URL 映射 `("omni_translate_0615", "/omni-translate-0615")`（仿第 127 行）、列表项（仿第 153 行）
3. DB：新 `project_type="omni_translate_0615"`，与线上项目完全隔离（0615 任务不混入 V1 列表，反之亦然）。复用 projects 表，靠 type 字段区分；如有 project_type 白名单/枚举校验需同步登记。
4. `web/templates/layout.html`：sidebar 加菜单项「🧪 全能视频翻译0615」（仿现有 omni 入口）
5. preset 系统：**复用线上** `appcore/omni_preset_dao` + `omni_plugin_config`（只读取，不改写）。0615 创建任务时把 plugin_config 快照存进自己的 task 行；preset 改动两边互不影响（本就是存快照）。**0615 默认 preset 解析逻辑与 V1 完全一致（保持"一模一样"）**——句级链路（`av_sentence + sentence_reconcile + sentence_units`）是下一阶段优化目标，本克隆 spec 不改默认行为。

## 范围（本 spec 只做"一模一样的克隆"）
- **In scope**：上面全隔离克隆，行为与线上 V1 完全一致（一模一样）。
- **Not in scope（下一个 spec）**：句级候选选择智能化、多候选多样性策略等"抬基线"优化——在复制出的 `*_0615` 文件上做，本次不动算法。
- **Not in scope**：评估底座（另起 spec）。

## 验收标准
1. **线上零影响（最硬指标）**：`git diff origin/master` 中，线上 omni 相关文件——`runtime_omni.py` / `runtime_omni_steps.py` / `omni_translate.py` / `omni_profile.py` / 原 `sentence_reconcile*.py` / 原 `duration_reconcile*.py` / `av_sync_profile.py` / `omni_translate_list.html`——**零改动**。改动只允许：新增 `*_0615` 文件 + `web/app.py`/`permissions.py`/`layout.html` 各加几行注册。
2. **行为等价**：用同一段测试视频，0615 入口与 V1 入口跑出功能等价的产物（asr/translate/tts/subtitle 结构 + step 顺序 + 关键 artifact 对齐；LLM 输出有随机性不要求字节一致）。
3. **路由门禁**：`/omni-translate-0615` 未登录 302、登录且有权限 200；新路由 `@login_required + @permission_required("omni_translate_0615")`。
4. **隔离验证**：0615 创建的任务只出现在 0615 列表，不出现在 V1 列表；反之亦然。
5. **测试**：新增 `*_0615` 模块的导入/实例化 smoke 测试；`python3 scripts/pytest_related.py --base origin/master --run` 通过；线上 omni 既有测试不受影响（仍全绿）。

## 实施注意
- 复制时把文件内所有 `omni_translate` → `omni_translate_0615`、`OmniTranslateRunner` → `OmniTranslate0615Runner`、`OmniProfile` → `Omni0615Profile`、`code="omni"` → `code="omni_0615"`、模板名、import 路径等成套改名，避免与线上符号冲突。
- profile/strategy 注册表（`appcore/translate_profiles/__init__`、`appcore/tts_strategies/__init__`）需登记 0615 的新 code，且**不动**原有 code 的注册。
- 详情页若与 V1 共用 `_translate_detail_shell.html`，优先用 `project_type` 参数区分跳转 URL，避免复制大模板；若模板里硬编码了 `/omni-translate/` 路径则必须复制或参数化。
- 实施基于 master 最新（本分支已基于 `38c4dfee`）。
