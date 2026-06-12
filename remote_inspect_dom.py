import sys
from playwright.sync_api import sync_playwright
from bs4 import BeautifulSoup

sys.path.insert(0, '/opt/autovideosrt-test')

url = "https://newjoyloo.com/it/products/effortless-precision-toenail-trimmer-rjc"

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page()
    page.goto(url, wait_until="domcontentloaded")
    page.wait_for_timeout(5000)
    
    html = page.content()
    soup = BeautifulSoup(html, "html.parser")
    
    print("--- SEARCHING FOR IMAGES MATCHING 397272e4c57da3bde45de1a933041431 ---")
    for idx, node in enumerate(soup.find_all("img")):
        outer_html = str(node)
        if "397272e4c57da3bde45de1a933041431" in outer_html:
            print(f"\nImg #{idx}:")
            print("  Tag:", outer_html)
            print("  Parent:", [parent.name for parent in node.parents][:4])
            # Check selectors
            parents_classes = []
            for p_node in node.parents:
                if p_node.name == "body":
                    break
                cls = p_node.get("class")
                if cls:
                    parents_classes.append(f"{p_node.name}.{'.'.join(cls)}")
                else:
                    parents_classes.append(p_node.name)
            print("  Parent Path:", " > ".join(reversed(parents_classes)))
            
    browser.close()
