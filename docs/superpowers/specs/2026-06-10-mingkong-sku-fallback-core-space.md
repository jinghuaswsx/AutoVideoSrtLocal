# 2026-06-10 明空 SKU 兜底候选核心操作区

## Anchors

- `AGENTS.md`：文档驱动代码、worktree 隔离和 focused pytest 验证规则。
- `web/templates/CLAUDE.md`：模板修改需遵守 Jinja 与 CSRF/路由守卫约束。
- `web/static/CLAUDE.md`：后台 Ocean Blue 视觉、响应式与可操作控件约束。
- `docs/superpowers/specs/2026-06-09-mingkong-product-library-foundation-design.md`：新品 SKU 工作台读取明空产品库候选，人工确认后再写入本地产品和 DXM03。

## Context

明空 SKU 同步弹窗的“备选明空 ERP SKU”是兜底配对逻辑。当前候选列表嵌在“执行后的目标效果”卡片内部，并限制为很矮的小条目列表，运营在远程桌面或窄视口下无法看全 SKU 图片，也缺少足够的点击操作空间。

## Goal

1. “备选明空 ERP SKU”在每个同步行详情中成为独立核心操作区，而不是目标效果卡片内部的附属小列表。
2. 候选 SKU 使用更大的图片卡片展示，图片必须保留可辨识空间，文本字段不挤压图片。
3. 候选区允许纵向滚动，但不能用 150px 小列表压缩关键内容。
4. 点击候选卡片仍快速回填目标字段：店小秘 SKU、ERP 编码、商品名、目标图片 URL、采购链接、1688 商品 ID。
5. 不改变后端同步、AI 判断、数据库、RPA 复刻或接口契约。

## UX Contract

- 候选区标题为“备选明空 ERP SKU (点击快速回填)”。
- 候选区在每个同步行详情内优先展示，位于三张对比卡片上方，并独立占满整行宽度。
- 候选卡片至少展示：
  - SKU 图片，使用固定尺寸且 `object-fit: contain`。
  - 明空 ERP SKU。
  - 商品名。
  - ERP 编码或 1688 商品 ID 之一作为辅助信息。
- 候选卡片可点击，hover/focus 有明确边框或背景反馈。
- 移动端候选区单列展示，不能横向挤压图片。

## Verification

按 `docs/superpowers/specs/2026-06-08-targeted-pytest-verification.md` 跑 focused tests：

```bash
python3 scripts/pytest_related.py --base origin/master --run
```

必要时补跑：

```bash
pytest tests/test_mingkong_pairing_workbench.py -q
git diff --check
```

不跑全量 `pytest -q`，除非本次改动升级为广影响 schema/auth/deploy/scheduler/LLM/storage/billing。
