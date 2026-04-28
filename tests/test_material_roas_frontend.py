from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_medias_list_has_roas_modal_mount():
    html = (ROOT / "web" / "templates" / "medias_list.html").read_text(encoding="utf-8")

    assert 'id="roasModalMask"' in html
    assert 'id="roasForm"' in html
    assert "独立站保本 ROAS" in html
    assert "TK 可选项" in html


def test_medias_js_wires_roas_button_and_calculation():
    js = (ROOT / "web" / "static" / "medias.js").read_text(encoding="utf-8")

    assert "data-roas" in js
    assert "openRoasModal" in js
    assert "calculateRoasBreakEven" in js
    assert "packet_cost_actual" in js
    assert "roas_calculation" in js
