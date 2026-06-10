# Mingkong Monitor Release Verification

Date: 2026-06-10

## Context

The Mingkong outbound request rate monitor was merged to `master` for production release. Release-level full pytest on current `origin/master` surfaced five failures before deployment:

1. Three product-link push tests touched DB settings while deciding whether an outbound POST should be counted by the Mingkong monitor.
2. The Dianxiaomi procurement insight content script hardcoded the current server IP instead of using the extension backend configuration path.
3. One Xuanpin/TABCUT route test still asserted the legacy `tabcutTaskModal` DOM contract while the page now renders the shared `mkiXiaoModal` task dialog.

## Resolution

- Mingkong URL counting in `appcore.pushes` must not read DB-backed settings during the HTTP boundary decision.
- The procurement insight content script must read `backendBase` from the existing extension background configuration before rendering backend deep links.
- The Xuanpin/TABCUT route assertion follows the current `mkiXiaoModal` task dialog contract already covered by `tests/test_tabcut_selection_routes.py`.

## Verification

Targeted failing tests:

```bash
/opt/autovideosrt/venv/bin/python -m pytest \
  tests/test_product_link_push.py::test_push_unsuitable_product_posts_to_text_and_link_endpoints \
  tests/test_product_link_push.py::test_push_unsuitable_product_can_post_only_copy_type \
  tests/test_product_link_push.py::test_push_unsuitable_product_can_post_only_links_type \
  tests/test_server_ip_hardcoding.py::test_server_ips_are_not_hardcoded_outside_global_config \
  tests/test_xuanpin_routes.py::test_xuanpin_tabcut_page_uses_xuanpin_tabs_and_api -q
```

Full release gate:

```bash
/opt/autovideosrt/venv/bin/python -m pytest -q
```
