# AutoVideoSrt Codex Notes

- 与用户沟通一律使用中文。
- Follow the global workflow and installed-skill guidance from `C:\Users\admin\.Codex\AGENTS.md`.
- When this project gets real source files, update this file with exact run, test, lint, typecheck, and build commands.
- Prefer installed skills when relevant, especially `superpowers:*`, `Codex-api`, `pdf`, `docx`, `pptx`, `xlsx`, `webapp-testing`, `frontend-design`, and `mcp-builder`.

## Link Check Desktop Commands

- 开发运行：`python -m link_check_desktop.main`
- 聚焦测试：`pytest tests/test_appcore_medias_link_check_bootstrap.py tests/test_link_check_bootstrap_routes.py tests/test_link_check_gemini.py tests/test_link_check_same_image.py tests/test_link_check_desktop_storage.py tests/test_link_check_desktop_bootstrap_api.py tests/test_link_check_desktop_controller.py tests/test_link_check_desktop_gui.py -q`
- 打包：`pyinstaller link_check_desktop/packaging/link_check_desktop.spec`

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

# Generate 风格（视频 / 图片多模态 + 可选 JSON schema）
result = llm_client.invoke_generate(
    "video_score.run",
    prompt="评估视频", media=[video_path],
    user_id=42, project_id="task-xxx",
    system="你是带货视频评委",
    response_schema={...}, temperature=0.2,
)
```

## 三层架构

| 层 | 职责 | 定义 |
|----|------|------|
| UseCase | 业务功能 → 默认 provider/model/usage_log service | `appcore/llm_use_cases.py` |
| Binding | UseCase → Provider × Model 运行时绑定（DB 可覆盖） | `appcore/llm_bindings.py` + `llm_use_case_bindings` 表 |
| Adapter | Provider → 具体 SDK 调用（4 种） | `appcore/llm_providers/` |

Adapter `provider_code` 枚举：`openrouter` / `doubao` / `gemini_aistudio` / `gemini_vertex`。

## 新增业务功能的步骤

1. 在 `appcore/llm_use_cases.py` 里 `USE_CASES` 字典加一条 `_uc(...)`，包含默认 provider + model + usage_log service
2. 业务代码里 `llm_client.invoke_chat("module.function", ...)` 调用
3. 管理员可在 `/settings?tab=bindings` 覆盖默认绑定；点「恢复默认」回到注册表值

## 老调用路径的兼容

- `pipeline/translate.py` 的 `generate_localized_translation(provider=...)` 等三个函数：`provider` 可以传 use_case code，`_resolve_use_case_provider` 会映射到老式 `vertex_* / openrouter / doubao`
- `appcore/gemini.py` 的 `resolve_config(service=...)`：`service` 传 use_case code 且 `binding.provider=gemini_aistudio` 时覆盖 model

完整实施细节：`docs/superpowers/plans/2026-04-19-llm-call-unification.md`
