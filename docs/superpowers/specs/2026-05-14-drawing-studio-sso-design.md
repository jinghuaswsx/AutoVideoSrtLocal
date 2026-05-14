# 画图工作室菜单与 SSO 设计

日期：2026-05-14

## 背景

AutoVideoSrtLocal 需要在左侧主菜单新增一个大功能项「画图工作室」。该入口跳转到本机 81 端口的 Canvas Realm Studio 根页面，并带上当前 AutoVideoSrtLocal 登录用户的身份信息，实现免密码登录。

已确认端口：

- `http://127.0.0.1:81/` 是 Canvas Realm Studio / 画图工作室。
- `http://127.0.0.1:82/` 当前是 `sub2api`，本次不作为画图工作室入口。

## 文档锚点

- `AGENTS.md#硬红线`：文档驱动代码、worktree 隔离、改代码前必须有文档锚点。
- `AGENTS.md#structure`：`web/` 存放 Flask 路由与模板，`tests/` 存放 pytest。
- `web/templates/layout.html`：左侧菜单的唯一模板来源。
- `appcore/permissions.py`：菜单/页面级权限注册表；新增大功能项需要登记权限。
- `web/templates/CLAUDE.md#模板约束`：继承 `layout.html` 的页面需登录保护，POST/fetch 需遵守 CSRF 约束。
- `/home/cjh/code/canvas-realm-gpt-image-2-studio/docs/ARCHITECTURE.md#模块边界`：Canvas Realm 的认证与权限边界在 `lib/auth.ts` / `lib/permissions.ts`。
- `/home/cjh/code/canvas-realm-gpt-image-2-studio/README.md#项目结构`：Canvas Realm 的 Next.js API 路由、组件、数据库和 worker 目录约定。

## 目标

1. AutoVideoSrtLocal 左侧菜单新增「画图工作室」大功能项。
2. 点击菜单后跳转到 `http://127.0.0.1:81/` 对应的 Canvas Realm Studio 根页面。
3. 跳转时携带当前 AutoVideoSrtLocal 登录用户身份。
4. Canvas Realm 校验身份后自动登录，不要求用户再次输入密码。
5. 不传递用户密码，不复用 AutoVideoSrtLocal 的密码哈希。

## 非目标

- 不把 82 端口接入画图工作室。
- 不改 Canvas Realm 的图片生成、队列、模型配置或额度计算逻辑。
- 不把两个系统合并成同一个账号数据库。
- 不引入反向代理共享 Cookie 或统一 OAuth 服务。
- 不开放公网 SSO；本设计只面向内网便利登录。

## 方案

采用短期 HMAC 签名 SSO。

AutoVideoSrtLocal 新增内部跳转路由：

```text
GET /drawing-studio/sso
```

该路由要求当前用户已登录。路由读取 `current_user.id`、`current_user.username`、`current_user.role`，生成短期有效载荷并签名，然后 302 跳转到 Canvas Realm 的 SSO 接收接口：

```text
http://127.0.0.1:81/api/auth/autovideosrt-sso?...signed params...
```

Canvas Realm 的 SSO 接收接口校验签名和过期时间，通过后自动创建或匹配本地 Canvas Realm 用户，创建 `image_gen_session`，设置 Cookie，再 302 跳回 `/`。

## 参数与签名

跳转参数：

| 参数 | 说明 |
| --- | --- |
| `avs_user_id` | AutoVideoSrtLocal 用户 ID |
| `avs_username` | AutoVideoSrtLocal 用户名 |
| `avs_role` | AutoVideoSrtLocal 角色 |
| `exp` | Unix 秒级过期时间，建议当前时间 + 120 秒 |
| `nonce` | 随机字符串，用于让每次跳转签名不同 |
| `sig` | HMAC-SHA256 签名，hex 编码 |

签名输入使用稳定排序后的 query string，不包含 `sig`：

```text
avs_role=<role>&avs_user_id=<id>&avs_username=<username>&exp=<exp>&nonce=<nonce>
```

签名密钥：

- AutoVideoSrtLocal 使用环境变量 `DRAWING_STUDIO_SSO_SECRET`。
- Canvas Realm 使用同名环境变量 `DRAWING_STUDIO_SSO_SECRET`。
- 缺少密钥时 SSO 路由返回 503 或接收端返回 403，不降级为免签登录。

## 用户映射

Canvas Realm 的用户表要求 `email` 唯一。本集成使用稳定的内部邮箱作为跨系统绑定键：

```text
autovideosrt-<avs_user_id>@internal.local
```

Canvas Realm 用户字段映射：

| Canvas Realm 字段 | 来源 |
| --- | --- |
| `email` | `autovideosrt-<avs_user_id>@internal.local` |
| `name` | `avs_username` |
| `role` | AutoVideoSrtLocal `superadmin` / `admin` 映射为 `admin`，其他映射为 `member` |
| `group_id` | Canvas Realm 默认注册分组 |
| `monthly_quota` | Canvas Realm 默认注册额度 |

如果用户已存在，更新 `name` 与 `role`，保留既有分组和额度，避免覆盖画图工作室管理员在本地做过的运营配置。

## AutoVideoSrtLocal 改动范围

1. 新增 SSO 工具函数，负责构造签名 URL。
2. 新增 `drawing_studio` Flask blueprint：
   - `GET /drawing-studio/sso`
   - `@login_required`
   - 读取当前用户后重定向到 81 端口接收接口。
3. `web/app.py` 注册 blueprint。
4. `appcore/permissions.py` 新增菜单权限 `drawing_studio`：
   - 分组：业务功能。
   - admin 默认开启。
   - user 默认开启。
5. `web/templates/layout.html` 左侧主菜单新增「画图工作室」大功能项：
   - 目标：`url_for('drawing_studio.sso')`
   - 图标建议：`🎨`
   - 默认新窗口打开，保持现有主菜单习惯。
6. 新增或更新测试：
   - 权限注册与默认值测试。
   - 菜单可见性测试。
   - SSO 路由未登录 302 到登录页。
   - SSO 路由已登录时生成到 `127.0.0.1:81` 的签名跳转。

## Canvas Realm 改动范围

1. 新增服务端 SSO 校验工具，使用 Node `crypto` 验证 HMAC。
2. 新增 Next.js API route：
   - `GET /api/auth/autovideosrt-sso`
   - 校验 `DRAWING_STUDIO_SSO_SECRET`、参数完整性、过期时间和签名。
   - 通过后 upsert 用户并创建 session cookie。
   - 成功后 302 到 `/`。
3. 扩展 `lib/db.ts`：
   - 增加按 email upsert SSO 用户的函数，或复用现有 `getUserByEmail` / `createUser` / `updateUser`。
4. 新增测试：
   - 有效签名可创建或匹配用户。
   - 过期签名失败。
   - 错误签名失败。
   - admin/member 角色映射正确。

## Canvas Realm Agent 协作指令

可把下面这段交给 `/home/cjh/code/canvas-realm-gpt-image-2-studio` 项目里的 agent：

```text
你负责 Canvas Realm Studio 侧的 AutoVideoSrtLocal SSO 接收端。

背景：
- 主系统 AutoVideoSrtLocal 会从 /drawing-studio/sso 跳转到 http://127.0.0.1:81/api/auth/autovideosrt-sso。
- Query 参数为 avs_user_id、avs_username、avs_role、exp、nonce、sig。
- sig 是 HMAC-SHA256 hex，密钥来自 DRAWING_STUDIO_SSO_SECRET。
- 签名输入为不含 sig 的稳定排序 query string：
  avs_role=<role>&avs_user_id=<id>&avs_username=<username>&exp=<exp>&nonce=<nonce>

实现要求：
1. 新增 GET /api/auth/autovideosrt-sso。
2. 缺少 DRAWING_STUDIO_SSO_SECRET、参数缺失、exp 过期、签名不匹配时返回 403 或重定向到 /login 并带错误提示，不能创建 session。
3. 校验通过后用 email=autovideosrt-<avs_user_id>@internal.local 匹配 Canvas Realm 用户。
4. 不存在则创建用户：name=avs_username；role=admin 当 avs_role 是 admin 或 superadmin，否则 member；group/quota 使用现有默认注册设置；password_hash 使用随机不可登录密码哈希。
5. 已存在则更新 name 和 role，但不要覆盖 group_id 和 monthly_quota。
6. 调用现有 createUserSession / setSessionCookie 设置 image_gen_session，最后 302 到 /。
7. 增加单元测试覆盖有效签名、过期签名、错误签名、角色映射。
8. 不改图片生成、队列、模型 provider、OpenAI OAuth 逻辑。
9. 完成后运行 bun test 或至少新增测试 + bun run typecheck，并汇报改动文件。
```

## 安全说明

- URL 中不出现密码或 password hash。
- 签名有效期短，默认 120 秒。
- 签名密钥由部署环境配置，不能写入代码或提交到 Git。
- Canvas Realm 不信任裸 query 参数，必须先验签再登录。
- SSO 仅接受 `127.0.0.1:81` 当前部署；如未来换域名，需要同步更新 AutoVideoSrtLocal 的目标地址配置。

## 验证计划

AutoVideoSrtLocal：

```bash
pytest tests/test_appcore_permissions_drawing_studio.py tests/test_drawing_studio_sso.py tests/test_tools_routes.py -q
```

Canvas Realm：

```bash
bun test
bun run typecheck
```

端到端手动验证：

1. AutoVideoSrtLocal 未登录访问 `/drawing-studio/sso` 应跳登录页。
2. 登录 AutoVideoSrtLocal 后点击左侧「画图工作室」。
3. 浏览器跳到 `http://127.0.0.1:81/`。
4. Canvas Realm 顶部显示当前用户名，不再要求输入密码。
5. 管理员用户在 Canvas Realm 仍可访问管理员后台，普通用户不可访问管理员后台。
