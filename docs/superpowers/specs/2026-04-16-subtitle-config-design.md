# 字幕配置模块设计文档

**日期**: 2026-04-16  
**状态**: 已确认，待实现

---

## 1. 背景与目标

当前 `pipeline/compose.py` 中 `_build_subtitle_filter()` 的字幕样式完全硬编码：

- 字体：Arial，FontSize=18（用户反馈偏大）
- 仅三档位置（top / middle / bottom），不直观
- 无字体选择，无 UI 可调项

目标：在任务配置步骤中增加字幕样式配置区，允许用户在每次任务开始前选择字体、字号，并通过 TikTok 仿真手机界面拖拽定位字幕位置，位置保存为全局默认。

---

## 2. 字体库

打包 6 个 Google Fonts 免费字体到项目 `fonts/` 目录，ffmpeg 通过 `fontsdir` 参数直接引用。

| 文件名 | 显示名 | 风格标签 |
|---|---|---|
| `Impact.ttf` | Impact | 极粗极窄，冲击感强，TikTok 最经典字幕字体 |
| `Oswald-Bold.ttf` | Oswald Bold | 窄体现代感，广告字幕首选 |
| `BebasNeue-Regular.ttf` | Bebas Neue | 全大写展示字体，戏剧感强，适合 hook 句 |
| `Montserrat-ExtraBold.ttf` | Montserrat ExtraBold | 几何感，干净大气，品牌广告常用 |
| `Poppins-Bold.ttf` | Poppins Bold | 圆角友好，美妆/生活类视频适用 |
| `Anton-Regular.ttf` | Anton | 比 Impact 略宽，更清晰，适合稍长字幕行 |

字体文件通过 Google Fonts 下载，以 OFL / Apache 2.0 开源协议发布，可商用。

---

## 3. 字号系统（分辨率自适应）

字号不写死 pt 值，在合成时根据视频实际高度换算：

```
font_size_pt = round(video_height / 1080 × base_pt)
```

| 档位 | UI 标签 | 基准 pt（1080p） | 说明 |
|---|---|---|---|
| `small` | 小 | 11 | 内容密集时 |
| `medium` | 中（默认） | 14 | 日常推荐 |
| `large` | 大 | 18 | 少量字强调，等同旧版默认但视觉更合理 |

> 原默认 18pt 改为 medium=14pt，旧版"偏大"问题通过此修正解决。

---

## 4. 位置系统（TikTok 手机拖拽定位）

### 4.1 交互方式

替换原来的 top / middle / bottom 三个按钮，改为弹出一个 TikTok 仿真手机界面：

- 手机框：9:16 比例，缩放展示
- 界面包含 TikTok 典型 UI 元素：
  - 顶部：Following / For You / LIVE 标签栏
  - 右侧：头像、点赞、评论、分享图标列
  - 左下角：创作者账号名、视频标题、音乐信息条
- 红色半透明遮挡区标注两个危险区域：
  - 左下 UI 区：底部约 21%（账号名/标题/音乐条）
  - 右侧图标区：右侧约 22%（点赞/评论/分享）

### 4.2 拖拽操作

- **点击**手机屏幕任意位置 → 字幕条（黄色边框）跳转到该位置
- **拖住**字幕条上下滑动 → 实时移动并显示距顶百分比
- 字幕条进入遮挡区时显示 ⚠ 遮挡区 警告徽标（不强制阻止）

### 4.3 坐标存储与换算

位置以「距顶百分比」存储（浮点数，0.0=顶，1.0=底）：

```python
# 存储格式（用户设置）
subtitle_position_y: float = 0.68   # 默认 68%（距顶）

# ffmpeg 换算（compose 阶段）
margin_v = round(video_height * (1.0 - subtitle_position_y))
# 1080p 默认：round(1080 × 0.32) = 346px（距底）
# 对应 ASS：Alignment=2, MarginV=346
```

默认值 `0.68`（距顶 68%）= 距底 32%，位于左下 UI 遮挡区上方约 100px，是 TikTok 字幕最常见的安全位置。

### 4.4 全局默认保存

- 位置值存入用户全局设置（数据库 `user_settings` 表或 `config.py` 中的用户配置文件）
- 新任务配置面板打开时，位置条恢复上次保存的位置
- 用户只要不主动修改，每次任务都继承该默认值

---

## 5. UI 嵌入方式

在现有任务配置步骤的「选择音色」区块右侧，并排新增「字幕样式」区块，无需新增页面：

```
┌─────────────────────────────────────────────────────────┐
│  ⚙️ 人工配置                                              │
│  ┌──────────────┐  ┌──────────────────────────────────┐ │
│  │  音色选择     │  │  字幕样式（新增）                  │ │
│  │  [男声][女声] │  │  字体: [Impact][Oswald][Bebas]   │ │
│  │  Adam · EL   │  │       [Montserrat][Poppins][Anton]│ │
│  └──────────────┘  │  字号: [小] [中★] [大]           │ │
│                    │  位置: [📱 在手机界面里设置...]    │ │
│                    └──────────────────────────────────┘ │
└─────────────────────────────────────────────────────────┘
```

「位置」项不再是按钮组，改为一个按钮打开手机模拟器弹窗（modal）。

---

## 6. 改动范围

### 新增文件
```
fonts/
├── Impact.ttf
├── Oswald-Bold.ttf
├── BebasNeue-Regular.ttf
├── Montserrat-ExtraBold.ttf
├── Poppins-Bold.ttf
└── Anton-Regular.ttf
```

### 修改文件

| 文件 | 改动内容 |
|---|---|
| `pipeline/compose.py` | `_build_subtitle_filter()` 接收 `font_name`、`font_size_preset`、`position_y`；新增 `_get_video_height()` 辅助函数；字号自适应换算；MarginV 按 position_y 动态计算 |
| `pipeline/compose.py` | `compose_video()` 签名扩展：新增 `font_name`、`font_size_preset`、`subtitle_position_y` 参数 |
| `web/routes/task.py` | 启动任务接口接收 `subtitle_font`、`subtitle_size`、`subtitle_position_y` |
| `web/services/pipeline_runner.py` | 透传字幕参数到 `compose_video()` |
| `web/templates/_task_workbench.html` | 配置面板新增字幕样式区块；手机位置选择器弹窗（modal）HTML |
| `web/templates/_task_workbench_scripts.html` | 字体卡片选中交互；字号按钮组；手机拖拽位置选择器逻辑；位置默认值读写 `localStorage` |

### 数据持久化方案

字幕默认位置以 `localStorage` 存储（key: `subtitle_position_y`），纯前端，零后端改动。页面加载时读取并还原到手机位置条；用户拖动确认后立即写入。若未来需多设备同步，可迁移到用户设置表（不影响后端合成逻辑）。

---

## 7. 不在本次范围内

- 字幕颜色/背景框/阴影等样式扩展
- 字幕动画效果
- 字体预览的服务端渲染（用 CSS @font-face 做前端预览即可）
- 多用户字体配置隔离
