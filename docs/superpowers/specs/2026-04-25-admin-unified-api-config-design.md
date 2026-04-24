# Admin Unified API Config Design

## Goal

API 配置由用户名为 `admin` 的单独用户统一维护。配置入口只对 `admin` 可见、可访问、可提交；所有用户运行任务时统一读取 `admin` 的 API Key、Provider extra 和默认模型配置。剪映导出目录是用户侧本机路径，单独放到「用户设置」。

## Scope

- `/settings` 页面和表单提交只允许 username 为 `admin` 的用户访问。
- 侧栏 `API 配置` 入口只对 username 为 `admin` 的用户展示。
- `appcore.api_keys` 的运行时读取入口统一解析到 `admin` 配置 owner。
- `jianying` 导出目录保持用户级配置，放到 `/user-settings`，侧栏显示「用户设置」。
- API 配置页所有输入项明文展示，并在每个输入/下拉/文本域后附带复制按钮。
- `llm_use_case_bindings`、图片翻译通道、AI 定价、推送配置继续作为全局配置存在。
- 普通用户仍以自己的 `user_id` 创建任务和记录账单；只是不再拥有独立 API 配置。

## Architecture

在 `appcore.api_keys` 增加配置 owner 解析：优先查询 `users.username = 'admin'` 的 id，读取类函数对 API 服务忽略调用方 `user_id`，统一读取 admin 行；若没有 admin 用户，则不读取任何用户行并回退到环境变量或默认值。写入类函数要求调用方必须是 username=`admin`，否则拒绝写入，避免普通用户交互误改全局配置。`jianying` 是明确例外，按当前用户读写。

`web.routes.settings` 使用新的 `admin_config_required` 权限判断，区别于 role-based `admin_required`：即使存在其他管理员角色，也不能进入 API 配置。模板 `layout.html` 同步隐藏入口。

## Entry Cleanup

- 服务商接入：API Key、Base URL、服务 extra、图片翻译通道。
- 模块模型分配：UseCase 到 Provider/Model 的全局绑定。
- AI 定价：价格维护。
- 推送：推送目标与 wedev 凭据。
- 用户设置：剪映导出目录。

以上入口均只在 `/settings` 内由 `admin` 维护。

## Testing

- `appcore.api_keys`：普通用户读取 openrouter 时返回 admin 配置；普通用户写入被拒绝；admin 写入成功。
- `/settings`：普通用户和非 `admin` 用户名的管理员返回 403；username=`admin` 正常访问。
- `layout.html`：普通用户不渲染 API 配置导航。
- `/user-settings`：所有登录用户可查看和保存自己的剪映导出目录。
