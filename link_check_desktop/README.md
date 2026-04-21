# Link Check Desktop

这是一个纯客户端 Windows 桌面子项目。

- 本地运行在 Windows 机器上
- 通过可见的 Edge 浏览器真实访问目标页
- 只通过服务端 OpenAPI 交互，不依赖本地 MySQL 或服务端 Python 模块
- 任务结果落盘到 exe 同目录 `img/<product_id>-<YYYYMMDDHHMMSS>/`

## 开发运行

```bash
python -m link_check_desktop.main
```

Windows 一键启动：

```bat
run_link_check_desktop.bat
```

首次双击会自动：

1. 创建本地运行虚拟环境 `.venv_link_check_runtime`
2. 安装 `link_check_desktop/requirements.txt`
3. 启动桌面 GUI

这个 `bat` 版本更适合先联调和先跑通，不要求先打 exe。

## 打包 exe

推荐直接执行：

```bash
python link_check_desktop/build_exe.py
```

或手动执行：

```bash
pyinstaller --noconfirm link_check_desktop/packaging/link_check_desktop.spec
```

打包产物位于：

```text
dist/LinkCheckDesktop/
  LinkCheckDesktop.exe
  ...
```

当前采用的是 `PyInstaller onedir` 分发。
为了保证 Playwright 和依赖动态库稳定，交付时应拷贝整个 `dist/LinkCheckDesktop/` 目录，而不只是单独拷贝 `exe` 文件。

## 换机器运行

目标机器需要满足：

1. Windows 环境
2. 可访问服务端 OpenAPI
3. 已安装 Microsoft Edge
4. 已安装 Python 3.11+（`bat` 版本需要；`exe` 版本不需要）

程序会把运行配置保存到 exe 同目录：

```text
link_check_desktop_config.json
```

其中包含：

```json
{
  "base_url": "http://14.103.220.208:8888",
  "api_key": "autovideosrt-materials-openapi"
}
```

首版也可以直接在 GUI 中修改服务端 API 地址和 OpenAPI Key，保存后下次启动会自动复用。
