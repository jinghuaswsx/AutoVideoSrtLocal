"""
Screenshot capture script for design reference.
Target: http://172.16.254.56:1088
"""
import time
from pathlib import Path
from playwright.sync_api import sync_playwright

SAVE_DIR = Path("g:/Code/AutoVideoSrt/docs/design-reference")
BASE_URL = "http://172.16.254.56:1088"
LOGIN_URL = f"{BASE_URL}/login"
USERNAME = "蔡靖华"
PASSWORD = "K7@pQ3L!"


def save(page, name: str, full_page: bool = False):
    path = str(SAVE_DIR / f"{name}.png")
    page.screenshot(path=path, full_page=full_page)
    print(f"  Saved: {path}")
    return path


def main():
    SAVE_DIR.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1440, "height": 900})
        page = context.new_page()

        # ── 1. Login page ─────────────────────────────────────────────────
        print("Navigating to login page...")
        page.goto(LOGIN_URL, timeout=30000, wait_until="networkidle")
        time.sleep(1)
        save(page, "01-login-page")
        print(f"  Title: {page.title()}")
        print(f"  URL:   {page.url}")

        # Inspect login form fields
        inputs = page.query_selector_all("input")
        print(f"  Found {len(inputs)} input(s):")
        for inp in inputs:
            t = inp.get_attribute("type") or "text"
            name_attr = inp.get_attribute("name") or inp.get_attribute("placeholder") or "(no name)"
            print(f"    type={t}  name/placeholder={name_attr}")

        # ── 2. Fill login form ─────────────────────────────────────────────
        print("Filling login form...")
        # Try common selectors for username field
        username_selectors = [
            "input[name='username']",
            "input[type='text']",
            "input[placeholder*='用户']",
            "input[placeholder*='账号']",
            "input[placeholder*='user']",
            "input[placeholder*='User']",
            "input:nth-of-type(1)",
        ]
        password_selectors = [
            "input[type='password']",
        ]
        submit_selectors = [
            "button[type='submit']",
            "input[type='submit']",
            "button:has-text('登录')",
            "button:has-text('Login')",
            "button:has-text('Sign')",
            ".login-btn",
            ".submit-btn",
        ]

        filled_username = False
        for sel in username_selectors:
            try:
                el = page.query_selector(sel)
                if el and el.is_visible():
                    el.fill(USERNAME)
                    filled_username = True
                    print(f"  Username filled via: {sel}")
                    break
            except Exception:
                pass

        if not filled_username:
            print("  WARNING: could not find username field")

        filled_password = False
        for sel in password_selectors:
            try:
                el = page.query_selector(sel)
                if el and el.is_visible():
                    el.fill(PASSWORD)
                    filled_password = True
                    print(f"  Password filled via: {sel}")
                    break
            except Exception:
                pass

        if not filled_password:
            print("  WARNING: could not find password field")

        # Screenshot with form filled
        save(page, "02-login-filled")

        # Submit
        submitted = False
        for sel in submit_selectors:
            try:
                el = page.query_selector(sel)
                if el and el.is_visible():
                    el.click()
                    submitted = True
                    print(f"  Submitted via: {sel}")
                    break
            except Exception:
                pass

        if not submitted:
            # Try pressing Enter
            page.keyboard.press("Enter")
            print("  Submitted via Enter key")

        # Wait for navigation
        try:
            page.wait_for_load_state("networkidle", timeout=15000)
        except Exception as e:
            print(f"  Navigation wait: {e}")
        time.sleep(2)

        print(f"  Post-login URL: {page.url}")
        save(page, "03-post-login-dashboard")

        # ── 3. Inspect layout ─────────────────────────────────────────────
        print("Inspecting layout...")
        layout_info = page.evaluate("""
        () => {
            const body = document.body;
            const style = getComputedStyle(body);

            // Try to find sidebar
            const sidebarSelectors = [
                '.sidebar', '.nav-sidebar', '.side-menu', '.left-menu',
                '.menu-wrapper', '[class*="sidebar"]', '[class*="Sidebar"]',
                'aside', '.el-aside', '.ant-layout-sider', '.van-sidebar',
            ];
            let sidebar = null;
            for (const sel of sidebarSelectors) {
                sidebar = document.querySelector(sel);
                if (sidebar) break;
            }

            // Header
            const headerSelectors = [
                'header', '.header', '.navbar', '.top-bar', '.el-header',
                '.ant-layout-header', '[class*="header"]',
            ];
            let header = null;
            for (const sel of headerSelectors) {
                header = document.querySelector(sel);
                if (header) break;
            }

            // Background colors
            const bgColor = style.backgroundColor;
            const color = style.color;
            const fontFamily = style.fontFamily;
            const fontSize = style.fontSize;

            return {
                bodyBg: bgColor,
                bodyColor: color,
                fontFamily: fontFamily,
                fontSize: fontSize,
                sidebarFound: sidebar ? sidebar.className : null,
                sidebarRect: sidebar ? sidebar.getBoundingClientRect() : null,
                headerFound: header ? header.className : null,
                headerRect: header ? header.getBoundingClientRect() : null,
                title: document.title,
                url: window.location.href,
                // All unique background colors used
            };
        }
        """)
        print("  Layout info:")
        for k, v in layout_info.items():
            print(f"    {k}: {v}")

        # ── 4. Sidebar interactions ────────────────────────────────────────
        # Try to find and screenshot the sidebar in detail
        sidebar_screenshot_taken = False

        # Check if there are any menu toggle buttons
        toggle_selectors = [
            ".hamburger", ".menu-toggle", ".sidebar-toggle",
            "[class*='toggle']", "[class*='collapse']",
            "button.el-icon-s-fold", ".fold-btn",
        ]
        for sel in toggle_selectors:
            try:
                el = page.query_selector(sel)
                if el and el.is_visible():
                    print(f"  Found toggle: {sel}")
                    save(page, "04-sidebar-expanded")
                    sidebar_screenshot_taken = True
                    break
            except Exception:
                pass

        if not sidebar_screenshot_taken:
            save(page, "04-sidebar-view", full_page=False)

        # ── 5. Navigate to other pages ─────────────────────────────────────
        print("Checking navigation links...")
        nav_links = page.evaluate("""
        () => {
            const links = [];
            // Look for nav items
            const navSelectors = [
                '.el-menu-item', '.ant-menu-item', '.van-sidebar-item',
                '.menu-item', '.nav-item', '.sidebar-item',
                'aside a', '.sidebar a', '.menu a',
            ];
            for (const sel of navSelectors) {
                const items = document.querySelectorAll(sel);
                if (items.length > 0) {
                    items.forEach(item => {
                        const text = item.textContent.trim();
                        const href = item.href || item.getAttribute('href') || '';
                        if (text && text.length < 50) {
                            links.push({ text, href, selector: sel });
                        }
                    });
                    break;
                }
            }
            return links.slice(0, 15);
        }
        """)
        print(f"  Found {len(nav_links)} nav links:", flush=True)
        for link in nav_links:
            try:
                print(f"    [{link['text']}] -> {link['href']}")
            except UnicodeEncodeError:
                print(f"    [<nav item>] -> {link['href']}")

        # Visit up to 4 nav pages
        current_url = page.url
        visited = {current_url}
        shot_count = 5

        for link in nav_links[:8]:
            href = link.get("href", "")
            if not href or href == "#" or href.startswith("javascript"):
                # Try clicking by text
                try:
                    els = page.locator(f"text={link['text']}").all()
                    for el in els:
                        if el.is_visible():
                            el.click()
                            time.sleep(1.5)
                            new_url = page.url
                            if new_url not in visited:
                                visited.add(new_url)
                                name = f"{shot_count:02d}-nav-{link['text'][:20].replace('/', '-')}"
                                save(page, name)
                                shot_count += 1
                                page.go_back()
                                time.sleep(1)
                            break
                except Exception as e:
                    print(f"  Click error on [{link['text']}]: {e}")
            elif href not in visited and href.startswith("http"):
                try:
                    page.goto(href, timeout=15000, wait_until="networkidle")
                    time.sleep(1.5)
                    new_url = page.url
                    visited.add(new_url)
                    name = f"{shot_count:02d}-nav-{link['text'][:20].replace('/', '-')}"
                    save(page, name)
                    shot_count += 1
                    page.go_back()
                    time.sleep(1)
                except Exception as e:
                    print(f"  Nav error: {e}")

            if shot_count >= 10:
                break

        # Full page screenshot of dashboard
        page.goto(current_url, timeout=15000, wait_until="networkidle")
        time.sleep(1)
        save(page, "99-dashboard-fullpage", full_page=True)

        # ── 6. Collect CSS design tokens ───────────────────────────────────
        print("Collecting design tokens...")
        tokens = page.evaluate("""
        () => {
            const result = {};
            const allEls = document.querySelectorAll('*');
            const colorCounts = {};
            const bgCounts = {};

            // Sample first 200 elements for colors
            for (let i = 0; i < Math.min(allEls.length, 200); i++) {
                const s = getComputedStyle(allEls[i]);
                const c = s.color;
                const bg = s.backgroundColor;
                if (c && c !== 'rgba(0, 0, 0, 0)') colorCounts[c] = (colorCounts[c] || 0) + 1;
                if (bg && bg !== 'rgba(0, 0, 0, 0)') bgCounts[bg] = (bgCounts[bg] || 0) + 1;
            }

            // Get top colors
            result.topColors = Object.entries(colorCounts)
                .sort((a, b) => b[1] - a[1])
                .slice(0, 10)
                .map(([color, count]) => ({ color, count }));
            result.topBgColors = Object.entries(bgCounts)
                .sort((a, b) => b[1] - a[1])
                .slice(0, 10)
                .map(([color, count]) => ({ color, count }));

            // Buttons
            const buttons = document.querySelectorAll('button, .btn, [class*="btn"], [class*="button"]');
            const btnStyles = [];
            for (let i = 0; i < Math.min(buttons.length, 5); i++) {
                const s = getComputedStyle(buttons[i]);
                btnStyles.push({
                    text: buttons[i].textContent.trim().slice(0, 30),
                    bg: s.backgroundColor,
                    color: s.color,
                    borderRadius: s.borderRadius,
                    fontSize: s.fontSize,
                    fontWeight: s.fontWeight,
                });
            }
            result.buttons = btnStyles;

            return result;
        }
        """)
        print("  Top text colors:", tokens.get("topColors", [])[:5])
        print("  Top bg colors:", tokens.get("topBgColors", [])[:5])
        print("  Buttons:", tokens.get("buttons", []))

        browser.close()
        print("\nDone. All screenshots saved to:", str(SAVE_DIR))
        return layout_info, tokens, nav_links


if __name__ == "__main__":
    main()
