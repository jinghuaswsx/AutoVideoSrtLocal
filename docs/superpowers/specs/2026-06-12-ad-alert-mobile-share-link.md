# 2026-06-12 — 广告预警移动端与高额亏损分享链接

## 背景

`/ad-alerts` 已有三个一级 Tab：高额亏损广告、广告预警、问题广告。其中「问题广告」下还有 `Campaign / Ad Set / Ad` 三个维度表。运营需要在手机上查看这三个问题广告子表，同时需要把「高额亏损广告」结果通过公网链接分享给未登录访问者。

## 目标

1. 「问题广告」下 `Campaign / Ad Set / Ad` 三个子表在移动端可读、可点击，不出现文字互相挤压或 200px 主图撑爆表格。
2. 「高额亏损广告」页面提供生成分享链接入口。
3. 分享链接面向公网访问，链接必须携带签名 token 和过期时间。
4. 公网基准地址复用服务器现有反向 SSH 隧道：本地生产 80 端口经 `ubuntuserver-selected-web-tunnels.service` 映射到公网机，公网 nginx 以 `http://14.103.60.217/` 代理到本地生产。

## 移动端口径

- 桌面端保留现有宽表和列自定义能力。
- 小屏下隐藏问题广告表头，把每个 `<tr>` 变成卡片式分块：
  - 广告信息：名称、code、中文产品名、复制/搜索按钮。
  - 商品主图：缩小到固定移动端尺寸。
  - 指标组：今天、昨天、最近 7 天、最近 30 天、整体，每组展示消耗、成效、ROAS。
  - 账户与首投日期。
- 小屏下仍遵守列自定义，隐藏的时间窗口不渲染为可见指标块。
- 行点击仍跳转广告分析详情；复制和素材搜索按钮不触发行跳转。

## 分享链接口径

- 新增登录态管理员 API：

```text
POST /ad-alerts/api/high-loss-ads/share
```

请求可选：

```jsonc
{
  "q": "搜索词",
  "limit": 30,
  "expires_in_hours": 24
}
```

返回：

```jsonc
{
  "share_url": "http://14.103.60.217/ad-alerts/share/high-loss?token=...&expires=2026-06-13T12:00:00Z",
  "expires_at": "2026-06-13T12:00:00Z",
  "expires_in_hours": 24
}
```

- 公开访问入口：

```text
GET /ad-alerts/share/high-loss?token=...&expires=...
```

- token 为服务端签名 payload，至少绑定：
  - `scope = "ad_alert_high_loss"`
  - `q`
  - `limit`
  - `expires_at`
- 公开入口只展示高额亏损广告只读结果，不暴露其它管理后台数据，不调用需要登录的 JSON API。
- token 缺失、签名错误、scope 不匹配、已过期，均返回 403。

## 非目标

- 不新增数据库表。
- 不开放「问题广告」公开分享。
- 不绕过已有管理页和 API 的 `@login_required + @admin_required`。
- 不新增公网隧道或重启隧道服务；仅复用现有公网入口。

## 验证

Focused tests：

```bash
python3 scripts/pytest_related.py --base origin/master --run
```

若选择器没有覆盖，至少运行：

```bash
pytest tests/test_ad_alert_routes.py tests/test_ad_alert_template.py tests/test_ad_alerts.py -q
```

手动/自动 smoke：

1. 未登录访问 `/ad-alerts/` 仍 302。
2. 未登录访问有效 `/ad-alerts/share/high-loss?...` 返回 200。
3. 过期或篡改 token 返回 403。
4. 移动端 390px 宽度下，`Campaign / Ad Set / Ad` 三个问题广告子表可读、无明显重叠。
