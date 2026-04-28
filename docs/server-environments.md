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

## TOS 灾备存储

- 专用桶：`autovideosrtlocal`。
- 文件 Object Key 映射：`FILES/{TOS_BACKUP_ENV}/{本地绝对路径}`。例如 `/data/autovideosrt-test/output/media_store/1/items/a.mp4` 对应 `FILES/test/data/autovideosrt-test/output/media_store/1/items/a.mp4`。
- 数据库 dump 目录：`DB/{TOS_BACKUP_ENV}/{YYYY-MM-DD}/`，每天凌晨 01:00 生成前一天日期的 MySQL 全量 dump，保留 7 天。
- 系统级开关：`FILE_STORAGE_MODE=local_primary` 表示业务读写本地并补齐 TOS；`FILE_STORAGE_MODE=tos_primary` 表示优先以 TOS 为准，本地缺文件时自动拉回。
- 手动执行：`python scripts/tos_backup_sync.py` 全量执行；`--files-only` 只同步受保护文件；`--db-only` 只生成/上传数据库 dump 并清理过期 dump。
- 受保护文件范围：项目上传原始视频、素材管理中的商品详情图、视频素材、视频封面、各小语种视频素材/封面/详情图，以及 raw source 的小语种封面。

服务器启用 TUN 模式时，TOS 必须走 DIRECT，不要走代理。代码层会自动补 `NO_PROXY/no_proxy`，代理配置层仍需加直连规则：

```yaml
rules:
  - DOMAIN-SUFFIX,volces.com,DIRECT
  - DOMAIN-SUFFIX,ivolces.com,DIRECT

fake-ip-filter:
  - "*.volces.com"
  - "*.ivolces.com"
```

如果使用的是 Mihomo/Clash 的 rule-providers，把这两条 `DOMAIN-SUFFIX` 放在代理规则之前，确保 `tos-cn-shanghai.volces.com` 和 `tos-cn-shanghai.ivolces.com` 不消耗代理流量。

## 本次核对结果

2026-04-23 已通过 SSH 只读核对：

- `ss -ltnp` 显示 `80` 和 `8080` 均由 `gunicorn` 监听。
- `systemctl show` 显示 `80` 对应 `autovideosrt.service`，`8080` 对应 `autovideosrt-test.service`。
- `curl` 连通性检查显示线上入口和测试入口均返回 `302`，说明 Web 服务可达并进入应用登录/跳转流程。
- `mysql.service` 在服务器上为 `active (running)`，监听 `0.0.0.0:3306`。
