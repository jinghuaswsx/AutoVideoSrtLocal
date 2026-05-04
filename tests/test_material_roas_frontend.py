from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PARTIAL = ROOT / "web" / "templates" / "medias" / "_roas_form.html"


def test_medias_list_has_roas_modal_mount():
    html = (ROOT / "web" / "templates" / "medias_list.html").read_text(encoding="utf-8")
    partial = PARTIAL.read_text(encoding="utf-8")

    # Outer shell stays in medias_list.html
    assert 'id="roasModalMask"' in html
    assert "独立站保本 ROAS" in html

    # Field-level content lives in the partial
    assert 'id="roasForm"' in partial
    assert "TK 可选项" in partial


def test_medias_js_wires_roas_button_and_calculation():
    js = (ROOT / "web" / "static" / "medias.js").read_text(encoding="utf-8")

    assert "data-roas" in js
    assert "openRoasModal" in js
    assert "calculateRoasBreakEven" in js
    assert "roasCalculateBtn" in js
    assert "packet_cost_actual" in js
    assert "standalone_shipping_fee" in js
    assert "MATERIAL_ROAS_RMB_PER_USD" in js
    assert "roas_calculation" in js


def test_roas_modal_splits_site_and_tk_fields_into_single_column_sections():
    html = PARTIAL.read_text(encoding="utf-8")

    assert 'class="oc-roas-layout"' in html
    assert 'id="roasSiteSection"' in html
    assert 'id="roasTkSection"' in html

    site_section = html.split('id="roasSiteSection"', 1)[1].split('id="roasTkSection"', 1)[0]
    tk_section = html.split('id="roasTkSection"', 1)[1].split("</section>", 1)[0]
    site_fields = site_section.split('<div class="oc-roas-field-list">', 1)[1].split(
        "              </div>\n            </section>", 1
    )[0]

    assert 'data-roas-field="standalone_shipping_fee"' in site_section
    assert "采购价格 (RMB)" in site_section
    assert "预估小包成本 (RMB)" in site_section
    assert "实际小包成本 (RMB)" in site_section
    assert "独立站售价 (USD)" in site_section
    assert "用户支付运费 (USD)" in site_section
    assert site_section.index('data-roas-field="purchase_1688_url"') < site_section.index(
        'data-roas-field="standalone_price"'
    )
    assert site_fields.index('data-roas-field="package_height_cm"') < site_fields.index(
        'data-roas-field="packet_cost_estimated"'
    )
    assert site_fields.index('data-roas-field="packet_cost_estimated"') < site_fields.index(
        'data-roas-field="packet_cost_actual"'
    )
    assert site_fields.rstrip().endswith('data-roas-field="packet_cost_actual" type="number" min="0" step="0.01"></div>')
    assert 'class="oc-roas-field-list"' in site_section
    assert 'class="oc-roas-field-list"' in tk_section

    for field in (
        "purchase_price",
        "packet_cost_estimated",
        "packet_cost_actual",
        "package_length_cm",
        "package_width_cm",
        "package_height_cm",
        "standalone_price",
        "standalone_shipping_fee",
    ):
        field_markup = site_section.split(f'data-roas-field="{field}"', 1)[0].rsplit("<input", 1)[1]
        assert "required" not in field_markup

    for field in ("tk_sea_cost", "tk_air_cost", "tk_sale_price"):
        field_markup = tk_section.split(f'data-roas-field="{field}"', 1)[0].rsplit("<input", 1)[1]
        assert "required" not in field_markup


def test_roas_modal_uses_manual_calculate_button_and_injected_exchange_rate():
    html = (ROOT / "web" / "templates" / "medias_list.html").read_text(encoding="utf-8")
    partial = PARTIAL.read_text(encoding="utf-8")
    js = (ROOT / "web" / "static" / "medias.js").read_text(encoding="utf-8")

    # These field-level elements live in the partial
    assert 'id="roasCalculateBtn"' in partial
    assert "计算 ROAS" in partial
    assert "material_roas_rmb_per_usd" in partial

    # The JS injection lives in medias_list.html
    assert "window.MATERIAL_ROAS_RMB_PER_USD" in html

    assert "roasCalculateBtn" in js
    assert "markRoasResultDirty" in js
    assert "input.addEventListener('input', markRoasResultDirty)" in js
    assert "input.addEventListener('input', renderRoasResult)" not in js
    assert "markRoasResultDirty();\n    mask.hidden = false;" in js
    roas_js = js[js.index("function renderRoasResult"):js.index("function closeRoasModal")]
    assert "reportValidity" not in roas_js


def test_roas_modal_fills_main_area_outside_sidebar():
    html = (ROOT / "web" / "templates" / "medias_list.html").read_text(encoding="utf-8")
    styles = (ROOT / "web" / "templates" / "medias" / "_roas_styles.html").read_text(encoding="utf-8")

    # Outer shell element remains in medias_list.html
    assert 'id="roasModalMask"' in html
    # CSS moved to shared partial
    assert "inset:0 0 0 220px" in styles
    assert "align-items:stretch" in styles
    roas_modal_css = styles.split(".oc-roas-modal {", 1)[1].split("}", 1)[0]
    assert "width:100%" in roas_modal_css
    assert "height:100%" in roas_modal_css


def test_roas_modal_uses_full_height_scroll_area_and_tighter_field_spacing():
    styles = (ROOT / "web" / "templates" / "medias" / "_roas_styles.html").read_text(encoding="utf-8")

    form_css = styles.split("#roasForm {", 1)[1].split("}", 1)[0]
    layout_css = styles.split(".oc-roas-layout {", 1)[1].split("}", 1)[0]
    column_css = styles.split(".oc-roas-column {", 1)[1].split("}", 1)[0]
    field_list_css = styles.split(".oc-roas-field-list {", 1)[1].split("}", 1)[0]
    field_label_css = styles.split(".oc-roas-field label {", 1)[1].split("}", 1)[0]

    assert "display:flex" in form_css
    assert "flex:1 1 auto" in form_css
    assert "min-height:0" in form_css
    assert "height:100%" in layout_css
    assert "align-items:stretch" in layout_css
    assert "overflow-y:auto" in column_css
    assert "height:100%" in column_css
    assert "max-height:min(60vh, 640px)" not in column_css
    assert "gap:var(--oc-sp-2)" in field_list_css
    assert "margin-bottom:3px" in field_label_css


def test_roas_modal_embeds_average_shipping_in_bottom_half_of_tk_column():
    styles = (ROOT / "web" / "templates" / "medias" / "_roas_styles.html").read_text(encoding="utf-8")
    partial = PARTIAL.read_text(encoding="utf-8")
    js = (ROOT / "web" / "static" / "medias.js").read_text(encoding="utf-8")

    # HTML elements live in partial
    assert 'class="oc-roas-column oc-roas-tk-column"' in partial
    assert 'id="roasAverageShippingSection"' in partial
    assert ">平均运费计算器</h4>" in partial
    assert 'id="roasAverageShippingInput"' in partial
    assert 'id="roasAverageShippingResult"' in partial
    assert 'id="roasAverageShippingMeta"' in partial

    tk_column = partial.split('class="oc-roas-column oc-roas-tk-column"', 1)[1].split("</form>", 1)[0]
    assert tk_column.index('id="roasTkSection"') < tk_column.index('id="roasAverageShippingSection"')

    # CSS lives in the shared styles partial
    tk_column_css = styles.split(".oc-roas-tk-column {", 1)[1].split("}", 1)[0]
    half_section_css = styles.split(".oc-roas-tk-column .oc-roas-section {", 1)[1].split("}", 1)[0]
    avg_heading_css = styles.split(".oc-roas-avg-head h4 {", 1)[1].split("}", 1)[0]
    avg_input_css = styles.split(".oc-roas-avg-input {", 1)[1].split("}", 1)[0]
    assert "overflow:hidden" in tk_column_css
    assert "flex:1 1 0" in half_section_css
    assert "min-height:0" in half_section_css
    assert "font-size:28px" in avg_heading_css
    assert "font-weight:700" in avg_heading_css
    assert "color:var(--oc-accent)" in avg_heading_css
    assert "flex:1 1 auto" in avg_input_css

    assert "calculateAverageShippingText" in js
    assert "updateRoasAverageShipping" in js
    assert "roasAverageShippingInput" in js
    assert "addEventListener('input', updateRoasAverageShipping)" in js


def test_roas_button_is_after_ai_evaluate_button():
    js = (ROOT / "web" / "static" / "medias.js").read_text(encoding="utf-8")
    row_actions = js.split('<div class="oc-row-actions">', 1)[1].split("</div>", 1)[0]

    assert row_actions.index("data-ai-evaluate") < row_actions.index("data-roas")


def test_modal_head_contains_open_in_page_pill_button():
    html = (ROOT / "web" / "templates" / "medias_list.html").read_text(encoding="utf-8")
    assert 'id="roasOpenInPage"' in html
    assert 'class="oc-btn pill ghost"' in html or 'class="oc-btn ghost pill"' in html
    assert 'target="_blank"' in html
    assert 'rel="noopener"' in html
    assert "在新页面打开" in html


def test_modal_has_status_bar():
    html = (ROOT / "web" / "templates" / "medias_list.html").read_text(encoding="utf-8")
    assert 'class="oc-roas-status-bar"' in html
    assert 'data-roas-status' in html


def test_modal_footer_no_longer_has_save_button():
    html = (ROOT / "web" / "templates" / "medias_list.html").read_text(encoding="utf-8")
    assert 'id="roasSaveBtn"' not in html
    assert 'id="roasSaveMsg"' not in html


def test_medias_list_loads_roas_form_script():
    html = (ROOT / "web" / "templates" / "medias_list.html").read_text(encoding="utf-8")
    assert "roas_form.js" in html


def test_medias_js_uses_controller_class():
    js = (ROOT / "web" / "static" / "medias.js").read_text(encoding="utf-8")
    assert "RoasFormController" in js
    assert "new RoasFormController" in js
    # 旧函数应已被替换或移除
    assert "async function saveRoas" not in js
