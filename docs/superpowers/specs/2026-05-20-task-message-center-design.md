# 任务消息中心设计

- **日期**：2026-05-20
- **上位**：[任务中心端到端流程补全设计](2026-05-16-task-center-e2e-flow-design.md)
- **范围**：每个登录用户的站内消息中心，首期只承载任务中心通知。

## 目标

在后台顶栏增加一个图标型“消息中心”。当任务中心有任务到达某个用户的待办范围时，给该用户生成未读消息；用户点击消息跳到对应任务详情；用户把消息点掉后，该用户自己的未读数量减少 1。

## 非目标

- 不做邮件、飞书、短信、浏览器推送。
- 不做实时 WebSocket 推送；首期用页面加载和短轮询刷新未读数。
- 不做全站通用通知规则引擎；消息表支持扩展，但触发点只接任务中心。

## 触发规则

1. 创建父任务后，父任务处于 `pending` 待认领状态，通知所有启用且拥有 `can_process_raw_video` 生效权限的用户。
2. 创建子任务后，子任务处于 `blocked`，只通知该子任务的 `assignee_id`。
3. 父任务审核通过后，子任务从 `blocked` 变为 `assigned`，再次通知对应子任务 `assignee_id`：任务已可处理。
4. 父任务被管理员打回到 `raw_in_progress` 时，通知父任务当前 `assignee_id`。
5. 子任务被管理员打回到 `assigned` 时，通知子任务当前 `assignee_id`。
6. 取消、完成、审核通过本身不生成待办消息，因为它们不要求被通知用户继续处理。

## 数据模型

新增 `user_notifications` 表：

```sql
CREATE TABLE user_notifications (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  user_id INT NOT NULL,
  source_type VARCHAR(32) NOT NULL,
  source_id INT NOT NULL,
  event_type VARCHAR(48) NOT NULL,
  title VARCHAR(120) NOT NULL,
  body VARCHAR(512) DEFAULT NULL,
  target_url VARCHAR(255) NOT NULL,
  read_at DATETIME DEFAULT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  KEY idx_user_read_created (user_id, read_at, created_at),
  KEY idx_source (source_type, source_id, event_type)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
```

同一任务同一事件可以给多个用户各写一条；同一用户点掉只更新自己的 `read_at`。

## 服务边界

- 新增 `appcore/user_notifications.py`，负责写入、列表、未读数、标已读。
- `appcore/tasks.py` 只在状态机事务成功路径里调用通知服务，通知写入与任务状态同事务提交。
- `web/routes/notifications.py` 提供当前用户自己的 API：
  - `GET /notifications/api/summary`
  - `GET /notifications/api/list`
  - `POST /notifications/api/<id>/read`
- 路由必须 `@login_required`；用户只能读写自己的通知。

## 顶栏交互

- `layout.html` 顶栏用户区域左侧增加铃铛图标按钮。
- 未读数用小角标展示，0 时隐藏。
- 点击图标打开轻量下拉层，显示最近通知、加载态、空态、错误态。
- 每条通知提供标题、简短正文、时间和关闭按钮。
- 点击通知主体：先标记已读，再跳转 `target_url`，例如 `/tasks/?task_id=123`。
- 点击关闭按钮：只标记已读并从列表移除，不跳转。

## 验证

1. no-DB 单元测试覆盖通知服务 SQL 参数、去重/收件人选择、标已读权限过滤。
2. no-DB 路由测试覆盖 summary/list/read 三个 API 均以当前用户为范围。
3. 模板测试覆盖顶栏有消息中心图标、角标和前端请求路径。
4. 相关测试不能连接 Windows 本机 MySQL；需要数据库验证时留到测试服务器环境。
