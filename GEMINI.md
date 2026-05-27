# AutoVideoSrtLocal - Gemini (Antigravity) Guide

主指南在 [AGENTS.md](AGENTS.md)，全局 Antigravity 原则在 `C:\Users\admin\.gemini\GEMINI.md`。本文件只放当前项目的 Antigravity 补丁和从 Codex 工作流同步来的可公开开发原则。

## 协作约定
- 与用户沟通默认使用中文，除非用户明确要求其他语言。
- 冲突优先级：用户当条指令 > 当前目录就近 `AGENTS.md` / `CLAUDE.md` / `GEMINI.md` > 全局原则 > 工具默认。
- 默认远程：`https://github.com/jinghuaswsx/AutoVideoSrtLocal.git`。
- 先读项目文档与现有实现，再动手；遵循本项目既有架构、helper API、命名和验证方式。
- 保护用户已有改动，不使用 `git reset --hard`、`git checkout --` 等破坏性操作，遇到脏工作区先识别来源并绕开无关改动。

## Codex 开发原则同步
- 能用已安装 skill / plugin / MCP 时优先用，尤其是流程类能力：规划、worktree、调试、验证和发布前检查。
- 常规需求必须先创建 `git worktree add` 隔离目录并开分支；主工作目录只做查看、合并、发布和状态检查。
- 只有用户当条消息明确说“马上 hotfix / 立刻 hotfix / 直接在主工作目录 hotfix”时，才允许在主工作目录或 `master` 上做小补丁。
- 改代码前必须有文档锚点：本文件、[AGENTS.md](AGENTS.md)、spec 或模块级说明。无锚点时先补文档或询问。
- 结构性代码理解优先用 CodeGraph；字面量、日志、模板文本等搜索用 `rg`。
- 改动保持小而聚焦，不做无关重构；需要结构化数据时用结构化 API 或解析器，不用脆弱字符串拼接。

## 项目硬红线
- 禁止连接、启动、安装、修复或依赖 Windows 本机 MySQL，也就是不得访问 `127.0.0.1:3306`。数据库确认以测试服务器或线上服务器为准。
- 用户没有明确说“发测试 / 上线”时，不执行 `systemctl restart`，不触碰生产服务。
- 禁止调用 `deploy/publish.sh`；发布按 [AGENTS.md](AGENTS.md) 的 SSH 命令执行。
- DB 凭据走 `infra_credentials` 或后台 `/settings?tab=infrastructure`，不要只改 `.env`。
- APScheduler、systemd timer、crontab、后台轮询等定时任务，必须同步登记到 `appcore/scheduled_tasks.py` 和后台“定时任务”模块。

## Antigravity 专属
- 非 trivial 需求先在 Planning Mode 中调研并写 `implementation_plan.md`，需要用户确认时标记 `request_feedback=true`；通过后用 `task.md` 推进，完成后给 `walkthrough.md`。
- 网页调试、登录态验证、CDP 操作优先使用 Antigravity/Chrome DevTools 相关插件，不凭静态推断替代实测。
- 耗时命令或服务用非阻塞方式运行，并设置合理唤醒；不要靠反复手动轮询状态推进。

## 验证
1. 文档改动至少检查 diff、行数和链接是否合理。
2. 功能验收默认使用 `http://172.16.254.106:8080/` 测试环境和 [testuser.md](testuser.md) 账号，不在线上环境制造测试数据。
3. 代码改动跑相关 `pytest <files> -q`；如测试会访问本机 MySQL，必须停止并说明规则限制。
4. Web 改动按 [AGENTS.md](AGENTS.md) 验证：未登录 302，登录后 200，新路由有 `@login_required + @admin_required`，POST 带 `X-CSRFToken`。
