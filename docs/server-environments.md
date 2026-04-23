# 服务器环境说明

最后更新：2026-04-23

本文记录 `AutoVideoSrtLocal` 当前线上环境和测试环境的实际部署位置。后续测试、联调、发布都以 `172.30.254.14` 服务器为准，不再在 Windows 开发机本地安装或启动 MySQL、Gunicorn、systemd 等运行服务。

## 连接信息

- 服务器地址：`172.30.254.14`
- SSH 用户：`root`
- SSH key：`C:\Users\admin\.ssh\CC.pem`
- 连接示例：`ssh -i C:\Users\admin\.ssh\CC.pem root@172.30.254.14`
- 敏感配置：以服务器上的 `.env`、密钥文件和系统服务为准，不写入仓库文档。

## 线上环境

- 访问地址：`http://172.30.254.14/`
- 监听端口：`80`
- 应用目录：`/opt/autovideosrt`
- systemd 服务：`autovideosrt.service`
- 服务文件：`/etc/systemd/system/autovideosrt.service`
- 启动工作目录：`/opt/autovideosrt`
- 启动命令：`/opt/autovideosrt/venv/bin/gunicorn --config /opt/autovideosrt/deploy/gunicorn.conf.py main:app`
- Gunicorn bind：`0.0.0.0:80`
- Gunicorn 运行方式：`gthread`，`workers=1`，线上服务环境变量设置 `AUTOVIDEOSRT_GUNICORN_THREADS=32`
- 当前代码远程：`origin = https://github.com/jinghuaswsx/AutoVideoSrtLocal.git`
- 旧仓库参考远程：`server-origin = https://github.com/jinghuaswsx/AutoVideoSrt.git`
- 数据库：服务器本机 MySQL，`DB_HOST=127.0.0.1`，`DB_PORT=3306`，`DB_NAME=auto_video`
- 数据目录：`UPLOAD_DIR=/opt/autovideosrt/uploads`，软链到 `/data/autovideosrt/uploads`
- 输出目录：`OUTPUT_DIR=/opt/autovideosrt/output`，软链到 `/data/autovideosrt/output`

常用只读检查：

```bash
systemctl status autovideosrt --no-pager -l
ss -ltnp | grep ':80\b'
git -C /opt/autovideosrt status --short --branch
```

线上发布时只操作线上目录和线上服务：

```bash
cd /opt/autovideosrt
git pull
systemctl restart autovideosrt
systemctl status autovideosrt --no-pager -l
```

## 测试环境

- 访问地址：`http://172.30.254.14:8080/`
- 监听端口：`8080`
- 应用目录：`/opt/autovideosrt-test`
- systemd 服务：`autovideosrt-test.service`
- 服务文件：`/etc/systemd/system/autovideosrt-test.service`
- 启动工作目录：`/opt/autovideosrt-test`
- 启动命令：`/opt/autovideosrt/venv/bin/gunicorn --config /opt/autovideosrt-test/deploy/gunicorn.conf.py main:app`
- Gunicorn bind：`0.0.0.0:8080`
- Gunicorn 运行方式：`gthread`，`workers=1`，测试服务环境变量设置 `AUTOVIDEOSRT_GUNICORN_THREADS=16`
- 当前代码远程：`origin = https://github.com/jinghuaswsx/AutoVideoSrtLocal.git`
- 数据库：服务器本机 MySQL，`DB_HOST=127.0.0.1`，`DB_PORT=3306`，`DB_NAME=auto_video_test`
- 数据目录：`UPLOAD_DIR=/data/autovideosrt-test/uploads`
- 输出目录：`OUTPUT_DIR=/data/autovideosrt-test/output`

常用只读检查：

```bash
systemctl status autovideosrt-test --no-pager -l
ss -ltnp | grep ':8080\b'
git -C /opt/autovideosrt-test status --short --branch
```

测试联调时只操作测试目录和测试服务：

```bash
cd /opt/autovideosrt-test
git pull
systemctl restart autovideosrt-test
systemctl status autovideosrt-test --no-pager -l
```

## 强制工作规则

- 本项目的功能验证、页面验证、接口验证、数据库验证默认走测试环境 `http://172.30.254.14:8080/`。
- 需要发布或验证线上行为时，才操作线上环境 `http://172.30.254.14/`。
- 不要在 Windows 开发机本地安装、初始化、启动或依赖 MySQL；项目数据库以服务器 MySQL 为准。
- 不要在 Windows 开发机本地长期启动项目 Web 服务来替代测试环境；需要联调时使用测试环境服务。
- 不要为了测试随意改线上目录或重启线上服务；先在测试环境验证，用户明确要求发布线上时再动线上。
- 两个环境共用虚拟环境路径 `/opt/autovideosrt/venv`。如需安装或升级 Python 依赖，要先评估是否会影响线上环境。
- 服务器 `.env` 中包含密钥和密码，排查时只读取必要的非敏感键；不要把敏感值写入仓库。

## 本次核对结果

2026-04-23 已通过 SSH 只读核对：

- `ss -ltnp` 显示 `80` 和 `8080` 均由 `gunicorn` 监听。
- `systemctl show` 显示 `80` 对应 `autovideosrt.service`，`8080` 对应 `autovideosrt-test.service`。
- `curl` 连通性检查显示线上入口和测试入口均返回 `302`，说明 Web 服务可达并进入应用登录/跳转流程。
- `mysql.service` 在服务器上为 `active (running)`，监听 `0.0.0.0:3306`。
