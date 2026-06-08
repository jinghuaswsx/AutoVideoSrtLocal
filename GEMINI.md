# AutoVideoSrtLocal - Gemini (Antigravity) Guide

主指南在 [AGENTS.md](AGENTS.md)，全局 Antigravity 原则在 `C:\Users\admin\.gemini\antigravity\GEMINI.md`（按全局配置）。本文件是本项目的 Antigravity 开发规则与流程规范，必须严格遵守。

## 1. 先守项目红线

- **默认中文沟通**。
- **就近规则优先**：先读最近层级的 [AGENTS.md](AGENTS.md) / [CLAUDE.md](CLAUDE.md) / [GEMINI.md](GEMINI.md)。
- **隔离开发**：除非用户当条消息明确说“马上 hotfix / 直接在主工作目录 hotfix”，否则绝对不要在主工作目录改代码。
- **Worktree 规范**：非 hotfix 一律先建 worktree，路径为 `G:\Code\AutoVideoSrtLocal\.worktrees\<branch>`。创建前确认 `.worktrees/` 已被 `.gitignore` 忽略。
- **禁止连接本地 MySQL**：永远不要连接 Windows 本机 MySQL（禁止 `127.0.0.1:3306`）。数据库验证只能走测试服务器或线上服务器。
- **服务重启**：用户没说“发测试 / 上线”，不要重启 systemd 服务。
- **发布规范**：不调用 `deploy/publish.sh`，发布按项目文档的 ssh + git pull + systemctl 流程做。
- **保护用户代码**：不碰无关改动，不回退用户已有修改。

## 2. 先判定任务类型

- **只读排查 / 导出**：不改代码，先查真实服务器状态，按用户指定路径产出文件，并验证文件存在。
- **明确 hotfix**：只修已有 bug，小范围、快验证，尽快收口。
- **普通需求 / 优化 / 重构 / 跨模块改动**：必须 worktree + 新分支。
- **非 hotfix 判定**：边界不清、可能超过 30 分钟、预计改 3 个以上文件时，默认不是 hotfix。

## 3. 改代码前必须先搞清楚真相

- 先执行 `git status`，确认当前工作区状态。
- **结构分析**：结构问题优先用 CodeGraph 寻找定义、调用链、影响面。
- **文本搜索**：文本搜索使用 `rg`。
- **定位根因**：不要凭感觉修，先复现、定位链路、找根因。
- **数据与产物证据**：数据问题以服务器和真实任务产物为准，不用本地 DB 兜底。音频、视频、导出、任务中心这类问题，最终证据必须来自真实产物、任务状态、接口返回或日志，而不是“测试过应该行”。

## 4. 实现原则

- **风格一致**：尽量小补丁，沿用现有代码风格和服务层，不新造体系。
- **文档锚点**：先找文档锚点（如 [AGENTS.md](AGENTS.md)、模块 [CLAUDE.md](CLAUDE.md)、`docs/superpowers/specs/`）。没有锚点时，先补或确认设计，不要直接乱改。
- **LLM 调用**：涉及 LLM 必须走 `appcore.llm_client` 和既有 use case 体系。
- **Flask 路由**：涉及 Flask 新路由必须加 `@login_required`，需要后台权限的加 `@admin_required`。前端 POST 必须带 `X-CSRFToken`（从 `layout.html` meta 读）。
- **清理与重置**：涉及任务重启 / resume，要清理旧产物和旧字段，避免 stale artifact 继续污染结果。
- **等待逻辑**：不把 Playwright 的等待逻辑粗暴换成 `time.sleep`。
- **PowerShell 兼容**：Windows PowerShell 不用 bash 写法，尤其不要用 `&&`。

## 5. 测试与验证顺序

每次改动后至少做到：
1. **跑相关 pytest**：按 `docs/superpowers/specs/2026-06-08-targeted-pytest-verification.md` 选择必要测试；非发布/合并/广影响门禁不跑全量。
2. **起本地 dev server**：用空闲端口（默认 5000，本地起空闲端口避免撞生产）。
3. **未登录重定向**：未登录访问敏感路由应是 302，不能 500。
4. **登录后响应**：登录后核心页面 / 新接口应返回 200。
5. **真实产物验证**：涉及真实任务、音频、视频、产品链接、数据采集时，要验证真实服务器状态和产物。
6. **429 容错**：不能把 429 当成链接存在/不存在结论；要缩小范围、慢速重试或换可靠来源。
7. **最终汇报**：必须列清楚改了什么、验证了什么、产物在哪里、还有什么风险。

## 6. 发布原则

只有用户明确要求“提交 / 合并 / 发测试 / 上线”时才发布。发布路径必须完整：
- 本地验证通过。
- commit。
- merge / rebase 到目标分支。
- push。
- ssh 到 `172.16.254.106`。
- **测试环境**：pull + restart + `systemctl is-active` + HTTP 验证。
- **线上环境**：pull + service 文件对比 + restart + `systemctl is-active` + HTTP 验证。
- 看到 active + HTTP 200/302 才能说发布完成。

## 7. 最重要的工作态度

**不要“看起来完成”，要“真实可用”。**
本项目很多问题不是代码表面问题，而是服务器状态、旧产物、任务恢复、权限链路、真实商品链接、真实音视频产物的问题。开发时必须先查证据，再下判断；先锁根因，再写补丁；最后用运行时结果证明修复成立。
