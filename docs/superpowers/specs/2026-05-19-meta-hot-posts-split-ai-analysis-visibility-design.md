# Meta 热帖美国/欧洲 AI 分析独立显示设置

日期：2026-05-19

## 背景

Meta 热帖素材库普通列表目前只带出了美国市场搬运分析字段，欧洲市场翻译分析字段只在“欧洲Top50”接口返回，导致素材库卡片只能看到美国 AI 分析结果。页面已有的“显示AI分析/关闭AI分析”也是一个总开关，无法分别控制美国和欧洲两类分析。

## 目标

- 素材库、今日新增、我的收藏夹列表都返回欧洲分析字段，和“欧洲Top50”使用同一套 hydrate 字段。
- 卡片里的美国市场搬运分析与欧洲市场翻译分析分别显示/隐藏。
- 页面工具区用两个独立按钮实现：
  - `显示美国AI分析` / `隐藏美国AI分析`
  - `显示欧洲AI分析` / `隐藏欧洲AI分析`
- 设置按用户维度持久化；所有用户默认美国和欧洲分析都隐藏。
- 开关只控制卡片内分析块，不影响底部“美国市场搬运AI分析”“欧洲市场翻译AI分析”弹窗按钮。

## 非目标

- 不改变 AI 分析执行、回填、排序和筛选逻辑。
- 不新增欧洲分析队列。
- 不删除原始英文分析字段。

## 实现计划

- `appcore/meta_hot_posts/store.py` 的普通列表、今日新增、收藏夹 SQL 左连接 `meta_hot_post_europe_assessments e`，仅取 `status='done'` 的欧洲分析字段，并补齐中文缓存字段。
- `appcore/meta_hot_posts/service.py` 继续通过 `_hydrate_item()` 输出 `europe_fit_*` 与 `europe_fit_*_zh`。
- 使用 `api_keys` 表新增用户级 service：`meta_hot_posts_ai_visibility`，保存 JSON：`{"us": false, "europe": false}`。
- 新增 `/xuanpin/api/meta-hot-posts/ai-analysis-visibility` GET/POST，读取和保存当前用户设置。
- `web/templates/meta_hot_posts.html` 拆分 `copyabilityBlock()` 与 `renderEuropeFitPanel()` 的显示判断，默认隐藏，美国/欧洲按钮点击后分别持久化并重绘当前卡片。
- 显示设置保存按“最后一次点击”生效：前端允许乐观重绘，但每次保存请求必须带本地会话 ID 和单调版本号。后端对同一会话 ID 拒绝旧版本覆盖新版本；前端也要忽略旧请求的成功响应和失败回滚。

## 验证

- Store 测试覆盖普通列表、今日新增、收藏夹都 select/join 欧洲分析字段。
- Service 测试覆盖普通列表 hydrate 欧洲中文字段。
- Route/template 测试覆盖两个按钮文案、默认隐藏、用户级设置接口，以及保存请求“最后一次点击优先”的旧响应/旧失败忽略逻辑。
- 发布后确认生产服务 active，页面未登录 302，登录后按钮默认显示“显示美国AI分析”“显示欧洲AI分析”。
