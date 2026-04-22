# Web Service Tuning Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把当前 Web 服务从 `eventlet worker` 切到单 worker `gthread`，并统一后台任务启动入口，提升与现有代码模型的一致性和新服务器上的承载能力。

**Architecture:** 保持单 Gunicorn worker，不做跨进程扩容；通过 `gthread + 32 threads` 提升同进程内并发，并把残留的 `eventlet.spawn(...)` 收敛到 `socketio.start_background_task(...)`。部署参数集中到独立 Gunicorn 配置文件，systemd 负责环境变量和重启策略。

**Tech Stack:** Python, Flask, Flask-SocketIO, Gunicorn, systemd, pytest

---

### Task 1: 补后台任务启动抽象

**Files:**
- Create: `web/background.py`
- Modify: `web/routes/bulk_translate.py`
- Modify: `web/routes/copywriting.py`
- Modify: `web/routes/copywriting_translate.py`
- Modify: `web/routes/video_creation.py`
- Modify: `web/routes/video_review.py`
- Test: `tests/test_web_background.py`

- [ ] **Step 1: 先写失败测试，固定统一后台任务入口行为**

- [ ] **Step 2: 运行 `pytest tests/test_web_background.py -q`，确认因为 helper 尚不存在而失败**

- [ ] **Step 3: 新增 `web/background.py`，包装 `socketio.start_background_task(...)`**

- [ ] **Step 4: 把五个路由文件中的 `eventlet.spawn(...)` 全部切到统一 helper**

- [ ] **Step 5: 运行 `pytest tests/test_web_background.py tests/test_bulk_translate_routes.py tests/test_copywriting_translate_routes.py tests/test_web_routes.py -q`**

### Task 2: 调整 Gunicorn 与 systemd 配置

**Files:**
- Create: `deploy/gunicorn.conf.py`
- Modify: `deploy/autovideosrt.service`
- Modify: `deploy/setup.sh`
- Modify: `requirements.txt`
- Test: `tests/test_project_docs.py`

- [ ] **Step 1: 先写配置测试 / 文本断言，固定 `gthread`、`threads=32`、`workers=1` 的目标配置**

- [ ] **Step 2: 运行对应 pytest 用例，确认现有 `eventlet` 配置导致失败**

- [ ] **Step 3: 新增 Gunicorn 配置文件，并让 systemd 改为加载该配置**

- [ ] **Step 4: 在 service 里补充 `PYTHONUNBUFFERED` 与本地计算库线程限制环境变量**

- [ ] **Step 5: 更新 `deploy/setup.sh` 和 `requirements.txt`，确保部署环境具备 `simple-websocket` 且不再依赖 Eventlet worker**

- [ ] **Step 6: 运行配置相关测试，确认通过**

### Task 3: 聚焦验证与交付说明

**Files:**
- Modify: `README.md` or deployment notes if needed
- Verify: `deploy/autovideosrt.service`
- Verify: `deploy/gunicorn.conf.py`
- Verify: focused pytest suites

- [ ] **Step 1: 运行聚焦验证命令，确认 helper、路由和部署配置都通过**

- [ ] **Step 2: 检查变更文件 diff，确认没有残留 `eventlet.spawn(...)`**

- [ ] **Step 3: 输出服务器推荐参数与部署后验证命令**
