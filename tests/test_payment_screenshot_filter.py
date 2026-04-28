from __future__ import annotations

from appcore.payment_screenshot_filter import is_payment_screenshot


def test_is_payment_screenshot_matches_confirmed_host_and_alt_keywords():
    assert is_payment_screenshot("https://cdn.techcloudly.com/image/demo.webp", None)
    assert is_payment_screenshot("https://cdn.example.com/payment.jpg", "Payment Methods 1")
    assert is_payment_screenshot("https://cdn.example.com/secure.jpg", "Secure Checkout")
    assert is_payment_screenshot("https://cdn.example.com/trust.jpg", "Trust Badge")
    assert is_payment_screenshot(
        "https://cdn.example.com/secure-text.jpg",
        "All transactions are secure and encrypted",
    )


def test_is_payment_screenshot_does_not_block_unconfirmed_cdn_without_alt_signal():
    assert not is_payment_screenshot("https://cdn.hotishop.com/image/product-detail.webp", "")
    assert not is_payment_screenshot("https://cdn.example.com/image/product-detail.webp", "Product detail")
