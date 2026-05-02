# 任务交接：把 ShopifyImageLocalizer V2.4 的 GUI 改动 backfill 到 master

**给另一个 Claude Code 对话的工作单。**
**你应该比这边更清楚 V2.4 当时改了什么 GUI 细节——这边的对话只发现 V2.4 改动从未进 git，需要你这边把它补提进去。**

---

## 1. 背景（一句话）

- V2.4 当时打包带走了一批 GUI 改动（窗口尺寸约屏幕 90%、留 10% 边距，"实时进度 + 运行摘要"左右分栏，底部"实时日志"区，等等），但这些改动**从未 `git commit` 进 master**。
- 后续 master 上重新打包 V2.5 时，因为只有打包前未提交的改动才会进 exe，再之后的会话清理掉了工作树，结果这些 GUI 改动**在仓库里彻底丢失**。
- 用户已确认"由你这边来 backfill"，因为你掌握 V2.4 GUI 改动的完整设计上下文。

## 2. 当前 master 状态（你拉一下就是这个状态）

- 远程：`https://github.com/jinghuaswsx/AutoVideoSrtLocal.git`
- 主分支 HEAD：`95f20f97 merge: shopify localizer detail re-replace fix v2.5`
- 已包含的 V2.5 改动（不要碰，这边已经做完）：
  - [tools/shopify_image_localizer/rpa/taa_cdp.py](tools/shopify_image_localizer/rpa/taa_cdp.py) 删除 `is_already_localized_src` + 简化 `plan_body_html_replacements` 判定，让详情图每次都强制重传
  - [tools/shopify_image_localizer/version.py](tools/shopify_image_localizer/version.py) 已是 `RELEASE_VERSION = "2.5"`，**你 backfill 完后改成 `2.6`**
  - 新增/改写两个测试：`test_plan_body_html_replacements_re_replaces_same_filename_when_replace_shopify_cdn_true`、`test_plan_body_html_replacements_skips_shopify_cdn_when_replace_disabled`
- 当前 [tools/shopify_image_localizer/gui.py](tools/shopify_image_localizer/gui.py)：
  - 行 27：`self.root.geometry("920x760")`（**这就是用户说的"窗口被改小"的原因**）
  - 行 191 起：`_build_summary` 现在是简单的 `pack` 单栏布局，**没有左右分栏**，运行摘要表格是 `Treeview` 直接 pack
  - **要恢复的就是 V2.4 跑的那个版本——窗口大、左右两栏、底部日志大块**

## 3. 反编译参考（如果你忘了 V2.4 具体细节）

V2.4 实际跑的字节码已经从 `.exe` 解出来放在：
```
/tmp/v24_extract/ShopifyImageLocalizer.exe_extracted/PYZ.pyz_extracted/tools/shopify_image_localizer/
```

里面有 V2.4 实际打包的 `gui.pyc` / `controller.pyc` / `taa_cdp.pyc` 等所有项目模块的 Python 3.14 字节码。

**用法（任选其一）：**
- 直接 `python -m dis /tmp/v24_extract/.../tools/shopify_image_localizer/gui.pyc` 看反汇编，足够看清 Tk geometry / grid 调用的字面值
- 或装 `pycdc` 反编译为可读 `.py`（Python 3.14 支持有限，可能要回退到 dis）

**重点对比的 .pyc**（按怀疑度从高到低）：
1. `gui.pyc`——窗口尺寸、`_build_summary`、实时进度面板、实时日志面板都在这里
2. `controller.pyc`——可能改过状态回调结构
3. 其他 `.pyc` 跟 master 对比 hash 看哪些 V2.4 实际不一样

也可以反过来直接问用户："V2.4 GUI 改动具体改了哪些地方？"——你那边的会话历史应该有原始改动记录。

## 4. 你要做的（硬性步骤）

按 [`C:\Users\admin\.claude\CLAUDE.md`](C:\Users\admin\.claude\CLAUDE.md) 的全局规则：

### 4.1 开 worktree
```bash
cd G:/Code/AutoVideoSrtLocal
git pull origin master
git worktree add .worktrees/v24-gui-backfill -b fix/v24-gui-backfill
cd .worktrees/v24-gui-backfill
```

### 4.2 把 V2.4 GUI 改动逐项还原
- 改 `tools/shopify_image_localizer/gui.py`（窗口几何 + summary/log 布局）
- 如有别的文件要动，一并改
- **重要：跑测试**
  ```bash
  pytest tests/test_shopify_image_localizer_batch_cdp.py tests/test_shopify_image_localizer_gui.py -q
  ```

### 4.3 提交规范（用户硬性要求）
- 这次 backfill 当作 V2.6 的内容发布（V2.5 已发但缺 GUI，补这一刀就升级到 V2.6）
- commit subject 必须带版本号前缀：
  ```
  fix(shopify-localizer v2.6): backfill v2.4 GUI window/layout changes
  ```
- commit body 里**列清楚所有恢复的具体改动**（窗口尺寸怎么算的、布局怎么排的、字段名是什么），方便以后按版本审计
- 把 `tools/shopify_image_localizer/version.py` 的 `RELEASE_VERSION` 改成 `"2.6"`，可以放进同一个 commit 里

### 4.4 合并 + push
```bash
cd G:/Code/AutoVideoSrtLocal
git merge --no-ff fix/v24-gui-backfill -m "merge: shopify localizer v2.6 (backfill v2.4 GUI + v2.5 detail fix)"
git push origin master
```

### 4.5 清理 worktree
```bash
git worktree remove .worktrees/v24-gui-backfill
git branch -d fix/v24-gui-backfill
```

### 4.6 完事通知用户
做完跟用户说"V2.4 GUI backfill 已合并到 master，可以发 V2.6 了"。**不要打包 V2.6**，那边对话会接手打包/上传/部署。

## 5. 你不需要做的（这边对话会接手）

- ❌ 不需要打包 `build_exe --version 2.6`
- ❌ 不需要上传 zip 到服务器
- ❌ 不需要更新 `system_settings.shopify_image_localizer_release` JSON
- ❌ 不需要 ssh 部署 web 代码 + healthcheck
- ❌ 不需要清理已发的 V2.5 zip / DB 记录

这些这边对话拿到通知后会一条龙做完。

## 6. 服务器/部署上下文（仅备查，你这次用不到）

- LocalServer：`172.30.254.14`，ssh key `~/.ssh/CC.pem`，root 用户
- 项目目录：`/opt/autovideosrt/`
- 下载目录：`/opt/autovideosrt/web/static/downloads/tools/`
- 当前已上传：`ShopifyImageLocalizer-portable-2.5.zip`（**有 GUI bug，等 V2.6 出来覆盖**）
- DB：`auto_video.system_settings`，主键列 `key`，配置在 key=`shopify_image_localizer_release`

## 7. 长期规则（用户已经定下来）

- 每次给 `tools/shopify_image_localizer/` 发新版本，**必须开独立 worktree**
- commit subject **必须带 `(shopify-localizer v<version>)` 前缀**
- 同一版本里多个 commit 都标同一版本号
- 发版本前 `git status` 必须干净，绝不能再次"打包但没进 git"
- 详细见用户全局 memory：`shopify_localizer_release_commit_convention`

---

完成后任意方式通知发起对话，我接手 V2.6 打包+部署+清理。
