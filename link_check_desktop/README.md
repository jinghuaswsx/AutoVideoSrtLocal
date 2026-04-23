# Link Check Desktop

这是一个运行在 Windows 本机上的链接检查桌面客户端。

能力范围：

1. 输入产品页链接
2. 调用服务端 OpenAPI 识别产品和语种，并拉取参考图
3. 通过可见浏览器访问目标页并下载网站图片
4. 执行参考图匹配、二值快检、同图大模型判断和语言分析
5. 在本地任务目录生成静态结果页 `report.html`

## 开发运行

```bash
python -m link_check_desktop.main
```

Windows 一键启动：

```bat
run_link_check_desktop.bat
```

## 输出目录

每次运行都会在程序根目录生成：

```text
img/<product_id>-<YYYYMMDDHHMMSS>/
```

目录中至少包含：

```text
report.html
task.json
page.html
page_info.json
reference/
site/
compare/result.json
```

其中：

1. `report.html` 是本地静态结果页，任务完成后会自动用默认浏览器打开
2. `reference/` 保存服务端素材库下载下来的参考图
3. `site/` 保存从目标页抓取并下载的图片
4. `compare/result.json` 保存逐图分析结果

## 打包 exe

推荐直接执行：

```bash
python link_check_desktop/build_exe.py
```

打包产物位于：

```text
dist/LinkCheckDesktop/
```

同时会生成便携压缩包：

```text
dist/LinkCheckDesktop-portable.zip
```

## 换机器运行

绿色版运行要求：

1. Windows 环境
2. 能访问服务端 OpenAPI
3. 使用 `dist/LinkCheckDesktop/` 整个目录，或直接分发 `LinkCheckDesktop-portable.zip`

运行配置保存在：

```text
link_check_desktop_config.json
```

默认内容为：

```json
{
  "base_url": "http://172.30.254.14",
  "api_key": "autovideosrt-materials-openapi"
}
```
