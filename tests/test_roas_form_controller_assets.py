from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
JS = ROOT / "web" / "static" / "roas_form.js"


def test_file_exists():
    assert JS.exists()


def test_exposes_controller_class():
    src = JS.read_text(encoding="utf-8")
    assert "class RoasFormController" in src
    assert "window.RoasFormController = RoasFormController" in src


def test_controller_implements_required_methods():
    src = JS.read_text(encoding="utf-8")
    for method in (
        "fillFromProduct",
        "collectPayload",
        "computeRoas",
        "renderResult",
        "save",
        "_setStatus",
        "_scheduleAutoSave",
    ):
        assert method in src, f"missing method {method}"


def test_controller_uses_600ms_debounce():
    src = JS.read_text(encoding="utf-8")
    assert "600" in src and "setTimeout" in src


def test_controller_targets_correct_endpoint_and_field_names():
    src = JS.read_text(encoding="utf-8")
    assert "/medias/api/products/" in src
    for field in (
        "purchase_1688_url",
        "purchase_price",
        "packet_cost_estimated",
        "packet_cost_actual",
        "package_length_cm",
        "package_width_cm",
        "package_height_cm",
        "tk_sea_cost",
        "tk_air_cost",
        "tk_sale_price",
        "standalone_price",
        "standalone_shipping_fee",
    ):
        assert f'"{field}"' in src or f"'{field}'" in src, f"missing field {field}"


def test_controller_handles_last_write_wins():
    src = JS.read_text(encoding="utf-8")
    assert "_pendingPayload" in src or "pendingPayload" in src
    assert "_inFlight" in src or "inFlight" in src


def test_controller_status_states_present():
    src = JS.read_text(encoding="utf-8")
    for state in ("saving", "saved", "error", "idle"):
        assert f"'{state}'" in src or f'"{state}"' in src
