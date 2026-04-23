from pathlib import Path


def test_pushes_template_contains_mk_id_column():
    template = Path("web/templates/pushes_list.html").read_text(encoding="utf-8")

    assert "<th>mk_id</th>" in template


def test_pushes_script_renders_product_link_and_copy_button():
    script = Path("web/static/pushes.js").read_text(encoding="utf-8")

    assert "product_page_url" in script
    assert "mk_id" in script
    assert "data-copy-product-code" in script
    assert "navigator.clipboard" in script
    assert "document.execCommand('copy')" in script


def test_pushes_css_styles_product_link_and_copy_button():
    css = Path("web/static/pushes.css").read_text(encoding="utf-8")

    assert ".product-link" in css
    assert ".product-copy-btn" in css
