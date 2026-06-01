# Pytest Shopify Browser Guard

Shopify Image Localizer tests must not launch or reuse a real local Chrome
session during normal `pytest` runs. Unit tests may exercise URL building,
mapping, task claiming, and RPA orchestration decisions, but page-opening
helpers such as CDP Chrome startup, Shopify admin preload, and storefront
display-size probing are treated as integration/browser behavior.

Default pytest behavior:

- Do not start `chrome.exe`.
- Do not open Shopify admin pages.
- Do not connect to a real CDP endpoint.
- Return deterministic no-op results for page preloading and display-size
  probes unless a test explicitly opts into a fully mocked browser helper.

Tests that intentionally validate the browser helper itself must use fakes for
Chrome/CDP/Playwright and opt in with a dedicated pytest marker.
