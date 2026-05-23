# 2026-05-22 服务器地址全局配置

## 背景

推送页面的视频预览拿到的历史 payload URL 仍指向旧服务器 `172.16.254.106`，浏览器无法从该地址拉取视频。当前服务器已切到 `172.16.254.106`，运行代码中也存在多处显式写死当前 IP，后续迁移时容易再次出现同类问题。

## 目标

- 建立一个轻量的全局服务器地址配置模块，作为运行代码和脚本默认服务器地址的唯一来源。
- 保留环境变量覆盖能力，便于测试环境、打包工具和运维脚本按需切换服务器。
- 推送页面预览本项目 `/medias/obj/...` 素材时使用当前页面 origin，避免历史绝对 URL 的主机名影响本地预览。
- 增加源码扫描测试：旧服务器 IP 不能出现在运行面；当前服务器 IP 只能出现在全局配置默认值里。

## 范围

纳入扫描与替换：

- `config.py`
- `appcore/`
- `web/`
- `tools/`
- `AutoPush/backend/`
- `link_check_desktop/`
- `scripts/`
- `deploy/`
- `tests/`

不做机械改写：

- 历史计划、历史规格、README、已落地数据库迁移等记录性文档。
- 下游第三方服务地址，例如推送目标 `172.17.254.77`。

## 配置约定

- `server_config.py` 是默认服务器地址唯一源码锚点。
- 默认主机名：`DEFAULT_SERVER_HOST`。
- 运行覆盖：
  - `AUTOVIDEOSRT_SERVER_HOST`
  - `AUTOVIDEOSRT_SERVER_SCHEME`
  - `AUTOVIDEOSRT_SERVER_BASE_URL`
  - `AUTOVIDEOSRT_TEST_SERVER_BASE_URL`
  - `AUTOVIDEOSRT_LOCAL_IMAGE_BASE_URL`
- 向后兼容：
  - 主项目 `LOCAL_SERVER_BASE_URL` 仍可覆盖素材公开 URL。
  - AutoPush `AUTOVIDEO_BASE_URL` 仍可覆盖 AutoVideo API 地址。

## 校验

- 单元测试覆盖默认值、环境变量覆盖和 `config.LOCAL_SERVER_BASE_URL` 回落逻辑。
- 源码扫描测试禁止运行面散落 `172.16.254.106` 或除 `server_config.py` 外的 `172.16.254.106`。
- 相关模块的既有测试更新为引用全局配置常量，而不是断言裸 IP。
