# Shopify Image Localizer TAA CDP 重连失败修复

- 创建时间：2026-05-11
- 状态：active
- 锚点：[`tools/shopify_image_localizer/CLAUDE.md#TAA reload 校验降级（2026-05-11）`](../../../tools/shopify_image_localizer/CLAUDE.md)

## 背景

Shopify Image Localizer V3.10 在处理 `omurio.com` 商品详情图时，日志显示 7/7 张详情图已经上传到 `cdn.shopify.com`，随后弹出：

```text
<urlopen error [WinError 10061] 由于目标计算机积极拒绝，无法连接。>
```

该错误来自本机连接 `http://127.0.0.1:7777/json/version`，不是 Shopify CDN 上传失败。截图时序和代码链路显示失败发生在详情图保存后的额外 reload 校验阶段：`replace_detail_images()` 先在当前 TAA 会话上传、替换 `body_html`、点击保存并读回 HTML；随后如果 `verify_reload=True`，会重新创建 `TaaSession` 再连一次 Chrome CDP。此时 Chrome CDP 端口若已退出、被安全软件拦截、或处于短暂不可用状态，就会把已完成上传和保存的任务判为失败。

## 成功口径

详情图自动成功条件沿用 `docs/superpowers/specs/2026-04-25-shopify-image-task-center-design.md`：

- TAA 后台上传图片成功并获得 Shopify CDN URL。
- `body_html` 完成整体替换并保存。
- 保存后在当前 TAA 会话重新读回 HTML，新增 CDN URL 都存在。

保存后的二次 reload 校验是诊断增强，不应覆盖上述硬成功口径。它可以发现 Shopify 后台刷新后的异常，但不能因为本机 CDP 端口瞬时拒绝连接，把已经保存成功的任务改成失败。

## 设计

在 `tools/shopify_image_localizer/rpa/taa_cdp.py::replace_detail_images()` 中保持当前流程：

1. 第一段 `TaaSession` 负责打开 TAA、读取原 HTML、上传图片、替换 HTML、保存、当前会话读回。
2. `verify.expected_new_urls_present` 默认基于当前会话读回 HTML 计算。
3. `verify_reload=True` 时继续尝试二次 `TaaSession` reload 校验。
4. 如果二次 reload 校验成功，则用 reload 后 HTML 覆盖诊断结果，并标记 `verify.reload_checked = True`。
5. 如果二次 reload 校验因为连接 CDP 或等待 TAA iframe 失败而抛异常，则捕获异常，保留当前会话读回 HTML 的诊断结果，并写入：

```json
{
  "verify": {
    "reload_checked": false,
    "reload_error": "<异常文本>"
  }
}
```

不改变上传、保存、当前会话读回失败的处理：这些仍然抛错并让任务失败。

## 非目标

- 不关闭详情图保存后的当前会话读回校验。
- 不修改 EZ 轮播图替换等待逻辑。
- 不修改登录按钮或普通 Chrome 登录流程。
- 不引入新的重试后台线程或常驻进程。

## 验证

- 新增单元测试：模拟第一次 `TaaSession` 上传、保存、读回成功；第二次 reload `TaaSession` 抛出 `ConnectionRefusedError`；断言 `replace_detail_images()` 返回 `status="done"`，且 `verify.reload_error` 有值。
- 运行：

```bash
pytest tests/test_shopify_image_localizer_batch_cdp.py -q
python -c 'import py_compile; [py_compile.compile(p, doraise=True) for p in ["tools/shopify_image_localizer/main.py","tools/shopify_image_localizer/gui.py","tools/shopify_image_localizer/controller.py","tools/shopify_image_localizer/browser/orchestrator.py","tools/shopify_image_localizer/rpa/taa_cdp.py"]]; print("ok")'
```
