# 广告预警卡片级亏损 AD 预览 — Codex 执行指引

在每张预警卡片上直接展示亏损最严重的 AD，并支持卡片级 AI 评估。

## 操作步骤

```bash
git checkout master && git pull
git checkout -b feature/ad-warning-module origin/feature/ad-warning-module
```

```bash
cat docs/superpowers/plans/2026-06-12-ad-alert-top-losing-ads-plan.md
```

按 Task 1→2→3 顺序执行，每步做完 commit：

| Task | 文件 | 改动 |
|------|------|------|
| 1 | `appcore/ad_alerts.py` | AlertItem 新增 top_losing_ads、_get_top_losing_ads()、get_alerts() 填充 |
| 2 | `web/routes/ad_alerts.py` | _alert_item_to_dict() 序列化新字段 |
| 3 | `web/templates/ad_alerts.html` | 卡片渲染亏损 AD + AI 评估按钮 + 浮层结果 + CSS |

```bash
git push origin feature/ad-warning-module
```
