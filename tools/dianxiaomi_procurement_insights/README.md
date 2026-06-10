# 店小秘采购洞察

Docs-anchor: ../../docs/superpowers/specs/2026-06-09-dianxiaomi-procurement-insights-extension-design.md

首版 Chrome 插件目录：

```text
tools/dianxiaomi_procurement_insights/chrome_ext
```

## 本地安装

1. 打开 Chrome `chrome://extensions/`。
2. 开启“开发者模式”。
3. 点击“加载已解压的扩展程序”。
4. 选择 `tools/dianxiaomi_procurement_insights/chrome_ext`。
5. 打开插件 popup，确认后台地址。默认是 `http://172.16.254.106`。
6. 先在 AutoVideoSrtLocal 后台登录，再进入店小秘云仓采购建议 / 缺货建议页面刷新插件面板。

## 首版行为

- 在店小秘页面右侧注入“采购洞察”面板。
- 自动读取当前鼠标所在表格行或页面可见文本里的 SKU / 商品编码 / 商品名线索。
- 请求 `/dianxiaomi-procurement-insights/api/insights`。
- 展示投放状态、今日订单、昨日订单、近 7 天订单和真实 ROAS。
- 真实页面入口确认后，再补页面专用选择器。

## 发布

发布流程遵循：

```text
docs/superpowers/specs/2026-06-09-chrome-extension-tool-release-standard.md
```

生产服务器 `/opt/autovideosrt` 普通 `master` checkout 中执行：

```bash
./venv/bin/python scripts/build_chrome_extension_release.py \
  --release-standard-read \
  --tool dianxiaomi_procurement_insights \
  --version 1.0.0 \
  --release-note "店小秘采购洞察首版"
```
