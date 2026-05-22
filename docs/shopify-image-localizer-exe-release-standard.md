# Shopify Image Localizer EXE 打包发布规范

最后更新：2026-05-13

## 目标

Shopify Image Localizer 是给 Windows 用户下载运行的 EXE 工具。每次发布必须能持续继承已修复的问题，不能再次出现“源码已修、发布包仍是旧行为”“配置为空或 demo-key”“zip 指向旧版本”“在临时 worktree 打了包”等事故。

本规范是打包发布的唯一标准流程。任何人打包 `ShopifyImageLocalizer-portable-<version>.zip` 前，必须先读本文件，并在命令里显式传 `--release-standard-read`。

## 强制门禁

1. **只能用普通 `master` checkout 打包。**
   - 禁止在 `.paseo/worktrees/*`、`.worktrees/*`、Git linked worktree、临时分支、hotfix 分支里打包。
   - 发布脚本和 `build_exe.py` 会校验 `git rev-parse --git-dir` 与 `git rev-parse --git-common-dir` 必须相同。
2. **必须是最新 `origin/master`。**
   - 打包前先 `git fetch origin master`。
   - `HEAD` 必须等于 `origin/master`。
   - tracked 文件必须干净；不能带未提交源码改动打包。
3. **版本号必须进入源码。**
   - `tools/shopify_image_localizer/version.py` 的 `RELEASE_VERSION` 必须等于本次 `--version`。
   - 不允许只靠 `--version 4.8` 生成标题或文件名；版本 bump 必须 commit 到 `master` 后再打包。
4. **必须显式确认读过本规范。**
   - Linux 服务器发布命令必须带 `--release-standard-read`。
   - Windows fallback 打包命令也必须带 `--release-standard-read`。
   - 没带这个参数时，打包入口直接失败。
5. **同版本禁止覆盖。**
   - 本地发布目录、zip、服务器下载目录里已有同版本产物时，必须换更高版本号。
   - 不允许覆盖旧 zip 来“修同版本”。

## 打包代码来源

每个 EXE 必须由 `origin/master` 上的这些代码构建：

- 入口：`tools/shopify_image_localizer/main.py`
- GUI：`tools/shopify_image_localizer/gui.py`
- 控制器：`tools/shopify_image_localizer/controller.py`
- 运行配置：`tools/shopify_image_localizer/settings.py`
- 打包入口：`tools/shopify_image_localizer/build_exe.py`
- PyInstaller spec：`tools/shopify_image_localizer/packaging/shopify_image_localizer.spec`
- RPA/CDP 逻辑：`tools/shopify_image_localizer/browser/`、`tools/shopify_image_localizer/rpa/`

打包前必须确认本次需求涉及的提交已经在 `origin/master` 上。不要从“刚改完的 worktree”直接打包；应该先提交、推送、到主 checkout 拉最新 master，再发布。

## 标准发布流程

### 1. 合并代码到 master

在开发 worktree 里完成代码与测试后：

```powershell
git push origin HEAD:master
```

随后切到普通主 checkout，例如生产服务器 `/opt/autovideosrt` 或 Windows 主目录 `G:\Code\AutoVideoSrtLocal`，确认：

```bash
git fetch origin master
git checkout master
git pull --ff-only origin master
git status --short --untracked-files=no
git rev-parse HEAD
git rev-parse origin/master
```

要求：

- 当前分支是 `master`。
- `git status --short --untracked-files=no` 没有输出。
- `HEAD` 与 `origin/master` 输出相同。

### 2. 更新版本号

修改并提交：

```python
# tools/shopify_image_localizer/version.py
RELEASE_VERSION = "<next-version>"
```

版本号提交也必须进入 `origin/master`。不要在打包机上临时改 `version.py` 后直接打包。

### 3. 运行打包前测试

至少运行：

```bash
pytest tests/test_shopify_image_localizer_build_exe.py tests/test_shopify_image_localizer_domains.py tests/test_shopify_image_localizer_batch_cdp.py tests/test_shopify_image_localizer_gui.py -q
```

如果改了发布入口、下载入口或 release JSON：

```bash
pytest tests/test_shopify_image_localizer_release_web.py tests/test_media_pages_service.py -q
```

### 4. Linux 服务器打包发布（默认）

在普通主 checkout 上执行：

```bash
bash scripts/build_shopify_image_localizer_wine.sh \
  --release-standard-read \
  --version <next-version> \
  --release-note "<本次变更摘要>"
```

脚本职责：

- 校验当前目录是普通 `master` checkout，且 `HEAD == origin/master`。
- 校验 `version.py` 与 `--version` 一致。
- 校验 Wine、Xvfb、Windows Python 3.12.10+。
- 注入 `SHOPIFY_IMAGE_LOCALIZER_API_KEY`，但不打印 key。
- 生成 EXE 与 portable zip。
- 校验 zip 内运行配置。
- 复制 zip 到 `/opt/autovideosrt/web/static/downloads/tools/`。
- 写 `system_settings.shopify_image_localizer_release`。
- HTTP 探测静态 zip 可访问。

脚本不会重启 web 服务，不会 commit，不会 push。

### 5. Windows 本机 fallback 打包

只有 Linux/Wine 环境不可用时才走 Windows fallback。必须在普通主 checkout `master` 上执行：

```powershell
$env:SHOPIFY_IMAGE_LOCALIZER_API_KEY = "<从服务器 openapi_materials 配置读取的 key>"
python -m tools.shopify_image_localizer.build_exe `
  --release-standard-read `
  --version <next-version>
```

Windows fallback 生成 zip 后，还需要手工上传 zip、写 release JSON、做 HTTP 与真机验收。不要直接替换旧 zip。

## 必要配置

发布包根目录必须包含：

- `shopify_image_localizer_config.json`
- `shopify_image_localizer_default_config.json`
- `release_version.txt`
- `release_manifest.json`
- `run_shopify_image_localizer.bat`
- `ShopifyImageLocalizer_<version>.exe`

配置要求：

- 两份 JSON 的 `api_key` 必须相同、非空、非占位。
- 禁止 `demo-key`、`changeme`、`change-me`、`your-api-key` 进入包。
- `browser_user_data_dir` 必须是 `C:\chrome-shopify-image`。
- JSON 必须 UTF-8 无 BOM。
- `release_manifest.json` 必须记录本次 `source_commit`，用于追溯发布包来自哪个 `master` 提交。

复发症状记忆（2026-05-22）：如果新包启动后点击“开始替换”再次弹出“高级设置里的 OpenAPI Key 和 Chrome 用户目录不能为空”，先按发布包配置事故处理。不要要求用户临时手填高级设置来绕过；必须解压线上 zip 和用户实际运行目录，核对 `shopify_image_localizer_config.json`、`shopify_image_localizer_default_config.json` 是否都存在，`api_key` / `browser_user_data_dir` 是否非空且一致、JSON 是否无 BOM、zip 是否是在修复后重新生成。只修发布目录不重建 zip 会让问题下次打包继续复发。

服务端鉴权校验只能使用受保护接口：

```bash
POST http://172.16.254.106/openapi/medias/shopify-image-localizer/bootstrap
```

用发布包 config 的 `X-API-Key` 必须 HTTP 200；用 `demo-key` 必须 HTTP 401。`languages`、`domains` 是公开接口，不能当 key 验证。

## 产物验收

发布完成后必须做这些检查：

1. 下载 URL 返回 HTTP 200 或 206。
2. 解压 zip，确认：
   - `release_version.txt` 等于 `<next-version>`。
   - `release_manifest.json.source_commit` 等于 `git rev-parse origin/master`。
   - 两份 config 非空、无占位 key。
3. Windows 真机启动 EXE，窗口标题显示新版本。
4. 对本次改动做可视或行为验收。例如 GUI 文案/字号改动，必须直接看新 EXE 窗口，而不是只看源码测试。
5. 登录相关链路验收时只允许用户自己登录、选择店铺、点击“已登录”；不要替用户输入商品 ID 或点击“开始替换”，除非用户明确要求。

## 事故复盘规则

遇到“新版本没有应用旧修复”时，按这个顺序查：

1. `git merge-base --is-ancestor <fix_commit> origin/master`
2. `git show origin/master:<changed-file>` 是否包含修复。
3. 解压线上 zip，读取 `release_manifest.json.source_commit`。
4. 如果 manifest 的 commit 不是当前 `origin/master`，说明发布用错 checkout。
5. 如果 manifest 正确但行为不对，再查 PyInstaller hiddenimports、运行时配置或用户是否启动了旧目录旧 EXE。

## 禁止事项

- 禁止在任何 worktree 或非 master 分支打包。
- 禁止未提交改 `version.py` 后打包。
- 禁止复用旧 zip 文件名。
- 禁止只修目录里的 config，不重建 zip。
- 禁止把生产 key 写进源码、文档、测试或仓库 JSON。
- 禁止用 `wine ./ShopifyImageLocalizer.exe` 当最终 smoke；Wine GUI smoke 不能代表 Windows 真机。
- 禁止发布脚本重启生产服务；EXE 静态包发布不需要重启 web。
