# 2026-04-22 本地服务器迁移验收清单

适用分支：`codex/local-server-migration`

目标机器：`172.30.254.14`

目标结论：确认 `AutoVideoSrtLocal` 已具备在 `172.30.254.14` 上作为唯一正式生产入口运行的条件；运行时以 `systemd + 本地 MySQL + 本地文件存储` 为主，只有必须公网回拉的链路继续使用 TOS；旧远程服务器保持不动，仅保留为参考和应急回退入口。

## 0. 验收边界

本清单按四大块执行：

1. 基线
2. 主链路真跑
3. 重点模块 smoke
4. 旁路服务

执行原则：

- 先跑测试，再上机器做服务级验收，再做人工真跑。
- 正式验收一律以 `http://172.30.254.14/` 为入口，不使用旧远程地址，不通过临时端口，不通过反向代理假设。
- 验收期间旧远程服务器保持不动：不删服务、不改库、不做覆盖式发布，只允许查看或在需要时作为人工回退入口。
- 需要公网回拉的链路仅验证“按需可用”，不把 TOS 当作默认主存储。
- 每一大块都要记录“执行人 / 时间 / 结果 / 证据位置 / 待跟进问题”。

建议验收记录模板：

| 项目 | 内容 |
| --- | --- |
| 执行人 |  |
| 执行时间 |  |
| 目标机器 | `172.30.254.14` |
| 结果 | 通过 / 阻塞 / 部分通过 |
| 证据 | 截图、命令输出、日志片段、任务 ID |
| 备注 |  |

## 1. 基线

本块目标：确认代码、服务、数据库、目录、入口约束全部对齐本地生产服务器，不带着“旧远程依赖”进入真跑。

### 1.1 测试基线

先在当前 worktree 跑聚焦回归，作为进入服务级验收的门槛：

```bash
pytest tests/test_autopush_settings.py tests/test_config.py tests/test_web_routes.py tests/test_multi_translate_routes.py tests/test_subtitle_removal_routes.py tests/test_medias_routes.py tests/test_image_translate_routes.py tests/test_cleanup.py tests/test_task_restart.py tests/test_pipeline_runner.py tests/test_local_storage_migration.py -q
```

通过标准：

- 命令退出码为 `0`
- 不允许新增失败项
- 如果存在已知 baseline 失败，必须单独列出并明确说明“与本次迁移是否相关”

记录项：

- pytest 命令
- 总用例数
- 失败数
- 如有失败，对应测试名和归因

### 1.2 systemd 基线

在 `172.30.254.14` 上确认正式服务由 `systemd` 托管：

```bash
systemctl daemon-reload
systemctl restart autovideosrt
systemctl status autovideosrt --no-pager
journalctl -u autovideosrt -n 100 --no-pager
```

通过标准：

- `autovideosrt` 为 `active (running)`
- 最近 100 行日志中没有持续刷新的 traceback
- 重启后服务可以稳定存活至少 2 分钟

重点观察：

- Gunicorn 启动参数是否符合迁移约束
- 是否仍引用旧远程机器地址或旧端口
- 是否有 `.env` 缺失、数据库连接失败、目录不存在等启动错误

### 1.3 本地 IP 直连基线

确认正式访问入口就是本地服务器 IP，且不依赖旧远程入口：

```bash
ss -lntp | grep ":80"
curl -I http://127.0.0.1/
curl -I http://172.30.254.14/
```

通过标准：

- 有进程监听 `:80`
- `http://127.0.0.1/` 和 `http://172.30.254.14/` 返回 `200` 或登录态下可接受的 `302`
- 不需要手工带 `:8888` 一类历史端口

补充检查：

- 浏览器从同网段 / VPN 网络直接访问 `http://172.30.254.14/`
- 页面资源加载正常，没有因为错误 base URL 指向旧远程机器

### 1.4 MySQL 基线

确认运行时只使用本地 MySQL，不再依赖远程数据库：

```bash
mysql -h 127.0.0.1 -P 3306 -u autovideosrt -p -e "SHOW DATABASES;"
mysql -h 127.0.0.1 -P 3306 -u autovideosrt -p -e "SHOW TABLES FROM auto_video;"
mysql -h 127.0.0.1 -P 3306 -u autovideosrt -p -e "SELECT COUNT(*) AS total FROM auto_video.projects;"
```

通过标准：

- 可以连通本地 `127.0.0.1:3306`
- `auto_video` 存在且表结构可读
- 关键业务表可查询

重点观察：

- 应用日志中不应出现连接旧远程数据库地址的痕迹
- 如 `.env` 中有数据库主机配置，应明确为本地地址

### 1.5 本地文件存储基线

确认本地文件目录存在、可写、且软链或目录约定已对齐：

```bash
ls -ld /opt/autovideosrt
ls -ld /opt/autovideosrt/uploads
ls -ld /opt/autovideosrt/output
ls -ld /data/autovideosrt/uploads
ls -ld /data/autovideosrt/output
python scripts/verify_local_storage_references.py
```

通过标准：

- `uploads`、`output` 目录存在
- 目录权限满足运行用户读写需求
- 本地引用校验脚本通过，或明确列出缺失项且确认不阻塞首轮验收

重点观察：

- `uploads` / `output` 是否指向预期的数据盘目录
- 不允许把“主文件仍在 TOS”误判为迁移完成

### 1.6 旧远程保持不动确认

本项不是要去改旧远程，而是要确认本轮验收没有把旧环境卷入联动修改。

检查项：

- 本次发布、重启、验收命令只在 `172.30.254.14` 上执行
- 未对旧远程服务器执行 `git pull`、数据库迁移、服务重启、目录清理
- 未把旧远程数据库作为运行依赖或兜底依赖

通过标准：

- 验收记录里能明确说明“旧远程仅保留，不参与本轮正式写入”

## 2. 主链路真跑

本块目标：在 `172.30.254.14` 上完成至少一条主翻译任务的真实闭环，验证从上传到下载的完整链路。

### 2.1 验收样本要求

建议样本：

- 时长 `15-30s`
- 单人讲话
- 音频清晰
- 源语言明确
- 尽量使用可重复验证的视频

不建议首轮就使用：

- 超长视频
- 多人对话
- 背景乐很重的视频
- 需要大量人工干预的素材

### 2.2 任务创建与本地上传

执行步骤：

1. 浏览器打开 `http://172.30.254.14/`
2. 登录管理员账号
3. 新建一个主翻译任务
4. 直接通过页面上传本地视频

通过标准：

- 上传成功
- 任务创建成功
- 服务端没有要求走旧的通用 TOS bootstrap 流程
- 任务创建后能在本地任务列表看到该任务

服务级补充验证：

- 上传后在本地 `uploads` 目录能看到对应源文件
- 数据库中该任务已写入本地相关状态
- 如状态里保留 `source_tos_key` 等历史字段，不应影响“本地优先”语义

建议记录：

- 任务 ID
- 上传文件名
- 本地落盘路径

### 2.3 主处理链路真跑

执行步骤：

1. 让任务从创建态进入处理态
2. 持续观察处理进度
3. 确认任务依次经过主链路关键阶段

关键阶段：

- 上传 / 入库
- 抽取
- ASR
- 翻译
- TTS
- 字幕
- 合成
- 导出

通过标准：

- 任务能够实际跑到 `export` 或项目定义的完成态
- 页面上可看到进度更新
- 日志里没有阻断性异常
- WebSocket 或轮询进度在页面可见

建议旁证：

```bash
journalctl -u autovideosrt -n 200 --no-pager
mysql -h 127.0.0.1 -P 3306 -u autovideosrt -p -e "SELECT id,status,updated_at FROM auto_video.projects ORDER BY updated_at DESC LIMIT 5;"
```

### 2.4 结果预览、下载、重跑

执行步骤：

1. 打开任务详情页
2. 预览关键结果
3. 下载最终产物
4. 触发至少一次重跑或重新处理

通过标准：

- 详情页可以正常打开
- 预览可用
- 下载返回本地生成结果，不要求跳转 TOS 才能取回主产物
- 重跑后任务可以再次推进，不因源文件缺失失败

服务级补充验证：

- 本地产物目录存在对应任务目录
- 重跑不会把本地源视频误清理
- 清理逻辑不会因为历史 `tos` 元数据把本地文件删掉

### 2.5 主链路通过判定

本块判定为通过，至少需要同时满足：

- 同一条真实任务完成“上传 -> 处理 -> 预览 -> 下载 -> 重跑”
- 服务、数据库、磁盘三侧证据一致
- 没有依赖旧远程服务器完成中间步骤

## 3. 重点模块 smoke

本块目标：整仓主要模块至少完成一次“可进入页面 / 可提交参数 / 错误反馈合理 / 关键状态可读”的冒烟验收。

执行方法：

- 优先在 `172.30.254.14` 浏览器人工走查
- 必要时配合日志、数据库、目录查看
- 对依赖外部服务的模块，以“参数可提交、链路可达、报错可解释”为首轮通过标准

### 3.1 模块清单

以下模块至少各做一次 smoke：

- `de_translate`
- `fr_translate`
- `multi_translate`
- `bulk_translate`
- `copywriting`
- `text_translate`
- `title_translate`
- `video_review`
- `video_creation`
- `subtitle_removal`
- `translate_lab`
- `image_translate`
- `medias`
- `openapi_materials`
- `pushes`
- `link_check`
- `voice_library`
- `prompt_library`
- `settings`
- `admin_prompts`
- `admin_usage`
- `admin_ai_billing`
- `auth`

### 3.2 smoke 统一通过标准

每个模块至少满足以下四项中的三项，且不能出现阻断性错误：

- 页面能打开
- 基础数据能加载
- 表单或参数可以提交
- 失败时能看到明确错误反馈，而不是空白页或 500

如模块具备任务型特征，再加一项：

- 至少成功创建一条任务或成功发出一次请求

### 3.3 必做重点项

以下模块建议提高标准，不只看“能打开”：

`subtitle_removal`

- 创建任务
- 提交一次真实或准真实处理请求
- 验证只在需要公网回拉时才补 TOS 交换层能力

`medias`

- 上传一个素材
- 确认素材写入本地文件存储
- 页面可再次读取该素材

`image_translate`

- 上传图片
- 确认源图解析优先走本地素材存储
- 页面返回的成功或失败信息可解释

`settings`

- 页面可打开
- 关键配置可读
- 保存动作不会报错

`auth`

- 登录可用
- 登出可用
- 未登录访问受限页面时行为正确

### 3.4 smoke 记录模板

建议按模块逐项记录：

| 模块 | 页面打开 | 提交动作 | 结果 | 证据 |
| --- | --- | --- | --- | --- |
| `de_translate` | 通过 / 失败 | 通过 / 失败 | 通过 / 阻塞 |  |

## 4. 旁路服务

本块目标：确认不属于主翻译链路、但会影响整体迁移可用性的服务或配套能力处于可接受状态。

### 4.1 AutoPush

检查项：

- 默认上游地址是否指向 `http://172.30.254.14`
- 基础配置读取正常
- 与本地服务通信正常

建议执行：

```bash
pytest tests/test_autopush_settings.py -q
```

通过标准：

- 配置测试通过
- 不再默认指向旧远程地址

### 4.2 Link Check Desktop

检查项：

- 可启动
- 关键测试可跑
- 与本次迁移无直接回归冲突

建议执行：

```bash
pytest tests/test_appcore_medias_link_check_bootstrap.py tests/test_link_check_bootstrap_routes.py tests/test_link_check_gemini.py tests/test_link_check_same_image.py tests/test_link_check_desktop_storage.py tests/test_link_check_desktop_bootstrap_api.py tests/test_link_check_desktop_controller.py tests/test_link_check_desktop_gui.py -q
```

通过标准：

- 聚焦测试通过
- 若失败，需明确与本次迁移是否相关

### 4.3 旁路公网交换能力

本项只验证“该用时能用”，不是验证“所有文件都继续上 TOS”。

重点检查：

- ASR 所需公网回拉是否仍可用
- `subtitle_removal` 所需公网 URL 是否能按需生成
- `video_creation` 一类依赖公网资源的链路是否仍可达

通过标准：

- 按需生成的公网 URL 可用
- 不会要求所有主链路文件默认先上传 TOS

### 4.4 运维可观察性

至少确认以下运维入口可用：

- `systemctl status autovideosrt --no-pager`
- `journalctl -u autovideosrt -n 200 --no-pager`
- MySQL 本地连通检查
- 磁盘目录可观察

可选补充：

- Cockpit 或现有监控入口可打开
- 可以看到 CPU、内存、磁盘、服务状态

## 5. 正式迁移验收顺序

建议按以下顺序执行，避免边测边改导致结论失真：

1. 在 worktree 跑测试基线
2. 将当前分支部署到 `172.30.254.14`
3. 做基线验收
4. 做主链路真跑
5. 做重点模块 smoke
6. 做旁路服务验收
7. 汇总阻塞项并决定是否允许正式切换

## 6. 切换与回退口径

### 6.1 允许正式切换的条件

至少同时满足以下条件：

- 基线通过
- 主链路真跑通过
- 重点模块 smoke 无 P0 / P1 阻塞
- 旁路服务处于可接受状态
- 团队确认正式入口统一为 `http://172.30.254.14/`

### 6.2 回退口径

若本地服务器出现阻断性故障：

1. 立即停止继续把正式流量导向 `172.30.254.14`
2. 恢复团队使用旧远程入口
3. 保留本地机器现场，不做破坏性清理
4. 导出日志、任务 ID、数据库证据、目录证据
5. 回到当前迁移分支继续修复

明确限制：

- 回退不等于去修改旧远程环境
- 回退时旧远程环境仍保持“原样接管”，不是本轮验收中的新发布目标

## 7. 最终验收结论模板

### 7.1 总结论

- 结论：通过 / 有条件通过 / 不通过
- 验收机器：`172.30.254.14`
- 验收入口：`http://172.30.254.14/`
- 验收时间： 
- 验收人： 

### 7.2 四大块结果

| 大块 | 结果 | 备注 |
| --- | --- | --- |
| 基线 | 通过 / 失败 |  |
| 主链路真跑 | 通过 / 失败 |  |
| 重点模块 smoke | 通过 / 部分通过 / 失败 |  |
| 旁路服务 | 通过 / 部分通过 / 失败 |  |

### 7.3 阻塞问题

| 编号 | 问题 | 影响范围 | 是否阻塞切换 | 备注 |
| --- | --- | --- | --- | --- |

### 7.4 证据索引

- pytest 输出：
- `systemctl status`：
- `journalctl`：
- MySQL 查询：
- 本地目录检查：
- 主链路任务 ID：
- smoke 模块记录：

