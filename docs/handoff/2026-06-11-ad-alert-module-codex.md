# 广告预警模块 — Codex 执行指引

> **Codex 你好！** 这是一份给另一台机器的 Codex 看的执行指引。你和我不在同一台机器上，所以所有操作都通过 GitHub 完成。

---

## 一、基本信息

| 项目 | 值 |
|------|-----|
| 仓库 | `git@github.com:jinghuaswsx/AutoVideoSrtLocal.git` |
| 分支 | `feature/ad-warning-module` |
| 远程 | `origin` |
| 基线 | `master` |

## 二、相关文档（已在该分支上）

| 文档 | 路径 | 说明 |
|------|------|------|
| 设计文档 | `docs/superpowers/specs/2026-06-11-ad-alert-module-design.md` | 完整设计方案，包括数据架构、规则引擎、前端UI |
| 执行计划 | `docs/superpowers/plans/2026-06-11-ad-alert-module.md` | 逐 Task 的执行步骤，含完整代码 |
| 本指引 | `docs/handoff/2026-06-11-ad-alert-module-codex.md` | （就是你现在看的这个） |

## 三、Codex 操作步骤

### Step 1: 拉取代码

```bash
cd <你的工作目录>
git clone git@github.com:jinghuaswsx/AutoVideoSrtLocal.git  # 如果还没 clone
# 或
git checkout master && git pull
git checkout -b feature/ad-warning-module origin/feature/ad-warning-module
```

### Step 2: 阅读设计文档

```bash
# 先通读设计文档，理解整体架构
cat docs/superpowers/specs/2026-06-11-ad-alert-module-design.md
```

设计文档能帮你了解：
- 数据从哪里来（`media_product_lang_ad_summary_cache` 缓存表）
- 规则引擎逻辑（4 种研判结论）
- 前端 UI 布局
- 为什么不做 LLM、不用图表库、不新增表

### Step 3: 阅读执行计划

```bash
cat docs/superpowers/plans/2026-06-11-ad-alert-module.md
```

执行计划有 6 个 Task，**按顺序执行**：

| Task | 文件 | 内容 | 预估耗时 |
|------|------|------|---------|
| 1 | `appcore/ad_alerts.py` | 核心逻辑（阈值、查询、规则引擎、趋势） | 15min |
| 2 | `web/routes/ad_alerts.py` | Flask 路由 | 10min |
| 3 | `web/app.py` | 注册蓝图（改 2 处） | 3min |
| 4 | `web/templates/ad_alerts.html` | 前端页面模板（含 SVG 趋势图、JS） | 20min |
| 5 | `web/templates/layout.html` | 侧栏菜单入口（改 1 处） | 3min |
| 6 | `web/routes/admin.py` + `admin_settings.html` | 后台阈值配置 | 5min |

### Step 4: 按 Task 顺序执行

每个 Task 的代码块已经是**可直接粘贴的完整代码**。按 Task 编号依次执行：

1. **Task 1**: 创建 `appcore/ad_alerts.py`，写入所有类、数据模型、规则引擎
2. **Task 2**: 创建 `web/routes/ad_alerts.py`，写入所有路由
3. **Task 3**: 修改 `web/app.py`（导入 + 注册 Blueprint）
4. **Task 4**: 创建 `web/templates/ad_alerts.html`（完整前端模板）
5. **Task 5**: 修改 `web/templates/layout.html`（侧栏菜单）
6. **Task 6**: 修改 `web/routes/admin.py` 和 `web/templates/admin_settings.html`

每个 Task 内都有 `git commit` 命令，执行完该 Task 后提交一次。

### Step 5: 验证

启动服务后打开 `/ad-alerts`：

```bash
# 启动开发服务器
python main.py  # 或项目使用的启动方式
```

验证清单：
- [ ] 侧栏出现"🔔 广告预警"菜单项
- [ ] 点击后进入预警列表页
- [ ] 页面自动加载 API 数据
- [ ] 筛选按钮（全部/严重/中度/轻度）正常切换
- [ ] 搜索框可按商品名/编码搜索
- [ ] 点击卡片弹出详情弹窗
- [ ] 详情弹窗内包含 SVG 趋势折线图
- [ ] 可修改阈值（点击阈值数字旁的 ✎ 按钮）
- [ ] 管理员后台设置页也有阈值输入框

### Step 6: 提交和推送

```bash
git push origin feature/ad-warning-module
```

### 可选：合并到 master

如果你确认所有代码正常工作，可以创建 PR 合并到 master：

```bash
# 方案A：命令行创建 PR（如果安装了 gh）
gh pr create --base master --head feature/ad-warning-module \
  --title "feat: 广告预警模块" \
  --body "新增广告预警模块，详见设计文档 docs/superpowers/specs/2026-06-11-ad-alert-module-design.md"

# 方案B：GitHub Web 访问
# https://github.com/jinghuaswsx/AutoVideoSrtLocal/pull/new/feature/ad-warning-module
```

---

## 四、注意事项

1. **不新增数据库表** — 所有数据从现有 `media_product_lang_ad_summary_cache` 和 `meta_ad_daily_ad_metrics` 查
2. **不引入 JS 图表库** — 趋势图是内联 SVG，在 Jinja2 模板中生成的
3. **不用 LLM** — 研判结论走规则引擎（`judge_alert()` 函数）
4. **限管理员** — 所有路由加了 `@login_required @admin_required`
5. **CSRF** — 前端 AJAX POST 请求需要带 `X-CSRFToken`，模板已有 `getCsrfToken()` 函数
6. **Ocean Blue 设计系统** — CSS class 名用 `oc-*` 前缀，hue 范围 200-240，无紫色
7. **Python 版本** — 用 `from __future__ import annotations`；类型标注用 `list[dict]` 而非 `List[dict]`
8. 如遇 `register_blueprint` 问题，确认在 `web/app.py` 中导入并注册的顺序跟其他蓝图一致

## 五、如果你需要联系我

完成实现后 push 到 `feature/ad-warning-module` 即可。如果有需要讨论的设计问题，记录在 issue 中或直接修改设计文档后提交。
