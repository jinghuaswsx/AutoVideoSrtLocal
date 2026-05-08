# Meta 登录明文凭据自动填充设计（2026-05-08）

## 背景

`DXM01-Meta` 可视浏览器服务和 CDP 端口当前正常运行，但 Meta/Facebook 登录态会过期。2026-05-08 现场检查显示：

- `autovideosrt-dxm01-meta-vnc.service` 正常运行。
- CDP：`http://127.0.0.1:9222`。
- noVNC：`http://127.0.0.1:6092/vnc.html?host=127.0.0.1&port=6092&autoconnect=true&resize=remote`。
- 当前页面跳到 `business.facebook.com/business/loginpage` / `facebook.com/login`。
- `autovideosrt-roi-realtime-sync.service` 日志报 `server browser is not logged in`。

用户明确要求：Facebook 账号密码按明文存数据库，后续掉线后可由系统自动输入账号密码登录。

## 目标

1. 在数据库中明文保存 `DXM01-Meta` 对应的 Facebook 登录账号和密码。
2. Meta 同步检测到掉线后，可以自动读取明文账号密码并填入 Facebook 登录页。
3. 自动登录成功后，重新验证 Ads Manager 页面可访问，再继续广告导出或补抓。
4. 凭据维护入口仅限 superadmin，页面不回显密码明文，避免误复制或肩窥。
5. 运行日志、任务 summary、错误信息不得输出账号或密码明文。

## 非目标

- 不加密存储。这里按用户要求，数据库字段保存可直接读取的明文。
- 不绕过验证码、二次验证、安全检查、设备确认或 Meta 风控。
- 不把账号密码写入 `system_settings.meta_ad_accounts`，广告账户配置仍只保存 account/business/csv/store 映射。
- 不把账号密码写入命令行参数、环境变量、systemd unit 或 git 文件。
- 不自动处理 Facebook 密码错误、账号锁定或被风控限制。

## 数据模型

新增表 `browser_login_credentials`：

```sql
CREATE TABLE IF NOT EXISTS browser_login_credentials (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  env_code VARCHAR(64) NOT NULL,
  provider VARCHAR(64) NOT NULL,
  username VARCHAR(255) NOT NULL,
  password VARCHAR(1024) NOT NULL,
  enabled TINYINT(1) NOT NULL DEFAULT 1,
  last_login_at DATETIME DEFAULT NULL,
  last_login_status VARCHAR(64) DEFAULT NULL,
  last_error VARCHAR(512) DEFAULT NULL,
  updated_by INT DEFAULT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  UNIQUE KEY uk_browser_login_credentials_env_provider (env_code, provider),
  KEY idx_browser_login_credentials_enabled (enabled)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='Browser login credentials stored in plaintext by explicit operator request';
```

初始目标记录：

- `env_code = "DXM01-Meta"`
- `provider = "facebook"`

字段说明：

- `username`：Facebook 登录账号，明文保存。
- `password`：Facebook 登录密码，明文保存。
- `enabled`：关闭后自动登录不读取该凭据。
- `last_login_status`：`success` / `needs_human` / `failed` / `disabled`。
- `last_error`：只保存错误分类，不保存账号密码。

## DAO

新增 `appcore/browser_login_credentials.py`：

- `get_credential(env_code, provider)`：返回 enabled 的明文账号密码。
- `upsert_credential(env_code, provider, username, password, enabled, updated_by)`：superadmin 保存。
- `mark_login_result(env_code, provider, status, error=None)`：自动登录后写状态。
- `mask_username(username)`：页面展示账号脱敏，保留前后少量字符。

DAO 不做日志输出；调用方如需日志，只记录 `env_code`、`provider`、状态和错误分类。

## 设置入口

在 `/settings` 新增 tab：`browser_credentials`，仅 superadmin 可见。

页面内容：

- 标题：浏览器登录凭据。
- 说明：这些凭据按用户要求明文存储在 MySQL，仅用于服务器可视浏览器自动补登录。
- 表单字段：
  - 环境：固定 `DXM01-Meta`。
  - Provider：固定 `facebook`。
  - 账号：可编辑；保存后页面展示脱敏值。
  - 密码：`type=password`；留空表示不修改，输入新值表示覆盖。
  - 启用：checkbox。
  - 状态：最近一次自动登录状态、时间、错误分类。

页面不回显密码明文。明文只存在数据库字段和后端内存变量中。

## 自动登录流程

新增 `appcore/meta_login_autofill.py` 或 `tools/meta_login_autofill.py`，核心函数：

```python
ensure_meta_login(cdp_url, env_code="DXM01-Meta", provider="facebook", target_url=None) -> dict
```

流程：

1. 连接 `DXM01-Meta` CDP。
2. 打开或复用 Meta Ads Manager 页面。
3. 判断是否在登录页：
   - URL 包含 `business.facebook.com/business/loginpage`
   - URL 包含 `facebook.com/login`
   - 页面文本包含 `Log in with Facebook` / `Log into Ads Manager`
4. 如果未掉线，返回 `{"status": "already_logged_in"}`。
5. 如果掉线，读取 `browser_login_credentials(DXM01-Meta, facebook)`。
6. 填入 `input[name=email]` 和 `input[name=pass]`。
7. 提交登录。
8. 等待跳转，重新访问目标 Ads Manager URL。
9. 若进入 Ads Manager，写 `success`。
10. 若出现验证码、2FA、安全检查、设备确认，写 `needs_human`，返回给调用方。
11. 若密码错误或仍停留登录页，写 `failed`。

安全检查关键字：

- `checkpoint`
- `two-factor`
- `authentication code`
- `captcha`
- `confirm your identity`
- `secure your account`

这些情况必须停止自动化，不能继续尝试。

## 同步入口接入

接入点：

- `tools/roi_hourly_sync.py`
- `tools/meta_daily_final_sync.py`
- 后续历史回填脚本

接入规则：

1. 调用导出前先做轻量登录态检测。
2. 如果检测到登录页，先拿现有浏览器/CDP 锁。
3. 调 `ensure_meta_login(...)`。
4. 成功后继续导出。
5. `needs_human` 或 `failed` 时，本账户本轮失败，不影响其他账户；错误写为分类，不写账号密码。
6. 不在自动登录过程中并发操作同一个 CDP。

## 日志和泄露控制

必须遵守：

- 不把账号密码放入命令行参数。
- 不 `print()` 账号密码。
- 不把账号密码写入 `scheduled_task_runs.summary_json`。
- 不把账号密码写入 `meta_ad_realtime_import_runs.summary_json`。
- 不保存登录截图。
- 不启用 Playwright trace。
- 测试 fixtures 不使用真实账号密码。

允许：

- DB 中 `browser_login_credentials.username/password` 明文保存。
- settings 页面展示账号脱敏、密码配置状态。
- 自动登录代码在进程内短暂持有账号密码变量。

## 验收标准

- superadmin 可在 `/settings?tab=browser_credentials` 保存 `DXM01-Meta/facebook` 账号密码。
- DB 中能看到明文 `username` 和 `password` 字段。
- 页面刷新后不回显密码明文，只显示已配置状态。
- 当 `DXM01-Meta` 跳到 Facebook 登录页时，系统能自动填账号密码并提交。
- 登录成功后，Meta Ads Manager 页面不再停留 loginpage。
- 同步任务遇到掉线时会先尝试自动登录，再重试导出。
- 遇到验证码、2FA、安全检查时返回 `needs_human`，不继续尝试。
- 日志和 summary 中没有真实账号密码。

## 验证

自动化测试：

- DAO upsert/read/mask/status。
- settings tab 只有 superadmin 可访问。
- 密码留空不覆盖旧密码。
- 自动登录 helper 在 mock Facebook 登录页能填表提交。
- `needs_human` 页面会停止并写状态。
- 同步入口在 `login_required` 错误后会调用自动登录 helper 并重试一次。

手工验证：

1. 在测试环境或服务器本机保存 `DXM01-Meta/facebook` 凭据。
2. 手动让 `DXM01-Meta` 浏览器停在 Facebook login 页面。
3. 运行登录检测/补登录命令。
4. 确认 Ads Manager 可访问。
5. 查看 journal 和 DB summary，确认没有账号密码明文泄露到日志。

## Docs-anchor

- `CLAUDE.md` 的 “Meta 广告多账户同步” 小节。
- `docs/superpowers/specs/2026-05-07-meta-ads-multi-account-design.md`
- `docs/superpowers/specs/2026-05-08-meta-ads-creative-analysis-design.md`
- 本文件
