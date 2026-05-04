# AutoVideoSrtLocal Claude Code Notes

- 与用户沟通一律使用中文。
- 当前仓库默认开发远程应为 `https://github.com/jinghuaswsx/AutoVideoSrtLocal.git`。
- 旧服务器版仓库 `https://github.com/jinghuaswsx/AutoVideoSrt.git` 暂时仅作迁移参考，不作为默认推送目标；如需保留，建议使用单独远程名，例如 `server-origin`。
- Follow the global workflow and installed-skill guidance from `C:\Users\admin\.claude\CLAUDE.md`.
- When this project gets real source files, update this file with exact run, test, lint, typecheck, and build commands.
- Prefer installed skills when relevant, especially `superpowers:*`, `claude-api`, `pdf`, `docx`, `pptx`, `xlsx`, `webapp-testing`, `frontend-design`, and `mcp-builder`.

## GitHub 凭据 / push 流程（本机）

本机已配 GitHub Personal Access Token，直接跑 `git push` / `git pull` 即可，**不要再问凭据、不要 `gh auth login`、不要让用户重新发 token**。

- 凭据存储位置：`~/.git-credentials`（`chmod 600`），通过 `git config --global credential.helper store` 持久化。
- 账号：`jinghuaswsx`；token 不过期；scope 为 `jinghuaswsx/AutoVideoSrtLocal` 的 Contents Read/Write。
- 直接用法：在主仓库或任何 worktree 跑 `git push origin <branch>`，git 自动从 `~/.git-credentials` 读 token，无交互。
- 安全约束：
  - 不要 `cat ~/.git-credentials`、不要把 token 出现在命令行参数里、不要把它写进任何 commit 或日志。
  - 不要把 token 拷到对话里（即便用户原始提供的那条消息已经在 transcript 里，也不要在新消息里复述）。
- 失效兜底：如果 push 报 403/401，先确认 `~/.git-credentials` 还在、文件权限是 600；只有当确认 token 被 revoke 时，才请用户重新签发并直接 `printf 'https://jinghuaswsx:NEW_TOKEN@github.com\n' > ~/.git-credentials && chmod 600 ~/.git-credentials` 覆盖更新。

## 发布到生产（172.30.254.14）

**唯一发布入口：** `bash deploy/publish.sh "<commit message>"`（commit message 仅当 working tree 有未提交改动时生效；干净状态下脚本只跑 push + 远端 pull + restart + 健康检查）。

- 脚本在 [deploy/publish.sh](deploy/publish.sh)，依赖 `~/.ssh/CC.pem` 内网 SSH key（已就位）。
- 服务器固定参数：`root@172.30.254.14:22`、项目目录 `/opt/autovideosrt`、systemd 服务 `autovideosrt.service`（gunicorn）。
- 脚本自动：本地 commit/push（如有变更）→ 远端 `git pull` → 同步 `deploy/autovideosrt.service` 到 `/etc/systemd/system/` 并 `daemon-reload`（如有 unit 文件变化）→ `systemctl restart autovideosrt` → `systemctl status` → `curl http://127.0.0.1/` 健康检查。
- **不要手写 `ssh root@172.30.254.14 ...`**：~/.ssh/config 没有 LocalServer alias，IP 直连密码也不通；必须走 publish.sh（它用 `-i ~/.ssh/CC.pem` 显式指定 key）。
- 发布后 systemd 启动会自动 apply 所有未登记的 SQL migration（参考全局 memory `deploy_migration_workflow`）。**不要手动跑 SQL**——除非同时 `INSERT INTO schema_migrations` 登记，否则启动器会重复执行报错。
- 用户没说 "发布" / "deploy" / "上线" 等明确字眼前，**不要主动跑 publish.sh**（CLAUDE.md 全局规则：未经许可禁止重启服务）。一旦用户授权（一次说"发布"），就一次性走完 publish.sh，不要中途再问"要不要 restart"。

## Shopify Image Localizer 发布打包

- 打包发布：`python -m tools.shopify_image_localizer.build_exe --version 1.0`。产物固定使用版本号后缀：目录 `dist/ShopifyImageLocalizer-1.0`，绿色包 `dist/ShopifyImageLocalizer-portable-1.0.zip`；同版本目录或 zip 已存在时脚本会报错退出，不要覆盖旧版本。后续发布 `2.0` 时改用 `--version 2.0`，必须保持 `1.0` 原样不动。
- 发布到素材管理页时，绿色包放服务器 `/opt/autovideosrt/web/static/downloads/tools/`，下载 URL 形如 `/static/downloads/tools/ShopifyImageLocalizer-portable-1.0.zip`。
- 素材管理页的“下载自动换图工具”按钮、当前版本号、发布时间必须读取数据库 `system_settings` 中 `shopify_image_localizer_release` 的 JSON，不要硬编码到前端模板。JSON 字段：`version`、`released_at`、`release_note`、`download_url`、`filename`。
- 每次发布新版本的固定顺序：先打包生成对应版本 zip；上传到服务器下载目录；写入/更新 `shopify_image_localizer_release` 数据库配置；再发布 Web 代码并做 HTTP 可达性检查。

## Shopify Image Localizer EZ/CDP 回归防护

- 已知事故：2026-04-25 调整“停止”按钮后，`开始替换` 会卡在 EZ Product Image Translate 页面，EZ iframe/图片数据不加载；点击“停止”后页面马上加载。核心原因是把 EZ 页面等待循环里的 Playwright `page.wait_for_timeout(...)` 改成了普通 Python 侧的 `cancellation.cancellable_sleep(...)`，导致 Playwright/CDP 同步 API 没有持续刷新页面事件和 frame 列表，`_wait_plugin_frame` 长时间看不到 iframe。
- 禁止回改：`tools/shopify_image_localizer/rpa/ez_cdp.py` 中凡是等待 EZ iframe、弹窗关闭、上传后 UI 刷新的地方，必须保留 Playwright 页面级等待（如 `page.wait_for_timeout(...)` 或 frame/page locator wait）。不要用 `time.sleep` 或 `cancellation.cancellable_sleep` 替代这些页面等待。需要支持停止时，只能在 Playwright 等待前后插入 `cancellation.throw_if_cancelled(...)`。
- `登录shopify店铺` 按钮不要跳 Translate & Adapt 应用页，也不要跳具体商品的 EZ 页。它应固定打开 Shopify 产品列表页 `https://admin.shopify.com/store/0ixug9-pv/products`，只用于恢复/确认店铺登录状态，避免预先打开应用页干扰后续 EZ 工作流。
- EZ 轮播图替换前必须判断每个 slot 是否已经有目标语言标签：语言名称来自 `run_product_cdp.LANGUAGE_LABELS`，例如 `de -> German`。如果 slot 已有目标语言标签，结果应为 `skipped`，不要点击 `Remove {language}`，不要删除后重新上传，避免浪费时间和引入失败。
- 修改这条链路后至少运行：`pytest tests/test_shopify_image_localizer_batch_cdp.py tests/test_shopify_image_localizer_gui.py -q`，并执行 Shopify Image Localizer 的语法自检。

---

# Frontend Design System — Ocean Blue Admin

本项目是一个**友好、清晰、规整**的管理后台。视觉基调：**深海蓝侧栏 + 白色主区 + 海洋蓝品牌色 + 大圆角卡片**。温润但不花哨，密集但不拥挤。

**硬性约束：全程零紫色。** 所有 hue 值限定在 `200–240`（cyan 到 pure blue）区间，禁止出现 `260+` 的蓝紫/靛蓝色调。拿不准时对照已有页面，不要引入异类风格。

## 1. Aesthetic Direction

**One-line brief**: Linear 的冷静 + Vercel Dashboard 的密度 + Stripe Dashboard 的蓝 + 飞书后台的中文亲和感。色调像清晨的海面——冷、干净、有层次。

**核心特征**
- 深海蓝侧栏（deep navy / slate-blue），主区纯白或极浅灰
- 海洋蓝作为主 accent（按钮、选中、链接），克制使用
- 青色（cyan）作为二级 accent，用于数据可视化和高亮
- 大圆角（8-12px 卡片，6-8px 按钮）
- 状态色降饱和：warning 暖黄、success 海藻绿、danger 珊瑚红

**禁用清单（严格执行）**
- 任何紫色：violet / indigo / purple / magenta / pink / lavender。OKLCH hue 必须在 200-240
- 紫蓝渐变、紫粉渐变、彩虹渐变
- Glassmorphism 毛玻璃作为主效果
- Neumorphism、重阴影、3D 拟物
- 大面积深色（除侧栏外，主区不用暗色模式作为默认）
- 过度动画、鼠标跟随光晕
- 居中英雄区 + 渐变 blob 的营销站套路
- 纯黑 `#000` / 纯白 `#fff` 作文字/背景

## 2. Design Tokens（OKLCH，必须走 CSS 变量，禁止硬编码）

```css
:root {
  /* ==================== Color — Ocean Blue ==================== */
  /* 所有 chroma 色的 hue 严格限定 200-240，绝不 > 245 */

  --bg:            oklch(99%  0.004 230);
  --bg-subtle:     oklch(97%  0.006 230);
  --bg-muted:      oklch(94%  0.010 230);
  --border:        oklch(91%  0.012 230);
  --border-strong: oklch(84%  0.015 230);
  --fg:            oklch(22%  0.020 235);
  --fg-muted:      oklch(48%  0.018 230);
  --fg-subtle:     oklch(62%  0.015 230);

  --sidebar-bg:        oklch(26%  0.055 235);
  --sidebar-bg-hover:  oklch(32%  0.065 235);
  --sidebar-bg-active: oklch(38%  0.085 230);
  --sidebar-fg:        oklch(96%  0.008 225);
  --sidebar-fg-muted:  oklch(72%  0.020 230);
  --sidebar-border:    oklch(34%  0.055 235);

  --accent:        oklch(56%  0.16  230);
  --accent-hover:  oklch(50%  0.17  230);
  --accent-active: oklch(45%  0.16  230);
  --accent-fg:     oklch(99%  0     0);
  --accent-subtle: oklch(94%  0.04  225);
  --accent-ring:   oklch(56%  0.16  230 / 0.22);

  --cyan:          oklch(62%  0.13  215);
  --cyan-subtle:   oklch(94%  0.04  215);

  --warning:       oklch(72%  0.14  80);
  --warning-bg:    oklch(96%  0.05  85);
  --warning-fg:    oklch(42%  0.10  60);
  --success:       oklch(62%  0.13  165);
  --success-bg:    oklch(95%  0.04  165);
  --success-fg:    oklch(38%  0.09  165);
  --danger:        oklch(58%  0.18  25);
  --danger-bg:     oklch(96%  0.04  25);
  --danger-fg:     oklch(42%  0.14  25);
  --info:          oklch(62%  0.12  230);
  --info-bg:       oklch(95%  0.04  230);

  --chart-1: oklch(56%  0.16  230);
  --chart-2: oklch(66%  0.14  215);
  --chart-3: oklch(72%  0.11  200);
  --chart-4: oklch(46%  0.15  235);
  --chart-5: oklch(80%  0.08  210);

  --font-sans: "Inter Tight", "Geist", -apple-system, BlinkMacSystemFont,
               "PingFang SC", "HarmonyOS Sans SC", "Microsoft YaHei",
               "Noto Sans SC", sans-serif;
  --font-mono: "JetBrains Mono", "Geist Mono", ui-monospace,
               "SF Mono", Consolas, monospace;

  --text-xs: 12px;  --text-sm: 13px;  --text-base: 14px;
  --text-md: 15px;  --text-lg: 18px;  --text-xl: 22px;  --text-2xl: 28px;

  --leading-tight: 1.3;  --leading: 1.55;  --leading-loose: 1.75;

  --space-1: 4px;   --space-2: 8px;   --space-3: 12px;
  --space-4: 16px;  --space-5: 20px;  --space-6: 24px;
  --space-7: 32px;  --space-8: 40px;  --space-9: 56px;  --space-10: 80px;

  --radius-sm: 4px;  --radius: 6px;     --radius-md: 8px;
  --radius-lg: 12px; --radius-xl: 16px; --radius-full: 9999px;

  --shadow-xs: 0 1px 2px 0 oklch(22% 0.02 235 / 0.04);
  --shadow-sm: 0 1px 3px 0 oklch(22% 0.02 235 / 0.06),
               0 1px 2px 0 oklch(22% 0.02 235 / 0.04);
  --shadow:    0 4px 8px -2px oklch(22% 0.02 235 / 0.08),
               0 2px 4px -2px oklch(22% 0.02 235 / 0.05);
  --shadow-lg: 0 12px 24px -4px oklch(22% 0.02 235 / 0.10),
               0 4px 8px -2px oklch(22% 0.02 235 / 0.06);

  --ease:          cubic-bezier(0.32, 0.72, 0, 1);
  --ease-out:      cubic-bezier(0.16, 1, 0.3, 1);
  --duration-fast: 120ms;  --duration: 180ms;  --duration-slow: 280ms;

  --sidebar-w: 224px;  --header-h: 56px;
  --container-max: 1440px;  --content-pad: 24px;
}
```

## 3. Layout Rules

- 固定深海蓝侧栏（`--sidebar-w`，桌面端常驻；< 1024px 折叠为抽屉）
- 顶部白色 header（`--header-h`），含面包屑/标题 + 右上角操作区
- 主内容区：`padding: var(--content-pad)`，最大宽度 `--container-max`
- **4/8 网格**：margin/padding 用 `--space-*`，禁止 `13px` `17px`
- 卡片内边距默认 `--space-6`；紧凑列表 `--space-4`
- 区块之间 `--space-6` ~ `--space-7`
- 表格行高 40-44px；表单 field 间距 `--space-4`
- 卡片网格 gap `--space-4` ~ `--space-5`

## 4. Component Rules

- **按钮**：主 `--accent` 底白字 `--radius`；次 白底 `--border-strong` 描边；文字 无边框 hover `--bg-muted`；危险 `--danger` 底；高度 `sm=28 / default=32 / lg=36`
- **卡片**：白底 + `1px solid --border` + `--radius-lg`，默认无 shadow，仅 hover / 浮层 `--shadow-sm`
- **Badge/Tag**：h=22，`--radius-md`，`--text-xs`；状态 tag 用 `--{status}-bg / --{status}-fg`
- **输入框**：h=32，边框 `--border-strong`，focus 换 `--accent` + 2px `--accent-ring`，placeholder `--fg-subtle`
- **Warning 条**：`--warning-bg` 底 + 左 icon + `--radius-md`，无重边框
- **侧栏**：项高 36，padding `--space-3`，`--radius-md`，图标 16-18；激活 `--sidebar-bg-active`；分组标题 uppercase + letter-spacing 0.05em
- **空状态**：居中图标 64-96 + 标题 + 描述 + 主按钮；扁平矢量海洋蓝系

## 5. Motion

- hover/focus 120ms，只动 color/background/border
- 展开/折叠 180ms `--ease-out`
- 弹窗入场 280ms `--ease`，opacity 0→1 + translateY(4→0)
- 禁止鼠标跟随光晕、滚动视差、infinite 动画、bounce
- 只 transition `opacity / transform / *-color`

## 6. Icons

- 首选 **Lucide**（1.5px stroke），用 inline SVG（本项目无 React）
- 侧栏和空状态允许彩色图标/插画（海洋蓝系）
- **禁止 emoji 出现在按钮、表格单元格、表单 label**
- 尺寸：14 / 16 / 18 / 20 / 24

## 7. 中文排版

- 行高：正文 `--leading`（1.55），标题 `--leading-tight`（1.3）
- 数字/代码用 `--font-mono`
- UI label 用半角标点，正文用全角

## 8. 工作流约定

新页面/组件前：
1. 先问清楚页面目的、主要动作、数据形态、是否需要空/加载/错误状态
2. 先出方案再写代码（文字或 ASCII 结构）
3. 参考已有页面的密度/圆角/按钮样式
4. Token 优先，硬编码必须解释
5. 三态必做（empty/loading/error）
6. 响应式：侧栏 < 1024 折叠，主内容 < 768 单列

自检清单：
- [ ] 有任何紫色/靛蓝？（hue ≤ 240）
- [ ] 颜色/尺寸都走了 token？
- [ ] 三态齐全？
- [ ] 键盘可达（Tab / focus / Esc）？
- [ ] 和现有页面风格一致？

---

# LLM 统一调用（2026-04-19 重构）

所有新代码调用大模型时**一律走 `appcore.llm_client`**，不要直接 `from openai import OpenAI` 或 `from appcore import gemini`。旧调用路径保留兼容但不推荐新增。

## 用法

```python
from appcore import llm_client

# Chat 风格（翻译、文案、结构化 JSON 输出）
result = llm_client.invoke_chat(
    "video_translate.localize",                   # use_case code
    messages=[{"role": "system", "content": "..."},
              {"role": "user",   "content": "..."}],
    user_id=42, project_id="task-xxx",
    temperature=0.2, max_tokens=4096,
    response_format={"type": "json_schema", ...},
)
# result: {"text", "raw", "usage": {"input_tokens", "output_tokens"}}

# Generate 风格（视频 / 图片多模态 + 可选 JSON schema）
result = llm_client.invoke_generate(
    "video_score.run",
    prompt="评估视频", media=[video_path],
    user_id=42, project_id="task-xxx",
    system="你是带货视频评委",
    response_schema={...}, temperature=0.2,
)
# result: {"text" or "json", "raw", "usage"}
```

## 三层架构

| 层 | 职责 | 定义 |
|----|------|------|
| UseCase | 业务功能 → 默认 provider/model/usage_log service | [appcore/llm_use_cases.py](appcore/llm_use_cases.py) |
| Binding | UseCase → Provider × Model 运行时绑定（DB 可覆盖） | [appcore/llm_bindings.py](appcore/llm_bindings.py) + `llm_use_case_bindings` 表 |
| Adapter | Provider → 具体 SDK 调用（4 种） | [appcore/llm_providers/](appcore/llm_providers/) |

Adapter `provider_code` 枚举：`openrouter` / `doubao` / `gemini_aistudio` / `gemini_vertex`。

## 新增业务功能的步骤

1. 在 [appcore/llm_use_cases.py](appcore/llm_use_cases.py) 里 `USE_CASES` 字典加一条 `_uc(...)`，包含默认 provider + model + usage_log service
2. 业务代码里 `llm_client.invoke_chat("module.function", ...)` 调用
3. 管理员可在 `/settings?tab=bindings` 覆盖默认绑定；点「恢复默认」回到注册表值

## 老调用路径的兼容

- [pipeline/translate.py](pipeline/translate.py) 的 `generate_localized_translation(provider=...)` 等三个函数：`provider` 可以传 use_case code，`_resolve_use_case_provider` 会映射到老式 `vertex_* / openrouter / doubao`
- [appcore/gemini.py](appcore/gemini.py) 的 `resolve_config(service=...)`：`service` 传 use_case code 且 `binding.provider=gemini_aistudio` 时覆盖 model
- `pipeline/copywriting.py` 前端已有 provider picker，走 UI 传参，不经过 bindings 默认

完整实施细节：[docs/superpowers/plans/2026-04-19-llm-call-unification.md](docs/superpowers/plans/2026-04-19-llm-call-unification.md)

---

# 本机部署到线上的标准流程（Claude Code agent 必读）

## 环境拓扑（important）

**本机就是线上生产服务器**（172.30.254.14）。所有 Claude Code agent / Codex / paseo agent 都跑在这台机器上，**开发机就是部署机**。

| 路径 | owner | 用途 |
|------|-------|------|
| `/home/cjh/.paseo/worktrees/<id>/<name>/` | cjh | 各 agent 的 git worktree（开发态） |
| `/opt/autovideosrt/` | root | **生产部署目录**，systemd 服务 `autovideosrt.service` 从这里跑（端口 80） |
| `/opt/autovideosrt-test/` | root | 测试部署目录，`autovideosrt-test.service` |

GitHub remote：`https://github.com/jinghuaswsx/AutoVideoSrtLocal.git`，prod 仓库当前分支固定 `master`。

## agent 能做什么 / 不能做什么

| 操作 | cjh agent 自己 | 必须用户 sudo |
|------|----------------|---------------|
| 在 worktree 改代码 / 跑测试 / 起 dev server | ✅ | — |
| `git commit` 在 worktree | ✅ | — |
| `git push` 到 GitHub | ❌ cjh 无凭据 | ✅ root 有 |
| `git pull` / 写 `/opt/autovideosrt/` | ❌ root 拥有 | ✅ |
| `systemctl restart autovideosrt` | ❌ | ✅ |

**结论**：发布到线上必须**用户输入 sudo 密码**执行命令；agent 不能自己完成。**不要走 `deploy/publish.sh`**——那是给外部开发机用的、需要 SSH key（CC.pem 在 Windows 上）；本机直接 sudo 即可。

## 标准发布流程（agent → 用户）

### 1. agent 在 worktree 里做完所有事

- 改代码、写测试、跑测试通过
- 起 dev server 在空闲端口（如 5090）做端到端验证（用 prod `.env` + prod 数据库）
- 测试账号：见 [testuser.md](testuser.md)（admin/709709@）
- 必测：未登录路由跳 302 + 登录后跳 200

### 2. agent 准备 git 状态

```bash
git fetch origin master
# 如本地分支落后 master：stash → rebase → pop
git stash push -u -m "pre-rebase"   # 仅当 working tree 有未 commit 改动
git rebase origin/master
git stash pop

# commit（用 HEREDOC，带 Co-Authored-By）
git add -A && git commit -m "..."
```

确保 agent 当前 worktree 分支是 `origin/master` 的**纯 fast-forward 后代**（没有 origin/master 没有的祖先）。

### 3. agent 给用户**一条 sudo 命令**

见下方"一键发布命令模板"。用户在 cjh 终端粘贴 + 输 sudo 密码即可。

### 4. sudo 命令自带验证

让命令最后 curl `http://127.0.0.1<新路由>` 输出 HTTP 码 + `systemctl is-active`，部署成功与否一目了然，不需要等用户回贴。

## 一键发布命令模板

把 `<WORKTREE_DIR>` 和 `<NEW_ROUTE>` 替换为实际值给用户：

```bash
sudo bash -c '
set -e
WORKTREE=<WORKTREE_DIR>           # 例：/home/cjh/.paseo/worktrees/0ubtzq57/hip-falcon
git config --global --add safe.directory /opt/autovideosrt
git config --global --add safe.directory $WORKTREE

cd /opt/autovideosrt
git fetch origin master
git reset --hard origin/master                    # 清掉历史半成功状态
BRANCH=$(git -C $WORKTREE rev-parse --abbrev-ref HEAD)
git fetch $WORKTREE $BRANCH:_deploy_incoming
git merge --ff-only _deploy_incoming
git branch -d _deploy_incoming
git push origin master

systemctl restart autovideosrt
sleep 3
systemctl is-active autovideosrt
curl -s -o /dev/null -w "<NEW_ROUTE>: HTTP %{http_code}\n" http://127.0.0.1<NEW_ROUTE>
'
```

**关键点**：
- `git reset --hard origin/master` 必须在 fetch worktree **之前**——清掉之前 sudo 命令半成功留下的乱状态
- `git fetch <worktree-path> <branch>:_deploy_incoming` 用本地路径作为 remote，绕开 cjh 没 GitHub 凭据的问题
- `git merge --ff-only` 失败 = worktree 分支不是 master 后代 → agent 必须重新 rebase 后再给命令
- 末尾 curl 验证：HTTP 302（跳 login）= 路由生效；404 = 部署失败；500 = 模板/代码挂

## 路由守卫规范（避免 500）

新增 web 路由必须加 `@login_required` + `@admin_required`，跟 [web/routes/order_analytics.py](web/routes/order_analytics.py) 同款：

```python
from flask_login import login_required
from web.auth import admin_required

@bp.route("/your-path")
@login_required
@admin_required
def page():
    ...
```

不加守卫的两个事故：
- 未登录访问 → layout.html 访问 `current_user.username` 抛 `UndefinedError` → HTTP 500
- API 敞开访问 → 任意人能查业务数据（安全问题）

## 测试账号

[testuser.md](testuser.md) 已纳入仓库；admin 账号 / 密码 / 测试图片视频路径都在那里。dev server 跑起来后用这个账号 login 测端到端流程。

## TTS Duration Loop 变速短路（2026-05-04）

- 当 multi-translate 任务某一轮 TTS 音频落入 `[0.9v, 1.1v]` 但不在 `[v-1, v+2]`，会**自动**用 ElevenLabs `voice_settings.speed` 重生成一遍音频试图直接收敛。命中即终结；未命中走 atempo 兜底；变速调用失败回退原始音频走 atempo。**任何分支都不再继续后续 rewrite 轮次**。
- 每次变速 pass 都会**同步**调用 `video_translate.tts_speedup_quality_review`（默认 OpenRouter + google/gemini-3-flash-preview）做双轨对比 AI 评分，120s 超时不阻塞任务（写 `status=failed` 的 eval 行）。
- admin 可在 `/admin/tts-speedup-evaluations/` 跨任务查询样本，并在 `/settings?tab=bindings` 切换评估模型。
- 想下线该功能：把 `_in_speedup_window` 改为永远返回 False（或加 settings 开关）即可，不会破坏现有 5 轮 rewrite 主路径。
