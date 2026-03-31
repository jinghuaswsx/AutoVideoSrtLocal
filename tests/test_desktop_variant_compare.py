"""Tests for VariantCompareWidget."""
import sys
import pytest
from PySide6.QtWidgets import QApplication


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication(sys.argv)


def test_both_panels_created(qapp):
    from desktop.widgets.variant_compare import VariantCompareWidget
    from desktop.widgets.artifact_preview import ArtifactPreviewWidget
    w = VariantCompareWidget()
    assert isinstance(w.normal_preview, ArtifactPreviewWidget)
    assert isinstance(w.hook_preview, ArtifactPreviewWidget)


def test_set_text_populates_both_panels(qapp):
    from desktop.widgets.variant_compare import VariantCompareWidget
    w = VariantCompareWidget()
    w.set_text("normal text", "hook text")
    # Both previews should be on the text page (index 1)
    assert w.normal_preview.currentIndex() == 1
    assert w.hook_preview.currentIndex() == 1
    assert w.normal_preview._text_view.toPlainText() == "normal text"
    assert w.hook_preview._text_view.toPlainText() == "hook text"
