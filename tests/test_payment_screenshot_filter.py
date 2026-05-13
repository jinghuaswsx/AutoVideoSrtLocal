from __future__ import annotations

from appcore.payment_screenshot_filter import is_payment_screenshot


def test_is_payment_screenshot_does_not_block_payment_or_trust_images():
    assert not is_payment_screenshot("https://cdn.techcloudly.com/image/demo.webp", None)
    assert not is_payment_screenshot("https://cdn.example.com/payment.jpg", "Payment Methods 1")
    assert not is_payment_screenshot("https://cdn.example.com/secure.jpg", "Secure Checkout")
    assert not is_payment_screenshot("https://cdn.example.com/trust.jpg", "Trust Badge")
    assert not is_payment_screenshot(
        "https://cdn.example.com/secure-text.jpg",
        "All transactions are secure and encrypted",
    )


def test_is_payment_screenshot_does_not_block_unconfirmed_cdn_without_alt_signal():
    assert not is_payment_screenshot("https://cdn.hotishop.com/image/product-detail.webp", "")
    assert not is_payment_screenshot("https://cdn.example.com/image/product-detail.webp", "Product detail")
