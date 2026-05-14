# 文案封面项目卡片封面与操作菜单设计

日期：2026-05-15
状态：已确认

## 锚点

- `AGENTS.md`：代码修改前必须先有仓库内文档锚点，并在隔离 worktree 内开发。
- `docs/superpowers/specs/2026-05-14-video-cover-generation-design.md` §1.1：`/video-cover` 是项目列表页，管理员查看全局 `video_cover` 项目，项目卡片展示创建人。
- `docs/superpowers/specs/2026-05-14-video-cover-generation-design.md` §数据流：创建项目时保存上传视频、抽取缩略图并写入 `projects.thumbnail_path`。
- `web/templates/multi_translate_list.html`：项目卡片布局、创建人/创建时间底部信息和右上角三点菜单作为交互参考。

## 目标

文案封面生成项目列表页的项目卡片对齐多语种视频翻译项目列表页的使用方式：封面在上、信息在下、右上角三点菜单提供项目级操作。

## 范围

- 项目卡片封面使用上传视频第一帧。
- 创建项目时立即抽取卡片封面，封面文件固定服务于列表卡片。
- 卡片封面显示尺寸固定为 `180x270`。
- 如果视频第一帧抽取失败，卡片封面区域显示全白占位，不显示图标或文字。
- 卡片底部显示项目名、创建人中文名、创建时间和状态。
- 创建人优先显示 `users.xingming`，为空或字段不存在时回退 `users.username`。
- 卡片右上角增加三点按钮，点击后出现“复制项目”和“删除项目”。
- 删除项目为软删除，并清理项目本地文件。
- 复制项目创建一个新项目，复制原项目的商品链接、商品信息、产品主图、上传源视频、封面张数和模型配置快照；新项目从第一步自动重新执行。

## 非目标

- 不修改多语种视频翻译列表页。
- 不抽通用项目卡片组件。
- 不改变文案封面详情页四步执行逻辑。
- 不改变封面生成结果图的 `1080x1920` 输出规则。

## 行为

### 列表卡片

`/video-cover` 的项目列表使用固定宽度卡片。卡片上半部分为 `180x270` 封面区，若 `thumbnail_path` 存在则通过 `/api/tasks/<task_id>/thumbnail` 加载；否则渲染纯白空区域。

卡片底部保留紧凑信息：

- 第一行：项目显示名，超出两行截断。
- 第二行：`创建人：<中文名或用户名>`。
- 第三行：`创建时间：MM-DD HH:mm`。
- 第四行：状态 badge。

### 创建项目

`POST /video-cover/api/projects` 保存上传视频后，使用第一帧生成列表封面缩略图。缩略图视觉目标为 `180x270`，后端以裁切方式输出，避免拉伸变形。抽取失败时不阻断创建流程，`thumbnail_path` 为空，列表卡片展示全白占位。

### 删除项目

三点菜单“删除项目”发送：

```http
DELETE /video-cover/api/<task_id>
X-CSRFToken: <token>
```

路由只允许登录管理员访问。管理员可删除当前列表中可见的文案封面项目。删除时调用现有 cleanup 清理 `task_dir` 和 `state_json.video_path` 指向的本地文件，然后将 `projects.deleted_at` 置为当前时间。

### 复制项目

三点菜单“复制项目”发送：

```http
POST /video-cover/api/<task_id>/duplicate
X-CSRFToken: <token>
```

复制成功后返回：

```json
{
  "ok": true,
  "id": "<new_task_id>",
  "redirect_url": "/video-cover/<new_task_id>"
}
```

前端跳转到新项目详情页。复制项目归当前操作用户所有。复制不复用旧项目的生成结果、步骤进度、错误和中间请求报文；只复制项目输入与配置快照，并自动从 `video_analysis` 开始执行。

## 测试

- Store 层：列表查询可使用中文名表达式作为 `creator_name`。
- 模板层：卡片包含 `180x270` 封面尺寸、全白占位、创建人、创建时间、三点菜单、“复制项目”和“删除项目”。
- 创建接口：抽帧使用 `180x270` 裁切滤镜；抽帧失败时项目仍创建且 `thumbnail_path` 为空。
- 删除接口：管理员删除可见项目时调用 cleanup 并软删除项目。
- 复制接口：复制源视频和产品主图，创建新项目，保留 `image_count` 与 `model_defaults`，并启动后台链路。

## 验证

```bash
pytest tests/test_video_cover_project_store.py tests/test_video_cover_generation.py -q
python -m compileall web/routes/video_cover.py appcore/video_cover_project_store.py
```

无仓库级 `CHANGELOG` 文件，本次不更新 changelog。`AGENTS.md` 已满 80 行，本次不追加主题索引，避免违反行数红线。
