from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_edit_language_tabs_are_outside_scrollable_body():
    html = (ROOT / "web" / "templates" / "_medias_edit_detail_modal.html").read_text(
        encoding="utf-8"
    )

    body_start = html.index('<div class="oc-modal-body oc-edit-form">')
    langbar_start = html.index('<div class="oc-modal-langbar">')
    tabs_index = html.index('id="edLangTabs"')

    assert langbar_start < tabs_index < body_start
