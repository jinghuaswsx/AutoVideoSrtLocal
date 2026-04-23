from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_edit_video_material_add_form_lives_in_popup():
    tpl = (ROOT / "web/templates/_medias_edit_detail_modal.html").read_text(encoding="utf-8")
    css = (ROOT / "web/templates/medias_list.html").read_text(encoding="utf-8")
    js = (ROOT / "web/static/medias.js").read_text(encoding="utf-8")

    assert 'id="edNewItemOpenBtn"' in tpl
    assert "新增视频素材" in tpl
    assert 'id="edNewItemMask"' in tpl
    assert tpl.index('id="edNewItemMask"') < tpl.index('id="edNewItemBox"')
    assert tpl.index('id="edItemsGrid"') < tpl.index('id="edNewItemMask"')

    assert ".oc-new-item-open-btn" in css
    assert "font-size:26px" in css

    assert "edOpenNewItemModal" in js
    assert "edCloseNewItemModal" in js
    assert "edNewItemOpenBtn" in js
