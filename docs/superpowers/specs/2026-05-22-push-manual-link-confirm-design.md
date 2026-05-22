# 推送管理人工确认链接正常设计

- 日期：2026-05-22
- 范围：`/pushes` 推送管理弹窗、`/pushes/api/items/<id>/payload`、`/pushes/api/items/<id>/push`
- 上位文档：`2026-04-18-push-management-design.md`

## 背景

推送管理在加载载荷和正式推送前都会对产品链接做服务端 HEAD 探活。实际访问正常但探活返回 503 时，弹窗只能显示“载荷加载失败”，管理员无法进入下一步，整条素材推送流程被卡住。

## 设计

保留自动探活作为默认保护。仅当管理员在弹窗中看到 `link_not_adapted` 后点击“人工确认链接正常”时，当前这次操作允许跳过服务端链接探活：

1. 载荷接口支持 `manual_link_confirmed=1` 查询参数，仍校验管理员、素材就绪、产品上架和 payload 可组装，只跳过 `probe_ad_url()`。
2. 正式素材推送接口支持 JSON 字段 `manual_link_confirmed: true`，与载荷接口保持一致，只跳过探活，不跳过其他 readiness、文案、下游推送和日志逻辑。
3. 前端弹窗在 `link_not_adapted` 错误下展示链接、错误详情和“人工确认链接正常”按钮。点击后重新加载载荷；之后点“推送素材”时带上同一个确认标记。
4. 后端审计日志在成功或失败推送中记录 `manual_link_confirmed`，便于之后追溯人工越过探活的操作。

## 非目标

- 不持久化“链接已人工确认”的状态。
- 不把该产品/语种自动改成已适配。
- 不关闭默认 HEAD 探活。
- 不允许普通用户绕过探活。

## 验证

- `payload` 默认探活失败仍返回 `link_not_adapted`。
- `payload?manual_link_confirmed=1` 在同样探活失败条件下可以返回载荷。
- `/push` 默认探活失败仍拒绝；携带 `manual_link_confirmed: true` 时可以继续执行下游推送。
- 前端资源包含人工确认按钮、重试载荷参数和推送请求体标记。
