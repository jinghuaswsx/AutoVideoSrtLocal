from __future__ import annotations

from pathlib import Path


def test_admin_settings_exposes_shopify_fee_toggle():
    admin_source = Path("web/routes/admin.py").read_text(encoding="utf-8")
    template_source = Path("web/templates/admin_settings.html").read_text(encoding="utf-8")

    # POST：按 checkbox 存在性写 "1"/"0" + 失效缓存
    assert 'request.form.get("shopify_dynamic_fee_enabled")' in admin_source
    assert 'set_setting("shopify_dynamic_fee_enabled"' in admin_source
    assert "invalidate_dynamic_fee_toggle_cache" in admin_source
    # GET：回显 context（仅显式 "0" 视作关）
    assert "shopify_dynamic_fee_enabled=" in admin_source
    # 模板：toggle 控件 + 文案
    assert 'name="shopify_dynamic_fee_enabled"' in template_source
    assert "手续费真实优先" in template_source
