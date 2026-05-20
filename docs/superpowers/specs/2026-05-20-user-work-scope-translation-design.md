# 用户工作范围：翻译工作 设计文档

- **日期**：2026-05-20
- **状态**：spec 已确认，待实施
- **锚点来源**：用户确认“给用户账号指定工作范围，可以有多个工作范围，目前先确定一个翻译工作；加入素材库或者做小语种只展现翻译工作的；周干琴 / 顾倩 / 王舒溦 / 王健 / 蔡靖华 标记有翻译工作的范围”
- **关联文档**：
  - [2026-04-26-mk-import-design.md](2026-04-26-mk-import-design.md)
  - [2026-04-26-task-center-skeleton-design.md](2026-04-26-task-center-skeleton-design.md)
  - [2026-04-24-medias-owner-reassign-design.md](2026-04-24-medias-owner-reassign-design.md)

## 0. 一句话目标

给用户账号增加可扩展的“工作范围”标记，第一期只落地 `翻译工作`。明空选品页的“加入素材库”和“做小语种”两个指派弹窗只展示具备 `翻译工作` 范围的用户，并初始化 5 个指定员工拥有该范围。

## 1. 范围

### 1.1 本期做什么

1. 在用户权限体系中新增一个工作范围位：`work_scope_translation`，显示名为 `翻译工作`。
2. 超级管理员的“用户管理 → 权限配置”可以勾选 / 取消该范围，未来可继续增加多个 `work_scope_*` 范围。
3. 新增应用层 helper，返回可接收翻译工作的用户列表；列表必须同时满足：
   - `users.is_active = 1`
   - `permissions.can_translate = true`
   - `permissions.work_scope_translation = true`
4. 明空选品页面两个入口只使用该 helper：
   - `加入素材库` 的“指定翻译员”弹窗
   - `做小语种` 的“创建翻译任务”弹窗
5. 后端对同一约束做强校验，直接调用接口传入非翻译工作用户时返回 400 / 422，不依赖前端过滤。
6. migration 初始化以下 5 个用户的 `can_translate=true` 与 `work_scope_translation=true`：
   - 周干琴
   - 顾倩
   - 王舒溦
   - 王健
   - 蔡靖华

### 1.2 本期不做什么

- 不新增独立 `user_work_scopes` 表。当前只有一个范围，先复用 `users.permissions` JSON，避免过度设计。
- 不改变素材管理“负责人重新分配”的用户范围；该入口仍按既有设计展示所有活跃用户。
- 不改任务中心“我的任务 / 全部任务”的可见性逻辑。
- 不在本期做国家、店铺、语种维度的细分范围。

## 2. 现状与问题

当前明空选品页的两个指派弹窗通过 `/medias/api/users/active` 读取所有活跃用户。该接口源于素材管理负责人重分配设计，语义是“可成为项目负责人”，不是“可接翻译工作”。

已有 `appcore.users.list_translators()` 会按 `can_translate` 过滤，但 `can_translate` 只是任务能力位，仍不足以表达用户当前实际工作范围。截图里的下拉出现了不应参与本次翻译指派的人，因此需要在能力位之外新增工作范围位。

## 3. 数据模型

复用 `users.permissions` JSON，不新增表。

新增权限码：

| code | group | label | admin 默认 | user 默认 |
|---|---|---|---|---|
| `work_scope_translation` | `capability` | `翻译工作` | `false` | `false` |

角色默认值：
- `superadmin`：仍由现有逻辑全部为 true。
- `admin`：默认 false，避免管理员自动出现在翻译工作下拉。
- `translator`：默认 false。是否可接翻译工作由管理员显式勾选。
- `user` / `analyst`：默认 false。

初始化 migration 只更新指定 5 人，不对所有翻译用户批量开启。

## 4. 后端设计

### 4.1 权限注册

在 `appcore/permissions.py` 的 `PERMISSIONS` 增加 `work_scope_translation`。它属于 `GROUP_CAPABILITY`，与 `can_translate` 相邻，便于管理员在权限 modal 中一起维护。

### 4.2 用户列表 helper

在 `appcore/users.py` 新增：

```python
def list_translation_work_users() -> list[dict]:
    """返回可接翻译工作的活跃用户。"""
```

返回格式沿用前端现有下拉需要的形态：

```json
[
  {"id": 123, "username": "worker", "display_name": "周干琴"}
]
```

显示名优先使用 `users.xingming`，没有该列或为空时 fallback 到 `username`。函数要能兼容测试库没有 `xingming` 列的情况。

### 4.3 后端校验 helper

在 `appcore/users.py` 新增：

```python
def ensure_translation_work_user(user_id: int) -> dict:
    """校验用户存在、启用、can_translate=true、work_scope_translation=true。"""
```

失败时抛 `ValueError`，错误消息清楚区分：
- 用户不存在或已停用
- 缺少 `can_translate`
- 缺少 `work_scope_translation`

### 4.4 API

新增或调整一个轻量 API：

`GET /tasks/api/translation-work-users`

返回：

```json
{"users": [{"id": 123, "username": "worker", "display_name": "周干琴"}]}
```

该接口 `login_required` 即可；页面本身已 admin-only，后端指派接口仍有 admin gate 和校验。

### 4.5 写路径校验

以下写路径在调用下游创建 / 入库前先校验 `translator_id`：

1. `POST /mk-import/video`
2. `POST /tasks/api/import-and-create`
3. `POST /tasks/api/parent`
4. `POST /new-product-review/api/<product_id>/decide`
5. `/xuanpin/api/today-recommendations/adopt` 对应的 service 入口

其中 `new_product_review._resolve_translator()` 需要从只看 `can_translate` 升级为同时检查 `work_scope_translation`。

## 5. 前端设计

`web/templates/mk_selection.html` 中：

- `MKI_ACTIVE_USERS_API` 改为 `/tasks/api/translation-work-users`。
- `mkiFetchActiveUsers()` 改名或内部语义调整为读取翻译工作用户。
- `mkiUserLabel()` 仍支持 `display_name || username`，避免后端字段切换造成空白。
- 空列表提示从 `没有可用员工` 改为 `没有可用翻译工作用户`。

两个 modal 复用同一缓存，因此“加入素材库”和“做小语种”展示完全一致。

## 6. Migration 初始化

新增 migration：`db/migrations/2026_05_20_user_translation_work_scope.sql`。

迁移策略：

1. 只匹配 `username` 或 `xingming` 等于以下名字的活跃 / 非活跃用户，不新建账号：
   - 周干琴
   - 顾倩
   - 王舒溦
   - 王健
   - 蔡靖华
2. `permissions` 为 NULL 或非法时，按空 JSON 处理。
3. 对匹配用户执行：
   - `$.can_translate = true`
   - `$.work_scope_translation = true`
4. migration 必须兼容线上可能存在 `users.xingming` 列、本地 schema 可能没有该列的差异。实现采用 `INFORMATION_SCHEMA.COLUMNS` + dynamic SQL。

## 7. 错误处理

| 场景 | 后端 | 前端 |
|---|---|---|
| 翻译工作用户列表为空 | 200 `{"users":[]}` | modal alert `没有可用翻译工作用户` |
| 传入用户不存在 / 停用 | 400 / 422 | toast 显示后端错误 |
| 缺 `can_translate` | 400 / 422 | toast `该用户没有翻译能力` |
| 缺 `work_scope_translation` | 400 / 422 | toast `该用户不在翻译工作范围` |

## 8. 测试计划

### 8.1 单元测试

- `tests/test_permissions.py`
  - `work_scope_translation` 在 `PERMISSION_CODES` 中。
  - admin / user / translator 默认都为 false。
  - superadmin 仍然全部 true。
- `tests/test_user_translators.py`
  - `list_translation_work_users()` 只返回同时具备 `can_translate` 与 `work_scope_translation` 的活跃用户。
  - `ensure_translation_work_user()` 接受合规用户，拒绝停用、缺能力、缺工作范围用户。

### 8.2 路由测试

- `tests/test_mk_import_routes.py`
  - `/mk-import/video` 传非翻译工作用户时拒绝，不调用入库 service。
- `tests/test_tasks_routes.py`
  - `/tasks/api/translation-work-users` 返回 `users`。
  - `/tasks/api/parent` 与 `/tasks/api/import-and-create` 传非翻译工作用户时拒绝。
- `tests/test_new_product_review_routes.py`
  - decide 接口传非翻译工作用户时返回 422。
- `tests/test_xuanpin_routes.py`
  - `mk_selection.html` 不再引用 `/medias/api/users/active`，改用 `/tasks/api/translation-work-users`。

### 8.3 手动验收

测试环境：

1. 超管进入 `用户管理`，确认 5 个指定用户勾选 `翻译员` 与 `翻译工作`。
2. 打开 `选品中心 → 明空选品`。
3. 点击任意卡片 `加入素材库`，下拉只展示：周干琴、顾倩、王舒溦、王健、蔡靖华。
4. 点击 `做小语种`，翻译员下拉同样只展示这 5 人。
5. 用 DevTools 改请求体传入其他用户 id，后端拒绝。

## 9. 回滚

- 代码回滚后，新增权限位不再被应用读取。
- migration 已写入的 `permissions.work_scope_translation=true` 可保留；如果需要数据回滚，可手动把 5 个用户该 key 置 false。
- 不涉及业务表结构变更，不影响已有任务和素材。
