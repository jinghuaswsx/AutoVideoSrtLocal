# Codex 接手执行指令：图片翻译串/并行模式

## 当前状态速览

- **Worktree（工作目录）**: `G:/Code/AutoVideoSrtLocal-image-translate-concurrency`
- **分支**: `feature/image-translate-concurrency`
- **需求文档 (spec)**: `docs/superpowers/specs/2026-04-22-image-translate-concurrency-mode-design.md`
- **实现计划 (plan)**: `docs/superpowers/plans/2026-04-22-image-translate-concurrency-mode.md`
- **进度**：9 个 Task，Task 1 **代码已写好未 commit**，Task 2-9 未动

### Task 1 现状（已改好未 commit）

两个文件已按 plan 里 Task 1 的 Step 1 + Step 3 修改到位：
- `appcore/task_state.py`：`create_image_translate(...)` 加了 `concurrency_mode="sequential"` kwarg，并在 state dict 里新增 `"concurrency_mode": ...` 字段（带枚举兜底）
- `tests/test_image_translate_runtime.py`：末尾追加了 `test_create_image_translate_stores_concurrency_mode`

**Codex 首先要做的事**：验证 Task 1 已写内容正确，跑测试，然后 commit。具体见下方【第 0 步】。

---

## 给 Codex 的完整指令

你将在 git worktree `G:/Code/AutoVideoSrtLocal-image-translate-concurrency` 内的 `feature/image-translate-concurrency` 分支上执行一个功能实现计划。**不要切到主 worktree `G:/Code/AutoVideoSrtLocal`**，那里有另一个分支的合并冲突，与本任务无关。

### 总原则

1. **严格按 plan 执行**：`docs/superpowers/plans/2026-04-22-image-translate-concurrency-mode.md` 里每个 Task 都有完整代码和命令。只要按 checkbox 逐项执行即可，不要自己发挥。
2. **TDD 节奏**：每个 Task 都是"先写失败测试 → 跑测试确认 FAIL → 写最小实现 → 跑测试确认 PASS → 跑回归 → commit"。**不要跳步**。
3. **每个 Task 独立 commit**：commit 信息用 plan 里每个 Step 6 给出的那条（中文，`feat/refactor(image_translate): xxx` 前缀）。
4. **任务之间不要累积改动**：Task N 测试不通过就停在 Task N，不要继续 Task N+1。
5. **沟通语言**：简体中文。

### 运行环境

- Windows 11 + bash（非 PowerShell）
- Python 3.14（仓库根目录直接 `python -m pytest`）
- pytest 配置：`pytest.ini` 里已配好
- 不需要任何额外依赖安装

### 执行流程

#### 第 0 步：验证并完成 Task 1（代码已就绪，只需验证 + commit）

```bash
cd "G:/Code/AutoVideoSrtLocal-image-translate-concurrency"

# 查看改动
git diff --stat
# 预期输出：
#  appcore/task_state.py                 |  4 +++-
#  tests/test_image_translate_runtime.py | 31 +++++++++++++++++++++++++++++++

# 跑 Task 1 新测试 + 现有 image_translate 测试确认零回归
python -m pytest tests/test_image_translate_runtime.py tests/test_image_translate_routes.py -x --tb=short
# 预期：全 PASS（含新加的 test_create_image_translate_stores_concurrency_mode）

# 若通过，commit
git add appcore/task_state.py tests/test_image_translate_runtime.py
git commit -m "feat(image_translate): task_state 加 concurrency_mode 字段"
```

**若测试失败**：先 `git diff` 看清具体改动，按 plan Task 1 Step 3 的代码核对，不对的地方改回去。

**若测试通过**：Task 1 ✅，进入第 1 步。

#### 第 1 步 ~ 第 8 步：依次执行 plan 里的 Task 2 ~ Task 9

打开 `docs/superpowers/plans/2026-04-22-image-translate-concurrency-mode.md`，从 **Task 2** 开始，按每个 Task 的 6 个 Step 依次执行：

- **Task 2**：API `/api/image-translate/upload/complete` 接受并校验 `concurrency_mode`
- **Task 3**：API `/medias/api/products/<pid>/detail-images/translate-from-en` 接受并校验 `concurrency_mode`
- **Task 4**：Runtime 重构（拆 `_run_sequential` + 加 `_state_lock`，零行为变更）
- **Task 5**：Runtime 实现 `_run_parallel`（`ThreadPoolExecutor(max_workers=10)` 分批）
- **Task 6**：Runtime 并发安全测试 + 必要时加锁
- **Task 7**：UI — 图片翻译菜单 pill
- **Task 8**：UI — 素材编辑 modal 配置态
- **Task 9**：全量 pytest + 手测 + push

每个 Task 完成后先跑 plan 里 Step 5 的回归测试，全部 PASS 再 commit 下一个 Task。

#### 关键注意事项

1. **字段 `concurrency_mode` 的合法值只有** `"sequential"` 和 `"parallel"`；非法值 API 层返 400，state 存 dict 层兜底为 `"sequential"`。
2. **`_BATCH_SIZE = 10`** 是模块级常量，写死不做可配置。
3. **`_state_lock` 必须保护**：`_update_progress` + `store.update` + `self._rate_limit_hits` 操作（见 plan Task 4/5/6）。
4. **串行路径行为必须 100% 不变**。Task 4 完成后所有现有测试应继续 PASS。
5. **重试接口不加参数**。runtime 读任务现有 `concurrency_mode` 即可。
6. **UI 约束**：图片翻译页用 `.it-pill`（已有样式），素材编辑 modal 用 `.oc-chip` + `.on`（已有样式）。不要引入新 CSS 类。

#### 卡住怎么办

- 测试失败、命令 hang、不知道怎么改 → **停下**，回报给用户："Task N Step X 卡在 Y，错误：Z"。不要在 plan 外自行决策。
- 如果某个 Task 的代码和 plan 不完全匹配，以 plan 为准。plan 里每一段 Python/HTML/JS 都是**完整可直接粘贴**的。

#### 完成后

Task 9 完成后：

```bash
git push -u origin feature/image-translate-concurrency
```

然后告诉用户："9 个 Task 全部完成，已推到 origin/feature/image-translate-concurrency，待 review/合并"。

---

## 三份核心文档路径（给用户看）

| 文档 | 路径（相对 worktree 根） |
|------|------------------------|
| 需求文档 (Spec) | `docs/superpowers/specs/2026-04-22-image-translate-concurrency-mode-design.md` |
| 实现计划 (Plan) | `docs/superpowers/plans/2026-04-22-image-translate-concurrency-mode.md` |
| Codex 指令（本文件） | `docs/superpowers/handoff/2026-04-22-codex-handoff.md` |

Worktree 根：`G:/Code/AutoVideoSrtLocal-image-translate-concurrency`
