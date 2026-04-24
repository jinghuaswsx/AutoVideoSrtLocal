# 店小秘 Shopify ID 回填功能 PRD

## 1. 文档信息

- 功能名称：店小秘 Shopify ID 爬取并回填
- 目标表：`auto_video.media_products`
- 目标字段：`shopifyid`
- 入口脚本：`tools/shopifyid_dianxiaomi_sync.bat`
- 主程序：`tools/shopifyid_dianxiaomi_sync.py`
- 文档日期：2026-04-24

## 2. 背景

素材管理里的产品库已经有内部 `product_code`，但缺少 Shopify 商品 ID。  
店小秘 ERP 的 Shopify 在线商品库接口可以直接返回：

- `shopifyProductId`：对应需要回填的 `shopifyid`
- `handle`：对应我们产品库里的 `product_code`

因此可以通过“店小秘在线商品库 -> 店小秘接口 -> 本地产品库精确匹配 -> 数据库回填”的方式，批量补齐素材管理中的 Shopify ID。

## 3. 目标

本功能要实现一键执行以下动作：

1. 自动打开专用 Chrome 目录 `C:\chrome-shopifyid-diaoxiaomi`
2. 使用已登录的店小秘会话访问在线商品页
3. 调用店小秘分页接口抓取全部在线商品
4. 读取正式库 `auto_video.media_products`
5. 当 `handle == product_code` 完全一致时，将 `shopifyProductId` 回填到 `shopifyid`
6. 输出本次执行的汇总结果和明细日志

## 4. 非目标

以下内容不在本功能范围内：

- 不处理店小秘“非在线”商品
- 不做模糊匹配、近似匹配、去空格匹配
- 不覆盖已有且不一致的 `shopifyid`
- 不反向修改店小秘商品数据
- 不要求用户在正式使用时手动选择环境

## 5. 使用对象

- 运营同学：手动触发一次同步，给素材管理补齐 Shopify ID
- 开发同学：调试接口、排查匹配失败、核查日志

## 6. 前置条件

执行前必须满足以下条件：

1. 当前机器是 Windows，并且已安装 Google Chrome
2. 本机存在可用的 Python 运行环境
3. 项目目录中存在脚本：
   - `tools/shopifyid_dianxiaomi_sync.bat`
   - `tools/shopifyid_dianxiaomi_sync.py`
4. 本机有店小秘专用 Chrome 用户目录：
   - `C:\chrome-shopifyid-diaoxiaomi`
5. 本机 SSH 私钥存在且可连接服务器：
   - `C:\Users\admin\.ssh\CC.pem`
6. 当前账号有权限访问店小秘页面：
   - `https://www.dianxiaomi.com/web/shopifyProduct/online`
7. Python 环境中已安装脚本依赖，至少包括 `playwright`

## 7. 数据来源与字段映射

### 7.1 店小秘接口

- 页面地址：`https://www.dianxiaomi.com/web/shopifyProduct/online`
- 接口地址：`https://www.dianxiaomi.com/api/shopifyProduct/pageList.json`
- 请求方式：`POST`
- 分页大小：`100`

核心返回字段：

- `shopifyProductId` -> 回填到 `media_products.shopifyid`
- `handle` -> 对比 `media_products.product_code`

### 7.2 本地数据库

- 正式库：`auto_video`
- 表名：`media_products`
- 读取字段：
  - `id`
  - `product_code`
  - `shopifyid`

## 8. 匹配规则

只允许一种回填条件：

- `店小秘.handle == media_products.product_code`

匹配规则是“完全一致”，包括：

- 区分是否为空
- 不做模糊包含
- 不自动替换字符
- 不自动大小写纠偏

只要不完全一致，就跳过。

## 9. 回填规则

### 9.1 可回填

满足以下条件时执行更新：

1. 本地 `product_code` 非空
2. 店小秘返回的 `handle` 非空
3. 店小秘返回的 `shopifyProductId` 非空
4. `handle == product_code`
5. 本地 `shopifyid` 为空

执行 SQL 时只更新空值：

- `shopifyid IS NULL`
- 或 `shopifyid = ''`

### 9.2 不回填

以下情况全部跳过：

- 本地没有匹配到 `product_code`
- 店小秘 `handle` 重复，但对应多个不同 `shopifyProductId`
- 本地 `shopifyid` 已有值，且与店小秘返回值不同
- 店小秘商品没有 `handle`
- 店小秘商品没有 `shopifyProductId`

## 10. 用户操作流程

### 10.1 标准启动方式

推荐直接双击：

```bat
tools\shopifyid_dianxiaomi_sync.bat
```

脚本会自动执行：

```bat
python tools\shopifyid_dianxiaomi_sync.py
```

### 10.2 执行过程

脚本启动后会按以下顺序执行：

1. 自动关闭当前仍在运行的专用 Chrome 进程
2. 用专用目录 `C:\chrome-shopifyid-diaoxiaomi` 启动 Chrome
3. 打开店小秘在线商品页 `https://www.dianxiaomi.com/web/shopifyProduct/online`
4. 等待用户确认店小秘已经登录
5. 通过浏览器登录态调用店小秘接口分页抓取全部在线商品
6. 连接正式库 `auto_video`
7. 检查 `media_products.shopifyid` 字段是否存在，不存在则自动补字段
8. 读取本地产品并执行精确匹配
9. 回填可以写入的记录
10. 生成 JSON 日志文件
11. 在命令行输出汇总结果

### 10.3 首次使用

首次使用或店小秘登录失效时，操作步骤如下：

1. 双击 `tools\shopifyid_dianxiaomi_sync.bat`
2. 等脚本自动打开专用 Chrome
3. 在打开的 Chrome 中登录店小秘
4. 回到终端窗口，按一次回车
5. 等待脚本抓取、匹配、回填完成
6. 查看终端汇总和日志文件

### 10.4 已登录时的启动方式

如果已经确认专用 Chrome 目录中的店小秘登录态有效，可以直接运行：

```bash
python tools/shopifyid_dianxiaomi_sync.py --skip-login-prompt
```

说明：

- 正常使用时默认就是正式库
- 不需要手动选择数据库环境
- 日常操作推荐还是直接双击 `.bat`

### 10.5 每日定时任务安装方式

如果要在当前电脑上安装“每天 12:10 自动执行一次”的 Windows 定时任务，运行：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File tools\register_shopifyid_dianxiaomi_sync_task.ps1
```

安装完成后，系统会注册一个任务：

- 任务名：`AutoVideoSrtLocal-ShopifyIdDianxiaomiSyncDaily`
- 执行时间：每天 `12:10`
- 实际执行脚本：`tools\shopifyid_dianxiaomi_sync_daily.ps1`

该定时脚本会自动：

1. 进入项目根目录
2. 调用 `python tools\shopifyid_dianxiaomi_sync.py --skip-login-prompt`
3. 把运行输出写入 `output/shopifyid_dianxiaomi_sync/` 下的调度日志

## 11. 成功输出

脚本完成后，终端会输出以下类型的信息：

- 正在操作的数据库
- 店小秘在线商品总数
- 抓取页数
- 实际抓取条数
- 命中 `product_code` 条数
- 新回填条数
- 已一致条数
- 本地冲突条数
- 本地未匹配条数
- 远端未匹配条数
- 远端 `handle` 冲突条数
- 本次日志文件路径

示例输出结构：

```text
同步完成：
  正式库: root@172.30.254.14:auto_video
  店小秘在线商品总数: 404
  抓取页数: 5
  抓取商品数: 404
  命中 product_code: 84
  新回填: 84
  已一致: 0
  本地冲突: 0
  本地未匹配: 19
  远端未匹配: 266
  远端 handle 冲突: 25
  结果日志: output/shopifyid_dianxiaomi_sync/xxxx.json
```

## 12. 日志与结果文件

每次运行都会生成一份 JSON 日志：

- 目录：`output/shopifyid_dianxiaomi_sync/`
- 文件名格式：`shopifyid-dianxiaomi-sync-YYYYMMDD-HHMMSS.json`

日志中会记录：

- 分页信息
- 更新成功列表
- 已一致列表
- 冲突列表
- 本地未匹配列表
- 远端未匹配列表
- 远端重复 `handle` 列表

这个文件用于复盘和排查，不需要人工编辑。

## 13. 常见问题

### 13.1 为什么脚本会先打开浏览器？

因为店小秘接口依赖登录态，脚本不是直接裸请求，而是借用已登录浏览器上下文发接口请求。

### 13.2 为什么有些商品没有回填？

常见原因有四类：

1. 店小秘 `handle` 和本地 `product_code` 不完全一致
2. 本地已经有 `shopifyid`，而且和店小秘值不一致
3. 店小秘同一个 `handle` 出现多个不同 `shopifyProductId`
4. 本地素材库里根本没有这条 `product_code`

### 13.3 会不会把已有数据覆盖错？

不会。当前策略只回填空值，不会覆盖已有且不一致的 `shopifyid`。

### 13.4 如果字段不存在怎么办？

脚本会先检查 `media_products.shopifyid` 是否存在，不存在时自动执行加字段。

### 13.5 如果 Chrome 起不来怎么办？

优先检查：

1. 本机是否安装 Chrome
2. `C:\chrome-shopifyid-diaoxiaomi` 是否可访问
3. 是否有残留的专用 Chrome 进程无法关闭

## 14. 运维与开发说明

### 14.1 默认环境

- 用户日常运行默认连接正式库 `auto_video`
- 不要求用户在界面或命令中手动选择环境

### 14.2 开发调试

程序内部保留测试库切换能力，供开发排查问题时使用；这不是日常操作入口，不需要暴露给运营同学。

### 14.3 数据安全策略

- 只更新空 `shopifyid`
- 单次批量更新放在事务里执行
- 所有跳过与冲突都写入日志

### 14.4 定时运行约束

- 定时任务默认使用 `--skip-login-prompt`，因此不会卡在“等待按回车”这一步
- 定时任务依赖 `C:\chrome-shopifyid-diaoxiaomi` 中仍然有效的店小秘登录态
- 如果店小秘登录态失效，本次任务会失败并在日志中记录错误
- 由于脚本需要调用本机 Chrome 浏览器，推荐在用户已登录桌面会话时运行

## 15. 验收标准

当以下条件成立时，功能视为可用：

1. 双击 `tools/shopifyid_dianxiaomi_sync.bat` 能正常启动
2. 脚本能自动打开专用 Chrome 并访问店小秘在线商品页
3. 用户登录后能继续执行
4. 能按页抓取店小秘全部在线商品
5. 能将 `handle == product_code` 的记录正确回填到 `shopifyid`
6. 不会覆盖已有冲突值
7. 能输出完整的汇总结果和 JSON 日志

## 16. 推荐使用口径

如果只关心“怎么用”，可以按下面这版最短流程执行：

1. 双击 `tools\shopifyid_dianxiaomi_sync.bat`
2. 等脚本自动打开 `C:\chrome-shopifyid-diaoxiaomi` 专用 Chrome
3. 如未登录店小秘，先登录
4. 回终端按回车
5. 等待同步完成
6. 看终端里的“新回填”数量和日志路径
