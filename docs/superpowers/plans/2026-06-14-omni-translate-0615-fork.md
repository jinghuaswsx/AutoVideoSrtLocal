# 全能视频翻译0615 全隔离 Fork 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: 用 superpowers:subagent-driven-development 或 executing-plans 逐 Task 执行。Spec: [specs/2026-06-14-omni-translate-0615-fork-design.md](../specs/2026-06-14-omni-translate-0615-fork-design.md)。本计划是「复制+改名+接线+验证」型，不是「写新函数」型——每个 Task 的"代码"= 精确 cp 命令 + 改名映射 + 编译/导入验证。

**Goal:** 把生产主力模块 V1(`/omni-translate`) 全物理复制成全隔离的「全能视频翻译0615」(`/omni-translate-0615`)，行为一模一样，线上 omni 文件零改动。

**Architecture:** 把 omni 自有层（route/runner/steps/profile/socketio适配/模板）+ 句级链路（sentence_reconcile/duration_reconcile）复制到 `_0615` 命名空间；base PipelineRunner/MultiTranslateRunner 继承共享不复制。0615 profile 用「直接 import 派发」到 0615 strategy/profile，避免注册表 code 冲突、保持 preset 兼容。

**Tech Stack:** Python 3.12 / Flask / 现有 translate_profiles + tts_strategies 注册体系。

**分支:** `feature/omni-translate-0615`（已基于 master）。

---

## 改名映射（★每个复制文件都按此改名，长 token 先改避免子串误伤★）

| 原 token | → 0615 token | 说明 |
|---|---|---|
| `runtime_omni_steps` | `runtime_omni_0615_steps` | **先于** runtime_omni 改 |
| `runtime_omni` | `runtime_omni_0615` | 模块路径 |
| `sentence_reconcile_v2` | `sentence_reconcile_0615_v2` | **先于** sentence_reconcile 改 |
| `sentence_reconcile` | `sentence_reconcile_0615` | 模块路径 |
| `duration_reconcile_v2` | `duration_reconcile_0615_v2` | **先于** duration_reconcile 改 |
| `duration_reconcile` | `duration_reconcile_0615` | 模块路径 |
| `omni_profile` | `omni_0615_profile` | 模块路径 |
| `av_sync_profile` | `av_sync_0615_profile` | 模块路径 |
| `omni_pipeline_runner` | `omni_0615_pipeline_runner` | 模块路径 |
| `OmniTranslateRunner` | `OmniTranslate0615Runner` | 类名 |
| `OmniLocalizationAdapter` | `Omni0615LocalizationAdapter` | 类名（含 Japanese/Module 变体：`OmniJapaneseLocalizationAdapter`→`Omni0615JapaneseLocalizationAdapter`、`OmniModuleLocalizationAdapter`→`Omni0615ModuleLocalizationAdapter`） |
| `OmniProfile` | `Omni0615Profile` | 类名 |
| `AvSyncProfile` | `AvSync0615Profile` | 类名 |
| `SentenceReconcileStrategyV2` | `SentenceReconcile0615StrategyV2` | **先改** |
| `SentenceReconcileStrategy` | `SentenceReconcile0615Strategy` | 类名 |
| `"omni_translate"`（project_type/权限/蓝图名字符串） | `"omni_translate_0615"` | 字符串 |
| `/omni-translate`（URL） | `/omni-translate-0615` | URL |
| profile `code = "omni"` | `code = "omni_0615"` | **手动改这一行，禁止 sed 全局替换 "omni"** |
| profile `name = "全能实验（合并版）"` | `name = "全能视频翻译0615"` | 显示名 |

**通用验证命令（每 Task 复制后立即跑）:**
```bash
python3 -m py_compile <新文件>          # 语法
python3 -c "import <新模块路径>"          # 导入（捕获改名遗漏/循环引用）
```

---

### Task 1: 复制句级 pipeline 链路（duration_reconcile）

**Files:** Create `pipeline/duration_reconcile_0615.py`、`pipeline/duration_reconcile_0615_v2.py`

- [ ] **Step 1: 复制 + 改名**
```bash
cd <repo>
cp pipeline/duration_reconcile.py pipeline/duration_reconcile_0615.py
cp pipeline/duration_reconcile_v2.py pipeline/duration_reconcile_0615_v2.py
# 按改名映射处理内部 import（这两个文件可能互相引用 + 引用 localization 等共享模块，
# 仅改 duration_reconcile_v2→_0615_v2、duration_reconcile→_0615；不要动共享模块名）
for f in pipeline/duration_reconcile_0615.py pipeline/duration_reconcile_0615_v2.py; do
  sed -i 's/duration_reconcile_v2/duration_reconcile_0615_v2/g; s/duration_reconcile\b/duration_reconcile_0615/g' "$f"
done
```
- [ ] **Step 2: 验证**
```bash
python3 -m py_compile pipeline/duration_reconcile_0615.py pipeline/duration_reconcile_0615_v2.py
python3 -c "import pipeline.duration_reconcile_0615, pipeline.duration_reconcile_0615_v2"
```
Expected: 无报错。若 import 报 `No module named ...` 说明有未改的自引用，按提示补改。
- [ ] **Step 3: Commit** `git commit -am "feat(0615): copy duration_reconcile chain"`

### Task 2: 复制 TTS 句级策略（sentence_reconcile）

**Files:** Create `appcore/tts_strategies/sentence_reconcile_0615.py`、`sentence_reconcile_0615_v2.py`

- [ ] **Step 1: 复制 + 改名**（类名 + 引用的 duration_reconcile 指向 Task1 的 0615 版 + strategy code 改 0615 后缀）
```bash
cp appcore/tts_strategies/sentence_reconcile.py appcore/tts_strategies/sentence_reconcile_0615.py
cp appcore/tts_strategies/sentence_reconcile_v2.py appcore/tts_strategies/sentence_reconcile_0615_v2.py
for f in appcore/tts_strategies/sentence_reconcile_0615.py appcore/tts_strategies/sentence_reconcile_0615_v2.py; do
  sed -i 's/SentenceReconcileStrategyV2/SentenceReconcile0615StrategyV2/g; s/SentenceReconcileStrategy/SentenceReconcile0615Strategy/g; s/duration_reconcile_v2/duration_reconcile_0615_v2/g; s/duration_reconcile\b/duration_reconcile_0615/g' "$f"
done
```
- [ ] **Step 2: 改 strategy code**：手动编辑两文件里类属性 `code = "sentence_reconcile"`→`"sentence_reconcile_0615"`、`code = "sentence_reconcile_v2"`→`"sentence_reconcile_0615_v2"`（grep `code =` 定位）。**不要在 `appcore/tts_strategies/__init__.py` 注册它们**——0615 profile 走直接 import 派发（Task 3），避免全局注册表冲突。
- [ ] **Step 3: 验证**
```bash
python3 -m py_compile appcore/tts_strategies/sentence_reconcile_0615.py appcore/tts_strategies/sentence_reconcile_0615_v2.py
python3 -c "from appcore.tts_strategies.sentence_reconcile_0615 import SentenceReconcile0615Strategy; from appcore.tts_strategies.sentence_reconcile_0615_v2 import SentenceReconcile0615StrategyV2"
```
- [ ] **Step 4: Commit** `git commit -am "feat(0615): copy sentence_reconcile strategies (own codes, not globally registered)"`

### Task 3: 复制 profile（omni + av_sync）并改直接 import 派发

**Files:** Create `appcore/translate_profiles/omni_0615_profile.py`、`av_sync_0615_profile.py`；Modify `appcore/translate_profiles/__init__.py`

- [ ] **Step 1: 复制 av_sync_profile + 改名**
```bash
cp appcore/translate_profiles/av_sync_profile.py appcore/translate_profiles/av_sync_0615_profile.py
sed -i 's/AvSyncProfile/AvSync0615Profile/g; s/sentence_reconcile_v2/sentence_reconcile_0615_v2/g; s/sentence_reconcile\b/sentence_reconcile_0615/g; s/SentenceReconcileStrategyV2/SentenceReconcile0615StrategyV2/g; s/SentenceReconcileStrategy/SentenceReconcile0615Strategy/g' appcore/translate_profiles/av_sync_0615_profile.py
```
手动：把 av_sync_0615_profile 里所有 `get_strategy("sentence_reconcile...")` 改成直接 `from appcore.tts_strategies.sentence_reconcile_0615... import ...; strategy = ...()`（grep `get_strategy` 定位）；`code = "av_sentence"` 这类 plugin_config 取值**保持不变**（preset 兼容）。
- [ ] **Step 2: 复制 omni_profile + 改名 + 改派发**
```bash
cp appcore/translate_profiles/omni_profile.py appcore/translate_profiles/omni_0615_profile.py
sed -i 's/OmniProfile/Omni0615Profile/g; s/av_sync_profile/av_sync_0615_profile/g; s/AvSyncProfile/AvSync0615Profile/g' appcore/translate_profiles/omni_0615_profile.py
```
手动改 `omni_0615_profile.py`：① `code = "omni"`→`code = "omni_0615"`；② `name = ...`→`name = "全能视频翻译0615"`；③ `tts()` 里 `get_strategy(cfg["tts_strategy"])` 改成：`five_round_rewrite` 仍走 `get_strategy`（共享，不改），`sentence_reconcile`/`sentence_reconcile_v2` 直接 import 0615 strategy 实例化（仿 `omni_v2_profile.py` 现成写法）；④ `translate()` 里 `av_sentence` 分支 `from .av_sync_profile import AvSyncProfile` 改成 `from .av_sync_0615_profile import AvSync0615Profile`；⑤ `subtitle()` 的 `sentence_units` 分支同理指向 `AvSync0615Profile`。
- [ ] **Step 3: 注册 profile**（`__init__.py`，**只加不改原有**）：加 `from .omni_0615_profile import Omni0615Profile` 和 `register_profile(Omni0615Profile())`（仿第 52 行 `register_profile(OmniProfile())`）。`AvSync0615Profile` 是被 omni_0615 内部直接 import 的，**不需要**注册进 `_REGISTRY`（注册了也无害，但 code `av_sentence` 会与原 AvSyncProfile 冲突 → 所以**不注册**）。
- [ ] **Step 4: 验证**
```bash
python3 -m py_compile appcore/translate_profiles/omni_0615_profile.py appcore/translate_profiles/av_sync_0615_profile.py appcore/translate_profiles/__init__.py
python3 -c "from appcore.translate_profiles import get_profile; p=get_profile('omni_0615'); print(p.code, p.name)"
```
Expected: `omni_0615 全能视频翻译0615`
- [ ] **Step 5: Commit** `git commit -am "feat(0615): copy omni+av_sync profiles with direct-import dispatch; register omni_0615"`

### Task 4: 复制算法步骤 runtime_omni_steps

**Files:** Create `appcore/runtime_omni_0615_steps.py`

- [ ] **Step 1: 复制 + 改名**
```bash
cp appcore/runtime_omni_steps.py appcore/runtime_omni_0615_steps.py
sed -i 's/runtime_omni_steps/runtime_omni_0615_steps/g; s/runtime_omni\b/runtime_omni_0615/g; s/OmniTranslateRunner/OmniTranslate0615Runner/g' appcore/runtime_omni_0615_steps.py
```
- [ ] **Step 2: 验证**
```bash
python3 -m py_compile appcore/runtime_omni_0615_steps.py
python3 -c "import appcore.runtime_omni_0615_steps"
```
（注意：此文件 import `from appcore import runtime_omni_steps` 这类自引用要确认已被改成 _0615；若有 `from appcore.runtime_omni import` 也要指向 _0615——但 runner 在 Task5 才建，import 顺序无关，模块级 import 才会立刻失败，函数内 import 不会。先确保 py_compile 过。）
- [ ] **Step 3: Commit** `git commit -am "feat(0615): copy runtime_omni_steps"`

### Task 5: 复制 runner runtime_omni

**Files:** Create `appcore/runtime_omni_0615.py`

- [ ] **Step 1: 复制 + 改名**（长 token 先改）
```bash
cp appcore/runtime_omni.py appcore/runtime_omni_0615.py
sed -i 's/runtime_omni_steps/runtime_omni_0615_steps/g; s/runtime_omni\b/runtime_omni_0615/g; s/OmniTranslateRunner/OmniTranslate0615Runner/g; s/OmniJapaneseLocalizationAdapter/Omni0615JapaneseLocalizationAdapter/g; s/OmniModuleLocalizationAdapter/Omni0615ModuleLocalizationAdapter/g; s/OmniLocalizationAdapter/Omni0615LocalizationAdapter/g' appcore/runtime_omni_0615.py
```
- [ ] **Step 2: 改 project_type + profile_code**：手动编辑 `runtime_omni_0615.py`，`project_type = "omni_translate"`→`"omni_translate_0615"`、`profile_code = "omni"`→`"omni_0615"`（grep 定位）。确认 av_sentence helper 里 `from .av_sync_profile` 之类已指向 0615（grep `av_sync` / `AvSyncProfile`，若有则改 `av_sync_0615_profile` / `AvSync0615Profile`）。
- [ ] **Step 3: 验证**
```bash
python3 -m py_compile appcore/runtime_omni_0615.py
python3 -c "from appcore.runtime_omni_0615 import OmniTranslate0615Runner; r=OmniTranslate0615Runner.__name__; print(r)"
```
Expected: `OmniTranslate0615Runner`
- [ ] **Step 4: Commit** `git commit -am "feat(0615): copy omni runner"`

### Task 6: 复制 SocketIO 适配 omni_pipeline_runner

**Files:** Create `web/services/omni_0615_pipeline_runner.py`

- [ ] **Step 1: 复制 + 改名**
```bash
cp web/services/omni_pipeline_runner.py web/services/omni_0615_pipeline_runner.py
sed -i 's/runtime_omni\b/runtime_omni_0615/g; s/OmniTranslateRunner/OmniTranslate0615Runner/g; s/omni_pipeline_runner/omni_0615_pipeline_runner/g' web/services/omni_0615_pipeline_runner.py
```
- [ ] **Step 2: 验证**
```bash
python3 -m py_compile web/services/omni_0615_pipeline_runner.py
python3 -c "import web.services.omni_0615_pipeline_runner"
```
- [ ] **Step 3: Commit** `git commit -am "feat(0615): copy socketio pipeline adapter"`

### Task 7: 复制路由 + 列表模板

**Files:** Create `web/routes/omni_translate_0615.py`、`web/templates/omni_0615_list.html`

- [ ] **Step 1: 复制路由 + 改名**
```bash
cp web/routes/omni_translate.py web/routes/omni_translate_0615.py
sed -i 's/omni_pipeline_runner/omni_0615_pipeline_runner/g; s/runtime_omni\b/runtime_omni_0615/g; s/OmniTranslateRunner/OmniTranslate0615Runner/g; s#/omni-translate#/omni-translate-0615#g; s/"omni_translate"/"omni_translate_0615"/g; s/Blueprint("omni_translate"/Blueprint("omni_translate_0615"/g; s/omni_translate_list\.html/omni_0615_list.html/g' web/routes/omni_translate_0615.py
```
手动核对 `web/routes/omni_translate_0615.py`：① `Blueprint(...)` 名为 `omni_translate_0615`；② 所有 `@permission_required("omni_translate")`→`("omni_translate_0615")`（sed `"omni_translate"` 已覆盖，确认）；③ `project_type="omni_translate_0615"`；④ `render_template("omni_0615_list.html", ...)`；⑤ 详情页 render 用的模板：若复用 `_translate_detail_shell.html` 且其内部用 `project_type` 变量决定 URL 则不必复制；若硬编码 `/omni-translate/` 则需在 Step 2 复制详情壳或参数化（grep 详情壳里的 `/omni-translate`）。
- [ ] **Step 2: 复制列表模板 + 改 URL**
```bash
cp web/templates/omni_translate_list.html web/templates/omni_0615_list.html
sed -i 's#/omni-translate-v2#__OMNI_V2_PLACEHOLDER__#g; s#/omni-translate#/omni-translate-0615#g; s#__OMNI_V2_PLACEHOLDER__#/omni-translate-v2#g' web/templates/omni_0615_list.html
```
（占位避免误改 v2 链接；模板里若 extends 的 base/详情壳含 omni URL，按需处理。）
- [ ] **Step 3: 验证** `python3 -m py_compile web/routes/omni_translate_0615.py`
- [ ] **Step 4: Commit** `git commit -am "feat(0615): copy route + list template"`

### Task 8: 接线（全是新增/加行，不改原有行为）

**Files:** Modify `web/app.py`、`appcore/permissions.py`、`web/templates/layout.html`、`web/services/translate_detail_protocol.py`

- [ ] **Step 1: 注册蓝图**（`web/app.py`）：在第 92-93 行附近加 `from web.routes.omni_translate_0615 import bp as omni_translate_0615_bp`；在第 391-392 行附近加 `app.register_blueprint(omni_translate_0615_bp)`。
- [ ] **Step 2: 权限**（`appcore/permissions.py`，仿现有 omni_translate 三处）：
  - 权限定义（仿第 69 行）：`("omni_translate_0615", GROUP_BUSINESS, "全能视频翻译0615", True, True),`
  - URL 映射（仿第 127 行）：`("omni_translate_0615", "/omni-translate-0615"),`
  - 列表项（仿第 153 行）：加 `"omni_translate_0615",`
- [ ] **Step 3: 详情协议白名单**（`web/services/translate_detail_protocol.py:75`）：`{"omni_translate", "omni_translate_v2"}` → 加 `"omni_translate_0615"`。
- [ ] **Step 4: sidebar**（`web/templates/layout.html`）：仿现有 `🌍 全能视频翻译` 入口加一项 `🧪 全能视频翻译0615` 指向 `/omni-translate-0615`，套相同 `{% if has_permission('omni_translate_0615') %}` 门禁。
- [ ] **Step 5: project_type 白名单**：grep `omni_translate_v2` 找是否还有别的 project_type 枚举/校验点（如 task 创建校验、task_restart.py 的 runner 选择），逐一加 `omni_translate_0615` 的等价分支，指向 `OmniTranslate0615Runner` / `omni_0615_pipeline_runner`。
```bash
grep -rn "omni_translate_v2" appcore/ web/ | grep -v test | grep -viE "\.html"
```
- [ ] **Step 6: 验证** `python3 -m py_compile web/app.py appcore/permissions.py web/services/translate_detail_protocol.py`
- [ ] **Step 7: Commit** `git commit -am "feat(0615): wire blueprint/permission/sidebar/detail-protocol"`

### Task 9: Smoke + 隔离验证（验收）

- [ ] **Step 1: 导入 smoke 测试**（Create `tests/test_omni_0615_smoke.py`）
```python
def test_omni_0615_profile_registered():
    from appcore.translate_profiles import get_profile
    p = get_profile("omni_0615")
    assert p.code == "omni_0615"

def test_omni_0615_runner_imports():
    from appcore.runtime_omni_0615 import OmniTranslate0615Runner
    assert OmniTranslate0615Runner.project_type == "omni_translate_0615"

def test_omni_0615_blueprint_registers():
    from web.app import create_app  # 或现有 app 工厂名
    app = create_app()
    rules = {r.rule for r in app.url_map.iter_rules()}
    assert any(rule.startswith("/omni-translate-0615") for rule in rules)
```
跑：`python3 -m pytest tests/test_omni_0615_smoke.py -q` → 期望 3 passed。（`create_app` 名字按现有 app.py 实际工厂调整。）
- [ ] **Step 2: ★线上零影响硬验收★**
```bash
git diff origin/master --stat -- appcore/runtime_omni.py appcore/runtime_omni_steps.py web/routes/omni_translate.py appcore/translate_profiles/omni_profile.py appcore/translate_profiles/av_sync_profile.py appcore/tts_strategies/sentence_reconcile.py appcore/tts_strategies/sentence_reconcile_v2.py pipeline/duration_reconcile.py pipeline/duration_reconcile_v2.py web/templates/omni_translate_list.html web/services/omni_pipeline_runner.py
```
Expected: **空输出**（线上 omni 文件零改动）。非空 = 违反隔离红线，必须回退那些改动。
- [ ] **Step 3: 路由门禁**（起 dev server 或 test client）：`/omni-translate-0615` 未登录 302、登录有权限 200。
- [ ] **Step 4: 相关回归** `python3 scripts/pytest_related.py --base origin/master --run`（线上 omni 既有测试应仍全绿——因为它们的文件没动）。
- [ ] **Step 5: Commit** `git commit -am "test(0615): smoke + isolation verification"`，push `feature/omni-translate-0615`，停下等人工验收（用同一测试视频对比 0615 与 V1 行为等价）。

---

## 自检清单（执行者完成后逐条核对）
- [ ] Spec 验收标准 1（线上零改动）= Task9 Step2 空输出
- [ ] 验收标准 2（行为等价）= 人工同视频对比
- [ ] 验收标准 3（门禁）= Task9 Step3
- [ ] 验收标准 4（数据隔离）= 0615 任务 project_type=omni_translate_0615，不入 V1 列表
- [ ] 验收标准 5（测试）= Task9 Step1/4
- [ ] 改名映射无遗漏 = 各 Task 的 `python3 -c import` 全过
- [ ] preset 兼容 = plugin_config 取值（av_sentence/sentence_reconcile/five_round_rewrite）未改，靠直接 import 派发
