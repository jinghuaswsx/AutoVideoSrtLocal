# 系统安全审计功能设计

- 日期：2026-05-02
- 分支：`feat/system-audit-logging`
- 状态：Draft，待用户 review

## 1. 背景

视频素材库目前对全员开放。管理员需要知道每个账号每天下载了哪些视频素材，以及在平台上做了哪些关键动作，用于安全追踪、责任定位和异常行为排查。

现有系统已有两类相近日志：

- `usage_logs`：AI / API 用量和成本统计，不适合承载安全审计。
- `task_events`：任务中心状态流转事件，只覆盖任务中心，不覆盖素材库和全站动作。

因此本功能新增独立的系统安全审计能力，避免污染现有成本统计和任务流转语义。

## 2. 目标

- 记录账号级关键行为：登录、退出、素材库写操作、任务动作、推送动作、检测/评估/翻译触发等。
- 重点记录素材库视频访问/下载行为：账号、日期、素材、商品、语言、来源路由、IP、User-Agent。
- 提供超级管理员专用后台页面查看审计日志和每日素材下载明细。
- 查询支持日期范围、账号、模块、动作类型、对象关键词筛选。
- 审计写入失败不得影响原业务请求。

## 3. 非目标

- 不记录所有页面浏览和列表刷新，避免日志量被普通 GET、轮询和静态资源请求淹没。
- 不做告警、风控拦截、自动封号或下载限流。
- 不回填历史行为日志。
- 不改变素材库当前“全员可访问”的权限模型。
- 不把安全审计混入 `usage_logs` 或 `task_events`。

## 4. 权限规则

审计数据属于最高敏感级别，只允许保留用户名为 `admin` 的超级管理员查看。

实现口径：

- 页面 `/admin/security-audit` 必须使用 `current_user.is_superadmin` 校验。
- 审计查询 API 必须使用同样的超级管理员校验。
- 侧栏入口只在 `current_user.is_superadmin` 为真时显示。
- 普通 `admin` 角色不能看到入口，直接访问页面或 API 返回 403。
- 普通用户不能看到入口，直接访问页面或 API 返回 403。

这个规则比现有 `admin_required` 更严格，因为 `admin_required` 会同时允许普通管理员角色。

## 5. 审计范围

### 5.1 重点记录

素材相关：

- 素材视频访问/下载：`media_items` 的视频对象访问。
- 原始去字幕素材视频访问/下载：`media_raw_sources` 的视频对象访问。
- 商品图/详情图 ZIP 下载。
- 素材产品新增、编辑、删除。
- 素材视频新增、编辑、删除。
- 封面、详情图、商品图新增、替换、删除。
- 素材翻译任务触发、链路检测触发、素材评分/评估触发。
- 推送到下游系统相关动作。

账号和系统动作：

- 登录成功。
- 登录失败。
- 退出登录。
- 用户管理里的新建用户、启停用户、角色调整、权限调整。
- 系统设置、API 配置、定时任务管理等超级管理员动作。

任务动作：

- 任务认领、提交、审核通过、打回、取消。
- 原始素材任务库里的处理和替换视频动作。

### 5.2 不记录或弱记录

- 静态资源请求不记录。
- 普通列表页 GET 不记录。
- 轮询接口不记录，除非它触发了状态变更。
- 图片缩略图和封面预览默认不作为重点下载行为记录；ZIP 下载和写操作会记录。

### 5.3 视频访问与下载的定义

浏览器内播放视频本质上也是对视频文件字节的访问，服务端无法总是区分“预览播放”和“手动保存到本地”。因此这期采用更保守的安全口径：

- 只要登录账号请求素材视频对象，就记录为 `media_video_access`。
- 如果路由明确使用附件下载或 ZIP 下载，则记录为 `media_download`。
- 日志详情里保留请求路径、Range 请求头、对象 key、素材 ID、商品 ID，便于后续判断是预览、拖动播放还是完整下载。

## 6. 数据模型

新增表：`system_audit_logs`

核心字段：

- `id BIGINT AUTO_INCREMENT PRIMARY KEY`
- `actor_user_id INT NULL`：操作者账号 ID，匿名或公开对象访问为空。
- `actor_username VARCHAR(64) NULL`：写入时保存用户名快照，避免账号改名后历史不可读。
- `action VARCHAR(64) NOT NULL`：动作编码，如 `login_success`、`media_video_access`。
- `module VARCHAR(64) NOT NULL`：模块编码，如 `auth`、`medias`、`tasks`、`pushes`。
- `target_type VARCHAR(64) NULL`：对象类型，如 `media_item`、`media_raw_source`、`media_product`。
- `target_id VARCHAR(64) NULL`：对象 ID，使用字符串兼容数字 ID 和任务 ID。
- `target_label VARCHAR(255) NULL`：对象名称快照，如视频文件名、商品名、任务名。
- `status VARCHAR(16) NOT NULL DEFAULT 'success'`：`success` / `failed`。
- `request_method VARCHAR(8) NULL`
- `request_path VARCHAR(512) NULL`
- `ip_address VARCHAR(64) NULL`
- `user_agent VARCHAR(512) NULL`
- `detail_json JSON NULL`
- `created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP`

建议索引：

- `idx_created_at (created_at)`
- `idx_actor_created (actor_user_id, created_at)`
- `idx_action_created (action, created_at)`
- `idx_module_created (module, created_at)`
- `idx_target (target_type, target_id, created_at)`

## 7. 代码结构

新增：

- `appcore/system_audit.py`
  - `record(...)`：写入审计日志，捕获异常并仅 debug 记录。
  - `record_from_request(...)`：从 Flask request/current_user 填充 IP、UA、路径等上下文。
  - `list_logs(...)`：后台审计列表查询。
  - `list_daily_media_downloads(...)`：每日素材下载明细查询。
  - `summarize(...)`：页面顶部统计卡片。

- `web/routes/security_audit.py`
  - `GET /admin/security-audit`：审计页面。
  - `GET /admin/security-audit/api/logs`：审计日志 JSON。
  - `GET /admin/security-audit/api/media-downloads`：每日素材下载明细 JSON。

- `web/templates/admin_security_audit.html`
  - 顶部统计卡：今日视频访问、今日下载、涉及账号、失败动作。
  - 筛选区：日期范围、账号、模块、动作、关键词。
  - Tabs：`操作日志` / `素材下载明细`。
  - 空状态、加载态、错误态。

- `web/static/admin_security_audit.js`
  - 筛选、分页、tab 切换、错误展示。

- `db/migrations/2026_05_02_system_audit_logs.sql`
  - 创建表和索引。

## 8. 埋点策略

### 8.1 登录和退出

在 `web/routes/auth.py`：

- 登录成功：记录 `auth.login_success`。
- 登录失败：记录 `auth.login_failed`，`actor_user_id` 为空，`detail_json` 保存提交的用户名，不保存密码。
- 退出登录：在 `logout_user()` 前记录 `auth.logout`。

### 8.2 素材视频访问

在 `web/routes/medias.py`：

- `media_object_proxy`：解析 `object_key`，如果能匹配 `media_items.object_key`，记录 `medias.media_video_access`。
- `raw_source_video_url`：记录 `medias.raw_source_video_access`。
- `public_media_object`：无登录上下文时记录 `actor_user_id=NULL` 的公开对象访问；如果将来公开路由被下游频繁调用导致噪音过大，可仅记录匹配视频扩展名的对象。
- `api_detail_images_download_zip`：记录 `medias.detail_images_zip_download`。
- `api_detail_images_download_localized_zip`：记录 `medias.localized_detail_images_zip_download`。

视频访问详情包含：

- `product_id`
- `product_name`
- `media_item_id` 或 `raw_source_id`
- `filename` / `display_name`
- `lang`
- `object_key`
- `range_header`
- `file_size`

### 8.3 素材写操作

在 `web/routes/medias.py` 关键 POST / PUT / PATCH / DELETE 路由成功后记录：

- 产品创建、更新、删除。
- owner 变更。
- 素材 item 新增、修改、删除。
- raw source 新增、改名、删除。
- 封面和详情图新增、替换、删除、重排。
- 链路检测、评估、翻译任务触发。

只在业务成功后写审计日志，失败请求只对安全敏感动作记录失败，例如删除失败、权限拒绝、登录失败。

### 8.4 任务和推送动作

任务中心优先在 service 层或 route 成功后记录：

- 认领、上传完成、提交、审核通过、打回、取消。

推送管理记录：

- 推送、标记成功、标记失败、重置、文本推送等写操作。

已有 `task_events` 不替代安全审计；它仍作为任务流转业务事件保留，安全审计只记录可追责的账号动作视图。

## 9. 页面设计

页面遵循 Ocean Blue Admin 风格。

布局：

- 顶部标题：`系统安全审计`
- 描述短文本：仅超级管理员可见，用于追踪账号动作和素材访问。
- 统计卡：今日视频访问、今日 ZIP 下载、今日活跃账号、失败动作。
- 筛选条：日期范围、账号、模块、动作、关键词、查询按钮。
- Tabs：
  - `操作日志`：按时间倒序展示所有关键动作。
  - `素材下载明细`：按账号、日期、素材维度展示视频访问/下载记录。

表格列：

操作日志：

- 时间
- 账号
- 模块
- 动作
- 对象
- 状态
- IP
- 路径
- 详情

素材下载明细：

- 时间
- 账号
- 商品
- 素材
- 语言
- 类型
- IP
- 请求信息

状态：

- Loading：表格内显示加载中。
- Empty：显示“当前筛选条件下暂无审计记录”。
- Error：显示接口错误和重试按钮。

## 10. 测试策略

单元和路由测试：

- `tests/test_system_audit.py`
  - `record()` 正常写入。
  - `record()` 在数据库异常时不抛出。
  - `list_logs()` 按账号、日期、模块、动作、关键词过滤。
  - `list_daily_media_downloads()` 只返回视频访问和下载动作。

- `tests/test_security_audit_routes.py`
  - 超级管理员 `admin` 可以访问页面和 API。
  - 普通 admin 返回 403。
  - 普通用户返回 403。
  - 页面包含两个 tab 和筛选控件。

- `tests/test_medias_audit.py`
  - 访问 `media_object_proxy` 命中 `media_items.object_key` 时记录素材视频访问。
  - 访问 raw source 视频时记录原始素材视频访问。
  - 下载详情图 ZIP 时记录下载动作。
  - 素材删除成功后记录删除动作。

验证命令：

```powershell
pytest tests/test_system_audit.py tests/test_security_audit_routes.py tests/test_medias_audit.py -q
```

如果修改任务或推送埋点，再追加对应聚焦测试。

## 11. 发布和运维

- 新增数据库迁移，发布测试环境时先运行迁移。
- 审计表只追加，不参与业务事务主路径。
- 审计写入失败只记录 debug 日志，不阻塞用户操作。
- 本期不自动清理历史审计日志。后续如数据量增长，可新增保留周期设置或定时归档；如果新增定时清理任务，必须登记到 Web 后台“定时任务”模块。

## 12. 风险和取舍

- 视频预览和下载难以完全区分，本期统一记录为视频访问，并保留 Range 请求头辅助判断。
- 公开 `/medias/obj/...` 路由没有登录账号，只能记录匿名访问和对象 key；这仍有助于定位外部下游系统访问，但不能归属到内部账号。
- 只记录重点动作，不记录所有 GET，因此“平台上的所有动作”在本期定义为所有关键可追责动作，而不是每次页面刷新。
- 普通 admin 无法看审计数据，符合用户补充要求，但也意味着日常管理排查必须由超级管理员 `admin` 执行。
