# 广告预警 AD 级详情 + Gemini 评估 — Codex 执行指引

## 基本信息

| 项目 | 值 |
|------|-----|
| 仓库 | `git@github.com:jinghuaswsx/AutoVideoSrtLocal.git` |
| 分支 | `feature/ad-warning-module`（⚠️ 和第一期在同一个分支） |
| 基线 | `master` |

## 操作步骤

### Step 1: 拉取最新代码

```bash
git checkout master && git pull
git checkout -b feature/ad-warning-module origin/feature/ad-warning-module
```
或者如果已经在该分支：
```bash
git pull origin feature/ad-warning-module
```

### Step 2: 读设计文档和执行计划

```bash
cat docs/superpowers/specs/2026-06-12-ad-alert-ad-level-design.md
cat docs/superpowers/plans/2026-06-12-ad-alert-ad-level-plan.md
```

### Step 3: 按 Task 顺序执行

| Task | 文件 | 说明 |
|------|------|------|
| 1 | `appcore/ad_alerts.py` + `appcore/llm_use_cases.py` | 新增 AdListItem/AdEvaluation 数据模型、get_ad_list()、evaluate_ads()、注册 use case |
| 2 | `web/routes/ad_alerts.py` | 新增 /api/ad-list 和 /api/evaluate 两个路由 |
| 3 | `web/templates/ad_alerts.html` | 详情弹窗新增 AD 表格和 AI 评估按钮+结果 |

### Step 4: 推送

```bash
git push origin feature/ad-warning-module
```

## 注意事项
- 沿用第一期同一个分支 `feature/ad-warning-module`，不要开新分支
- LLM use case 注册在 `appcore/llm_use_cases.py`，对照已有 use case 格式添加 `ad_alert.evaluate`
- Gemini 走 OpenRouter 的 `google/gemini-3.5-flash`
- evaluate_ads() 只评估 ROAS < threshold 的亏损 AD
- 不新增 CSS，复用现有 `.oc-*` 类
- 不自动调用 AI 评估，用户点击按钮才触发
- 执行计划中同个文件的多次修改在同一 Task 内一次性完成
