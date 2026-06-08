# AutoVideoSrtLocal — Claude Code

主指南在 [AGENTS.md](AGENTS.md)，本文件只放 Claude Code 专属补丁。Claude Code 会自动把 AGENTS.md 内容并入 context。

## 协作约定
- 与用户沟通一律使用中文。
- 默认远程：`https://github.com/jinghuaswsx/AutoVideoSrtLocal.git`；旧服务器版 `AutoVideoSrt.git` 仅作迁移参考。
- 全局规则见 `~/.claude/CLAUDE.md`（文档驱动代码 / 分支隔离 / Wine SOP）；冲突时优先级：用户当条指令 > AGENTS.md > 全局 > 系统默认。

## Claude Code 专属
- **优先用 Skill 系统**：`superpowers:*`、`claude-api`、`webapp-testing`、`frontend-design`、`mcp-builder`；改代码前若适用必须先 `Skill` 调用。
- **严禁调用 `deploy/publish.sh`**：本机自主闭环已替代它（见 AGENTS.md「发布」节）。
- **改代码前看 worktree 路径**：当前 `pwd` 不在 `~/.paseo/worktrees/...` 下且非用户明确 hotfix → 先 `git worktree add`。
- **任务流转 UI 必须闭环**：触发动作后留在上下文内显示 loading、成功 ID/下一步入口、失败接口与错误原因。

## AutoVideoSrtLocal pytest 最小化规则（强制）
- 默认不跑全量 `pytest -q`；按仓库 `docs/superpowers/specs/2026-06-08-targeted-pytest-verification.md` 选择改动相关测试。
- 首选 `python3 scripts/pytest_related.py --base origin/master --run`；Windows 若只有 `python` 则用 `python scripts/pytest_related.py --base origin/master --run`。
- 没有脚本时人工列 `pytest <相关 files> -q`。
- 脚本无目标时说明“无直接 pytest 覆盖”，改跑最小必要非 pytest 验证，不得自动退回全量。
- 只有发布/合并/用户明确要求、pytest 配置/fixture/依赖变更、跨模块重构，或 schema/auth/deploy/scheduler/LLM/storage/billing 等广影响改动时跑全量。
- 最终汇报必须说明全量是否跳过、理由，以及实际运行的 focused tests 或替代验证。

## 模块级 CLAUDE.md（只在进入对应目录时加载）
- `web/templates/CLAUDE.md` — Jinja 模板继承防呆 + asr-normalize-card 事故
- `web/static/CLAUDE.md` — Ocean Blue 设计系统 + CSRF + medias.js 弹窗约束
- `tools/shopify_image_localizer/CLAUDE.md` — EZ/CDP 等待规则 + 登录按钮 + EXE 发布/API key/BOM 配置门禁
- `appcore/order_analytics/CLAUDE.md` — 实时大盘业务日对齐 + 店铺筛选 + 广告费分摊

## 任务结束自检
1. AGENTS.md / CLAUDE.md 行数仍 ≤ 80
2. 涉及的模块级 `CLAUDE.md` 是否需要更新事故记录
3. 新增 spec 是否在 AGENTS.md「主题指引」加了引用
