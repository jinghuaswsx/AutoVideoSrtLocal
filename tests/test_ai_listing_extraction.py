"""Tests for AI listing transit page extraction."""
from __future__ import annotations

import pytest
from appcore.ai_listing_service import extract_outbound_links_heuristic


def test_extract_outbound_links_heuristic_with_ecommerce():
    html = """
    <html>
        <body>
            <h1>Check out this amazing product!</h1>
            <p>Read our blog here. We recommend this item.</p>
            <a href="https://social.facebook.com/share">Share on Facebook</a>
            <a href="https://example-shop.com/products/facial-hair-removal-cream" class="btn">Shop Now</a>
            <a href="https://example-shop.com/pages/contact-us">Contact Us</a>
        </body>
    </html>
    """
    base_url = "https://myblog.com/post-1"
    candidates = extract_outbound_links_heuristic(html, base_url)
    
    assert len(candidates) >= 1
    assert candidates[0] == "https://example-shop.com/products/facial-hair-removal-cream"


def test_extract_outbound_links_heuristic_scores_button_text():
    html = """
    <html>
        <body>
            <a href="https://example-shop.com/checkout">Checkout Here</a>
            <a href="https://example-shop.com/products/some-item">Shop Now</a>
        </body>
    </html>
    """
    base_url = "https://myblog.com/post-1"
    candidates = extract_outbound_links_heuristic(html, base_url)
    
    assert len(candidates) == 2
    assert "https://example-shop.com/products/some-item" in candidates
    assert "https://example-shop.com/checkout" in candidates

