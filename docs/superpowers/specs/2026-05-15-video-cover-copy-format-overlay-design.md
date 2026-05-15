# 文案封面文案格式与叠字修订设计

日期：2026-05-15
状态：已确认

## 背景

`docs/superpowers/specs/2026-05-14-video-cover-generation-design.md` 定义了文案封面生成的 4 步流程：视频分析、产品分析、文案创作、封面生成。实际使用中，封面图里的文字位置不稳定，且第 3 步输出的 `headline / body_text / cta` 与业务需要的三段式文案不一致。

目标文案格式必须是：

```text
标题: Don’t Get Stuck Unprepared
文案: Add high-visibility warning light, hands-free work lighting, and backup phone charging to your trunk, RV, or emergency kit.
描述: Road Trips Made Safer
```

这与 `docs/superpowers/specs/2026-04-21-push-english-texts-design.md` 已定义的推送文案合同一致：`title / message / description` 三个字段必须非空。

## 根因

- 第 3 步当前要求 `headline / body_text / cta`，导致下游没有稳定的 `description` 字段，最终复制文案也不是业务标准三段式。
- 第 4 步当前要求图片模型直接在图里生成 hook。图片模型无法稳定保证文字位置、字号、换行和可读性，提示词只能影响倾向，不能提供像素级保证。
- 第 4 步请求预览与实际生图 prompt 不完全一致，排查时容易看错模型真实输入。

## 目标

1. 第 3 步输出和保存 5 组 `ad_copy_sets`，每组英文文案字段为 `title / message / description`。
2. 第 4 步图片模型只生成无文字封面背景，不直接画任何文字。
3. 后端在输出 `1080x1920` PNG 后，用程序确定性叠加 `title`，保证安全区、换行、字号和可读性。
4. 前端最终文案展示与复制必须输出三段式：
   `标题: ...`
   `文案: ...`
   `描述: ...`
5. 兼容历史任务的 `headline / body_text / cta`：读取旧结构时映射为 `title / message / description`，不让旧项目失效。
6. 第 4 步保存每张封面实际使用的 prompt、文案和叠字元数据，提示词弹窗看到的是实际请求。

## 非目标

- 本次不改商品链接抓取、默认模型配置、权限、项目列表或部署流程。
- 本次不引入人工可拖拽文字编辑器。
- 本次不承诺图片模型能完全复刻商品外观，只修正文案合同、文字位置和可审计性。

## 设计

### 文案合同

`ad_copy_sets` 结构调整为：

```json
{
  "ad_copy_sets": [
    {
      "id": 1,
      "angle": "痛点解决型",
      "english": {
        "title": "Don’t Get Stuck Unprepared",
        "message": "Add high-visibility warning light, hands-free work lighting, and backup phone charging to your trunk, RV, or emergency kit.",
        "description": "Road Trips Made Safer"
      },
      "chinese_translation": {
        "title": "别在紧急时毫无准备",
        "message": "为后备箱、房车或应急包增加高可见警示灯、免手持工作照明和备用手机充电。",
        "description": "让自驾出行更安全"
      },
      "usage_note": "适合车尾箱、路边停车或应急包场景。"
    }
  ]
}
```

字段含义：

- `title`：封面叠字，只能是一句英文短标题，优先 3 到 7 个词，最多 42 个字符。
- `message`：广告正文，用于复制文案和下游推送，不画到封面上。
- `description`：短描述/副标题，用于复制文案和下游推送，不画到封面上。

兼容规则：

- 旧结构 `headline` → `title`
- 旧结构 `body_text` → `message`
- 旧结构 `cta` → `description`

### 生图 prompt

封面生成 prompt 改为“无文字背景”合同：

- 图片模型必须生成真实 UGC 风格 9:16 封面背景。
- 禁止生成任何可读文字、字幕、UI、价格、按钮、贴纸、箭头、红圈。
- 要为后续程序叠字预留一块干净区域，优先顶部 18% 或底部 22%，但产品和关键动作不得被遮挡。
- prompt 中只传入当前 `selected_ad_copy`，不重复塞全部长文案。

### 程序叠字

后处理在 `normalize_cover_png` 之后执行：

- 画布固定 `1080x1920`。
- 默认文字区域：顶部安全区，左右边距 80px，上边距 120px，最大宽度 920px。
- 若文字超过一行，自动按单词换行，最多 3 行。
- 字号从 86px 起向下收敛，最小 46px；仍放不下时按词边界截断并加 `...`。
- 字体优先使用系统 DejaVu Sans Bold，缺失时回退 PIL 默认字体。
- 文字使用白色、黑色描边和轻阴影；必要时在文字背后加半透明深色圆角底板。
- 叠字元数据写入每张 cover：`overlay_text`、`overlay_box`、`overlay_font_size`、`overlay_lines`。

### 可审计性

每张封面记录：

- `prompt`：本张图实际发送给图片模型的 prompt。
- `copy`：本张图使用的标准化文案。
- `formatted_copy`：三段式文本。
- `overlay_*`：程序叠字元数据。

第 4 步的 `step_requests.cover_generation` 保存 `image_prompts` 数组，而不是只保存一个预估 prompt。

### 最终结果布局

第 3 步文案创作结果必须把 5 组 `ad_copy_sets` 渲染为横向卡片组：

- 宽屏下 5 张卡片优先横向并列展示；内容区宽度不足时使用自适应网格自然换成两行或单列，不出现横向溢出。
- 每张卡片只对应一组文案，卡片内同时展示英文和中文对照，字段顺序固定为 `标题 / 文案 / 描述`。
- 每张卡片的英文区与中文区展示字段必须一致；按钮也必须一致提供“复制英文”“复制中文”“复制双语”三个操作。
- “复制英文”输出英文三段式；“复制中文”输出中文三段式；“复制双语”输出英文三段式、空行、中文三段式。
- 历史 `headline / body_text / cta` 数据继续按兼容规则映射后展示和复制。

第 4 步封面生成卡片内不再使用缩略图切换和左图右文案布局。前端必须直接读取当前 `state.result.covers`，按数组顺序从左到右渲染 1 到 4 张结果卡片。

每张结果卡片内部结构固定为：

1. 上方文案区：展示对应封面的三段式 `标题 / 文案 / 描述` 文案。
2. 文案区底部：一个蓝色胶囊“复制文案”按钮，复制本卡片对应的 `formatted_copy`，没有该字段时用标准化 `copy` 现场拼接。
3. 下方封面区：展示本卡片对应封面图。
4. 图片操作区：只保留一个“保存图片”胶囊按钮，不再显示“复制图片”。

文案区、复制文案按钮、封面图和保存图片按钮使用同一宽度约束；保存图片按钮在封面图下方居中对齐。多张图的结果区使用自适应网格排列，左右间距一致；运行中已有部分封面时立即展示已有卡片，全部封面完成后同一网格自动重新对齐，不出现缩略图切换、卡片跳宽或上下错位。

后端多张图生成必须串行排队执行。一次封面生成步骤内部只发起当前这一张图片生成请求；当前图片完成、保存 artifact、写入 `state.result.covers` 并持久化后，才开始下一张。前端依赖这些 partial state 轮询来逐张显示，不能等所有图片全部生成完才一次性落状态。

## 验收

- 给定示例文案时，封面结果的 `copy.english.title/message/description` 与输入字段一一对应。
- “复制文案”按钮输出：
  `标题: ...`
  `文案: ...`
  `描述: ...`
- 生图 prompt 中明确禁止模型直接生成文字。
- 最终 PNG 尺寸仍是 `1080x1920`。
- cover 结果包含 `overlay_text` 和 `overlay_box`。
- 旧的 `headline/body_text/cta` 测试数据仍能生成封面和复制文案。
- 第 4 步运行中只要 `state.result.covers` 已有封面，前端就显示已有卡片。
- 多张图片在后端按 1 → 2 → 3 → 4 串行排队，生成一张就持久化一张。
- 多张封面结果直接从左到右排列，每张卡片上文案、下封面图，蓝色“复制文案”按钮与图片等宽。
- 封面图下方不再出现“复制图片”；只保留与封面图等宽并居中对齐的“保存图片”按钮。
- 第 3 步有 5 组文案时优先显示为 5 张横向卡片；窄屏下自动换行。每张卡片同时展示中英文三段式，并提供“复制英文”“复制中文”“复制双语”。

## 测试

- `tests/test_video_cover_generation.py`
  - 覆盖新文案 schema 的解析与校验。
  - 覆盖旧 schema 的兼容映射。
  - 覆盖生图 prompt 禁止直接生成文字。
  - 覆盖程序叠字后输出仍为 `1080x1920`，并记录 overlay 元数据。
- 模板断言更新为 `title/message/description` 和三段式复制文案。
