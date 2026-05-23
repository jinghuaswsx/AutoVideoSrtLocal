# AutoVideoSrtLocal — Gemini (Antigravity) Guide

主指南在 [AGENTS.md](AGENTS.md)，本文件放 Antigravity (Gemini) 专属补丁。Antigravity 会自动合并 AGENTS.md 并入 context。

## 协作约定
- **核心沟通**：与用户沟通一律使用**中文**。
- **实测与测试环境验收**：所有功能改动和验收必须以测试环境实测为准。严禁在线上环境测试或产生垃圾数据。必须使用 `testuser.md` 的测试账号密码，在 `http://172.16.254.106:8080/` 测试环境通过 CDP 或浏览器实际操作、点击，复现逻辑状态，以此实测为唯一的最终验收标准。
- **默认远程**：`https://github.com/jinghuaswsx/AutoVideoSrtLocal.git`。
- **冲突优先级**：用户当条指令 > AGENTS.md > 全局 > 系统默认。

## Antigravity 专属与规划模式 (Planning Mode)
- **规划与文档驱动**：涉及重大修改或非 trivial 需求，先利用 `research` 子代理/工具进行调研，并在 artifacts 目录下编写 `implementation_plan.md` 标记 `request_feedback=true` 申请用户确认。通过后使用 `task.md` 推进，任务完成后提供 `walkthrough.md` 汇报。
- **隔离开发与 Worktree**：常规需求和重构一律通过 `git worktree add` 进行隔离开发，切勿污染 `master`（除非明确的 master hotfix）。
- **充分利用 Plugin 与子代理**：
  - 网页调试与自动化：使用 `chrome-devtools-plugin`
  - 现代 Web 设计自检：使用 `modern-web-guidance-plugin`
  - 繁重调研任务：delegate 到 `research` 子代理后台并发执行。
- **非阻塞命令与 Reactive Wakeup**：启动耗时测试/服务时，合理设置 `WaitMsBeforeAsync` 让任务在后台运行。善用 `schedule` 注册定时提醒（Timer），让系统通过消息队列主动唤醒，杜绝轮询 `manage_task status`。
- **发布与防呆**：禁止调用 `deploy/publish.sh`，直接遵循 [AGENTS.md](AGENTS.md) 的 remote SSH 部署命令。

## 任务结束自检
1. AGENTS.md / GEMINI.md 行数各自 ≤ 80 行。
2. 数据分析与 SKU ROAS 快照是否满足业务日对齐要求。
3. timelines / detail_extra 结构是否符合 Jinja2 继承与 Ocean Blue 零紫色规范。
