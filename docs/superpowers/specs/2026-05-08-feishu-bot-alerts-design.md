# 飞书应用机器人告警设计

最后更新：2026-05-08

## 文档锚点

- `AGENTS.md#定时任务归集规则`：定时任务和运行日志必须集中到 Web 后台“定时任务”模块。
- `docs/server_browser_runtime.md#CDP 连接恢复`：定时任务失败应写入 `scheduled_task_runs`，后台 admin 通过失败告警看到原因。
- `docs/project-audit-2026-05-01.md#11-定时任务登记已集中但控制能力不完全一致`：统一日志和告警能力是当前运维改进方向。

## 背景

现有系统已经把定时任务失败记录写入 `scheduled_task_runs`，并在 Web 后台为超级管理员展示最近失败提示。但该提示只在管理员打开后台时可见，无法主动通知到外部协作群。需要新增飞书机器人通知，把系统异常第一时间推送到指定飞书群。

用户确认第一版范围仅覆盖 `scheduled_task_runs` 失败记录：任务失败后自动向飞书群发送报警信息。第一版不覆盖业务页面内的普通表单错误、前端 `alert()`、用户手动操作失败提示、日志文件扫描或系统 journal 扫描。

## 接入方式

采用飞书自建应用机器人，不使用自定义机器人 webhook。

发送流程：

1. 系统读取 `system_settings` 中保存的飞书应用配置。
2. 用 `app_id` 和 `app_secret` 调用飞书开放平台获取 `tenant_access_token`。
3. 用 `tenant_access_token` 调用飞书 IM 发消息接口，`receive_id_type=chat_id`，把告警发到配置的群聊 `chat_id`。
4. 发送失败只写 Python log，不影响 `scheduled_task_runs` 原始失败记录写入。

官方接口参考：

- `https://open.feishu.cn/document/server-docs/authentication-management/access-token/tenant_access_token_internal`
- `https://open.feishu.cn/document/server-docs/im-v1/message/create`

说明：飞书“长连接”主要用于接收事件。当前需求是系统主动发送告警，不需要常驻 WebSocket 监听；后续如果要让飞书群消息触发系统操作，再单独设计长连接事件接收进程。

## 配置

配置存入 `system_settings`，避免写入源码、文档、测试或 commit：

| key | 含义 |
| --- | --- |
| `feishu_alerts.enabled` | 是否启用飞书告警，`1` 启用，其他值停用 |
| `feishu_alerts.app_id` | 飞书自建应用 App ID |
| `feishu_alerts.app_secret` | 飞书自建应用 App Secret |
| `feishu_alerts.chat_id` | 告警接收群聊 ID |

设置页新增“飞书告警”配置块，遵循现有敏感凭据规则：

- App Secret 输入框不回显真实值。
- 留空保存表示保留原值。
- 勾选清空才删除 Secret。
- 页面只显示“已配置/未配置”和末四位掩码。

## 告警触发点

触发点放在 `appcore.scheduled_tasks.finish_run()` 中：

- 仅当 `status == "failed"` 时考虑触发。
- 触发时读取当前 run 行信息，补齐 `task_code`、`task_name`、`started_at`、`finished_at`、`duration_seconds`、`error_message`、`summary`、`output_file`。
- 不在 `start_run()` 或 `record_failure()` 里重复触发；`record_failure()` 通过 `finish_run()` 统一发送。
- 发送函数必须捕获自身异常，避免告警模块故障影响任务记录。

### 连续失败阈值（2026-05-09 起）

为避免单次瞬时失败（浏览器 timeout、临时锁竞争、auth 重试一轮就过）刷屏飞书群，告警在 `_dispatch_failure_alert` 里加了一道「连续 2 次同 task 失败才发」的门：

- `appcore.scheduled_tasks._consecutive_failure_streak(task_code, including_run_id=...)` 倒着数同 `task_code` 的 `scheduled_task_runs.status`，遇到非 `failed` 立刻停。
- streak < `CONSECUTIVE_FAILURE_ALERT_THRESHOLD`（默认 2）：跳过 Feishu，仅留 DB 失败记录与后台「定时任务的运行日志」红色提示。
- streak >= 阈值：照常发飞书；`row["consecutive_failures"] = streak` 注入消息体，新增 `连续失败：N 次` 一行。

设计依据 / 触发场景：[`docs/superpowers/specs/2026-05-09-meta-daily-final-permission-recovery.md`](2026-05-09-meta-daily-final-permission-recovery.md)。

## 消息内容

第一版发送文本消息，便于稳定落地：

```text
【AutoVideoSrt 告警】定时任务失败
任务：<task_name> (<task_code>)
连续失败：<N> 次               # 仅 streak >= 2 时出现，2026-05-09 起
运行ID：<run_id>
开始：<started_at>
结束：<finished_at>
耗时：<duration_seconds>s
错误：<error_message>
查看：/scheduled-tasks?view=logs&task=<task_code>
```

格式原则：

- 错误信息限制长度，避免超长堆栈刷屏。
- `summary` 仅在简短时追加；过长时省略。
- 不包含 App Secret、token、Cookie、Authorization 等敏感值。
- 后台详情链接使用相对路径，避免在未知部署域名时拼错绝对 URL。

## 测试通知

新增 CLI 入口用于验收：

```bash
python3 -m tools.send_feishu_test_alert
```

行为：

- 读取同一份 `system_settings` 配置。
- 发送一条测试消息到配置的 `chat_id`。
- 发送成功输出 JSON：`{"ok": true, "message_id": "..."}`
- 配置缺失或接口失败时退出非 0，并输出不含密钥的错误信息。

## 错误处理

- 配置未启用：跳过发送。
- 配置缺失：跳过自动告警；测试 CLI 返回明确错误。
- token 接口失败：记录 warning，自动告警不抛出。
- 发消息接口失败：记录 warning，自动告警不抛出。
- 飞书返回非 0 code：作为失败处理，错误消息截断后进入 log 或 CLI 输出。

## 验证计划

聚焦测试：

- `tests/test_feishu_alerts.py`
- `tests/test_appcore_scheduled_tasks.py`
- `tests/test_settings_routes_new.py`
- `tests/test_scheduled_tasks_ui.py`

手工验收：

1. 在测试环境配置飞书应用机器人参数和接收群 `chat_id`。
2. 运行 `python3 -m tools.send_feishu_test_alert`。
3. 确认目标群收到测试通知。
4. 用测试替身验证 `finish_run(status="failed")` 会调用告警发送，且发送异常不影响 run 更新。

## 非目标

- 不新增飞书长连接事件接收进程。
- 不处理飞书群消息命令。
- 不做多群路由、告警级别订阅、静默时段。
- ~~不做去重聚合~~：2026-05-09 起按「连续 2 次同 task 失败」门控，详见上方「连续失败阈值」段。
- 不扫描系统 journal、Nginx log、Python log 文件。
- 不把飞书凭据写入 `.env`、源码常量、文档、测试 fixture 或日志。

## 实现记录

2026-05-08 已完成第一版接入：

- 新增 `appcore.feishu_alerts`，使用飞书自建应用机器人获取 `tenant_access_token` 并向配置的 `chat_id` 发送 IM 文本消息。
- `appcore.scheduled_tasks.finish_run()` 在 `status="failed"` 且失败记录成功写入后触发飞书告警；告警发送异常只记录 warning，不影响原始失败记录。
- `/settings?tab=feishu_alerts` 支持启用开关、App ID、App Secret 和 Chat ID 配置；App Secret 不回显，留空保存表示保留旧值。
- 新增 `python3 -m tools.send_feishu_test_alert` 测试通知 CLI。
- 聚焦回归：`tests/test_feishu_alerts.py tests/test_appcore_scheduled_tasks.py tests/test_settings_routes_new.py tests/test_scheduled_tasks_ui.py` 通过，结果为 `62 passed`。
- 编译检查：`python3 -m compileall appcore/feishu_alerts.py appcore/scheduled_tasks.py web/routes/settings.py tools/send_feishu_test_alert.py` 通过。
- 格式检查：`git diff --check` 通过。
- 使用测试环境配置发送真实飞书测试通知成功，飞书返回 `message_id=om_x100b50e0a13c0c98c4f135cb1d02078`。
