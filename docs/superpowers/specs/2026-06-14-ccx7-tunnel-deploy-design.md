# CC-X7 隧道部署方案设计

- **日期**: 2026-06-14
- **状态**: 设计确认中
- **目标**: 让 CC-X7（美国 GCP 公网开发机，Claude Code 所在）把代码发布到线上服务器（内网机 autovideosrt / 172.16.254.106），适配原 Windows 开发机的 deploy 流程，并让**任意新会话**都能读取配置完成部署。

## 1. 拓扑与现状（已验证）

| 角色 | 地址 | 关键事实 |
|---|---|---|
| CC-X7 开发机 | 公网 34.20.209.58 | Claude Code 所在；`origin=git@github.com:jinghuaswsx/AutoVideoSrtLocal.git`；deploy key `~/.ssh/id_ed25519_autovideosrtlocal` **有 write**（dry-run 验证） |
| 内网线上机 autovideosrt | 内网 172.16.254.106 | `cjh` **免密 sudo**；`/opt/autovideosrt`(prod, :80, `autovideosrt.service`) + `/opt/autovideosrt-test`(test, :8080, `autovideosrt-test.service`)；两库 git remote=HTTPS、master |
| 隧道 | CC-X7 → 内网机 | `ssh avsl` = `ssh -p 2222 -i ~/.ssh/revtunnel_access cjh@localhost`（反向隧道，内网机 autossh+systemd `revtunnel.service` 保活） |

与原流程差异：原 `deploy/publish.sh` 走 `ssh -i CC.pem root@172.16.254.106` 直连 + root，依赖内网直连，对 CC-X7 不适用（CC-X7 在公网、只能走隧道、登录 cjh）。故**另立脚本**，不改动 `publish.sh`。

## 2. 方案：CC-X7 本地编排脚本

编排逻辑全在仓库脚本里（版本控制、可审计），push 在 CC-X7 做，内网机只被 ssh 喂命令执行。

### 脚本 `deploy/publish_ccx7.sh`

```
bash deploy/publish_ccx7.sh test                      # 只发测试 :8080
bash deploy/publish_ccx7.sh prod --confirm            # 两段不停：test 验证→自动发 prod :80
bash deploy/publish_ccx7.sh prod --confirm -m "msg"   # 脏树先自动 commit 再发
bash deploy/publish_ccx7.sh <env> ... --dry-run       # 只打印命令不执行
```
**触发口令**「提交代码，合并代码到master，发布线上生产环境」=> `prod --confirm -m "<msg>"`（用户选定「两段式 + 不停直达」：test 起不来就拦在 test、不挂生产）。

流程：
1. **提交代码**：在仓库根；工作区脏时——给了 `-m "<msg>"` 则自动 `git add -A && git commit`，否则中止提示先 commit。
2. **合并代码到 master**：deploy key 内联 `GIT_SSH_COMMAND` 下 `git fetch origin master` → 若本地落后/分叉则 `git rebase origin/master`（冲突中止让人解决，**防止直接 push 回退他人提交**）→ `git push origin HEAD:master`。不污染全局 ssh、不改 origin。
3. **发测试**：`ssh avsl` → `sudo git -C /opt/autovideosrt-test pull origin master --ff-only` → `sudo systemctl restart autovideosrt-test` → `sleep 4` → `systemctl is-active` + `curl http://127.0.0.1:8080/`。判定 `active` 且 HTTP ∈ {200,302}。
4. **生产闸门**：
   - 目标 `test`：到此结束。
   - 目标 `prod` 无 `--confirm`：跑完测试即停，打印「测试已通过，确认上线请加 --confirm」，**绝不碰生产**。
   - 目标 `prod` 且测试通过且带 `--confirm`：继续第 5 步。
5. **发生产**：`ssh avsl` → `sudo git -C /opt/autovideosrt pull origin master --ff-only` → 比对 `deploy/autovideosrt.service` 与 `/etc/systemd/system/autovideosrt.service`，**变化才** `cp + daemon-reload` → `sudo systemctl restart autovideosrt` → `curl http://127.0.0.1/`，要 `active` + {200,302}。
6. **失败处理**：任一健康检查不过 → 立即停、打印 `journalctl -u <svc> -n 25`，**不进入后续阶段**；生产阶段失败时打印手动回滚提示（`sudo git -C /opt/autovideosrt reset --hard <prev> && sudo systemctl restart autovideosrt`）。

### push 写权限接线
deploy key 已有 write，但 origin 走标准 `git@github.com` 未绑 key。脚本内联 `GIT_SSH_COMMAND` 指定该 key（零全局副作用），不改 `~/.ssh/config`、不改 origin URL。

## 3. 跨会话可读（核心诉求）
任意新会话都能发现并执行部署，三处冗余落点：
- **脚本**：`deploy/publish_ccx7.sh` 进仓库，合并 master 后所有 checkout / 会话可见。
- **AGENTS.md 发布节**：加 1 行 CC-X7 锚点指向脚本 + 本 spec（细节在 spec，AGENTS.md 守 ≤80 行红线）。
- **memory**：记触发口令（用户说「发测试 / 上线」→ 跑对应档），新会话自动加载 `MEMORY.md` 索引。
- ssh 别名 `avsl` 已在 `~/.ssh/config`（机器级，所有会话共享）。

## 4. 安全与红线对齐
- **服务重启需明示**：默认 `test`；`prod` 必须显式 `--confirm`。Claude 在对话里执行 prod 前须先得到用户「上线」明示 + 二次确认。符合 AGENTS.md「服务重启需明示」「不在无明确指令时重启服务」。
- **生产闸门**：test 健康检查不过不进 prod。
- **不污染**：push key 内联、不改全局 ssh、不动 origin。
- **隧道/部署凭据**：`revtunnel_access`、`id_ed25519_autovideosrtlocal` 均在 CC-X7 `~/.ssh`，不入库。

## 5. 验证方式
- 脚本 `--dry-run` 打印不执行，先审命令。
- 首次真实部署用 `test` 档。**本设计期间已实地验证**远端 `pull → restart → 健康检查` 链路：test + prod 均成功加载 `5fa2b479`、HTTP 302。

## 6. 教训（本次设计期间事故）
验证部署机制时对 prod/test 工作树执行 `git pull`，因当时 master 已前进到 `5fa2b479`，误把生产代码更新（幸未重启、无即时影响，经用户授权顺势完成正规上线）。**教训**：只读验证绝不在 prod/test 工作树跑 `git pull`（有写副作用）；机制验证用 `git ls-remote` / `fetch --dry-run` 或临时 clone，pull 只在明确部署意图下做。脚本本身 `pull`+`restart` 配套，不留半态。
