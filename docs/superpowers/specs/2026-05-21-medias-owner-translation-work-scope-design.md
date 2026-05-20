# 素材管理负责人收敛到翻译工作用户 设计文档

- **日期**：2026-05-21
- **状态**：已确认，待实施
- **锚点来源**：用户确认“素材管理的负责人规则也修改为翻译工作用户范围，这样才可以配置为对应的素材管理负责人规则”
- **覆盖关系**：
  - 覆盖 [2026-04-24-medias-owner-reassign-design.md](2026-04-24-medias-owner-reassign-design.md) 中“负责人下拉用户范围 = 所有 `is_active=1` 用户”的旧决策
  - 覆盖 [2026-05-20-user-work-scope-translation-design.md](2026-05-20-user-work-scope-translation-design.md) 中“本期不改变素材管理负责人范围”的旧边界

## 1. 目标

把素材管理里“负责人”的候选人与写入校验统一收敛到“翻译工作用户”范围。素材管理前端、pushes 页面产品负责人筛选、以及负责人变更写路径都要使用同一套翻译工作用户语义，不再使用“所有活跃用户”。

## 2. 本期范围

1. 保留现有接口路径 `GET /medias/api/users/active`，但其返回语义改为“可担任素材管理负责人的翻译工作用户列表”。
2. `PATCH /medias/api/products/<pid>/owner` 的后端校验改为要求目标用户通过 `ensure_translation_work_user(user_id)`。
3. 保留素材负责人切换后的既有级联行为：
   - 更新 `media_products.user_id`
   - 更新该项目下未软删除的 `media_items.user_id`
   - 更新该项目下未软删除的 `media_raw_sources.user_id`
   - 继续触发任务中心 `tasks.on_product_owner_changed(...)`
4. 前端不新增新路由、不改现有调用路径，只调整文案语义，使负责人下拉明确对应翻译工作用户。

## 3. 不做什么

- 不改“选择产品负责人”流程；该项由主流程独立处理。
- 不改明空“加入素材库”弹窗顺序。
- 不新增数据库 schema 或迁移；继续复用 `users.permissions.work_scope_translation`。
- 不改任务中心语言指派、国家分发、入库主流程。

## 4. 设计

### 4.1 读路径

- `web/services/media_pages.build_active_users_response()` 改为默认调用 `appcore.users.list_translation_work_users()`。
- `GET /medias/api/users/active` 路径与 admin gate 保持不变，前端调用方无需换 URL。
- `web/static/medias.js` 的负责人 inline edit 下拉继续请求该接口。
- `web/static/pushes.js` 的产品负责人筛选继续请求该接口，因此筛选候选也同步收敛到翻译工作用户。

### 4.2 写路径

- `appcore.medias.update_product_owner(product_id, new_user_id)` 在产品存在校验外，改为调用 `appcore.users.ensure_translation_work_user(new_user_id)`。
- 如果目标用户不在翻译工作范围，抛出 `ValueError("该用户不在翻译工作范围")`；路由层继续映射为 400。
- 如果用户不存在、已停用、没有翻译能力，也沿用 `ensure_translation_work_user()` 的错误文案。

### 4.3 显示名

- `owner_name` 的返回仍沿用 `appcore.medias.get_user_display_name()`。
- 素材管理与 pushes 的下拉显示名仍优先 `xingming`，否则 fallback `username`。

## 5. 验证要求

1. `/medias/api/users/active` 返回值证明已走翻译工作用户 helper，而非 `medias.list_active_users()`。
2. 活跃但不在翻译工作范围的用户，调用 `PATCH /medias/api/products/<pid>/owner` 返回 400。
3. 合规翻译工作用户仍可成功接管负责人，且三张业务表与任务中心级联行为不回归。
4. 非 admin 访问现有接口仍为 403。

## 6. 测试计划

- `tests/test_media_pages_service.py`
  - `build_active_users_response()` 默认依赖改为 translation-work users
- `tests/test_medias_pages_routes.py`
  - `/medias/api/users/active` 仍先过 admin gate，再走 service builder
- `tests/test_medias_routes.py`
  - admin 可拿到翻译工作用户列表
  - owner 更新错误透传中文翻译工作范围错误
- `tests/test_appcore_medias.py`
  - `update_product_owner()` 拒绝“活跃但不在翻译工作范围”的用户
  - 成功路径仍保留三表同步与任务中心 cascade
- `tests/test_pushes_ui_assets.py`
  - 继续校验筛选脚本仍走 `/medias/api/users/active`

## 7. 回滚

- 代码回滚即可恢复“所有活跃用户”旧语义。
- 不涉及 schema 变更，无需数据回滚脚本。
