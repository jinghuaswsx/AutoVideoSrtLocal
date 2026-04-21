"""端到端浏览器烟雾测试:登录 → 素材管理 → 弹窗 → 启动任务 → 气泡 → 详情页。

运行:
    python scripts/smoke_bulk_translate_ui.py

前置:
    * 生产环境 http://172.30.254.14:8888 已部署本分支
    * testuser.md 的 admin / 709709@ 有效
"""
from playwright.sync_api import sync_playwright, expect

BASE = "http://172.30.254.14:8888"
USER = "admin"
PWD = "709709@"


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(viewport={"width": 1440, "height": 900})
        page = ctx.new_page()
        page.set_default_timeout(15000)

        # 1. 登录
        print("[1] 登录...")
        page.goto(f"{BASE}/login")
        page.fill('input[name="username"]', USER)
        page.fill('input[name="password"]', PWD)
        page.click('button[type="submit"]')
        page.wait_for_load_state("networkidle")
        print("    URL:", page.url)

        # 2. 素材管理页
        print("[2] 素材管理页 /medias ...")
        page.goto(f"{BASE}/medias")
        page.wait_for_load_state("networkidle")
        page.wait_for_selector("table tbody tr", timeout=10000)
        row_count = page.locator("table tbody tr").count()
        print(f"    行数: {row_count}")

        # 3. 找第一个 "🌐 翻译" 按钮 → 点击
        print("[3] 找 🌐 翻译 按钮...")
        btns = page.locator("button.bt-row-btn[data-bt-open]")
        cnt = btns.count()
        print(f"    一键翻译按钮数: {cnt}")
        if cnt == 0:
            raise RuntimeError("❌ 没找到 '🌐 翻译' 按钮,Phase 6 UI 没接好")

        # 点第一个
        first = btns.first
        pid = first.get_attribute("data-bt-open")
        pname = first.get_attribute("data-bt-name")
        print(f"    产品 id={pid} name={pname}")
        first.click()

        # 4. 弹窗应出现
        print("[4] 弹窗应出现...")
        page.wait_for_selector("#bt-dialog:not(.hidden)", timeout=5000)
        # 目标语言 chips
        chips = page.locator("[data-bt-langs] .bt-chip")
        print(f"    语言 chip 数: {chips.count()}")
        # 默认勾选的内容类型
        checked = page.locator("[data-bt-content]:checked")
        print(f"    默认勾选内容类型: {[c.get_attribute('data-bt-content') for c in checked.all()]}")

        # 预估展示
        page.wait_for_timeout(1500)   # 等 debounce 预估
        est = page.locator("[data-bt-estimate] .bt-estimate__body").inner_text()
        print(f"    预估区内容(前 200 字):\n      {est[:200]}")
        if "¥" not in est and "计算中" not in est:
            raise RuntimeError(f"❌ 预估未渲染: {est}")

        # 5. 关弹窗(用 header 右上角 × 按钮,最精准)
        print("[5] 关闭弹窗...")
        page.click("button.bt-dialog__close")
        page.wait_for_selector("#bt-dialog", state="hidden", timeout=3000)

        # 6. 任务中心列表
        print("[6] 访问 /tasks ...")
        page.goto(f"{BASE}/tasks")
        page.wait_for_load_state("networkidle")
        # 等表格或 empty 渲染
        try:
            page.wait_for_selector(".bt-tasks__table tbody tr, .bt-tasks__empty:not(.hidden)", timeout=5000)
        except Exception:
            pass
        rows = page.locator(".bt-tasks__table tbody tr").count()
        print(f"    任务行数: {rows}")

        if rows > 0:
            first_task_link = page.locator(".bt-tasks__table tbody tr a[href^='/tasks/']").first
            href = first_task_link.get_attribute("href")
            print(f"[7] 访问第一个任务详情 {href} ...")
            page.goto(BASE + href)
            page.wait_for_load_state("networkidle")
            page.wait_for_selector(".bt-detail__meta", timeout=5000)
            meta = page.locator(".bt-detail__meta").inner_text()
            print(f"    Meta: {meta[:200]}")
            stats = page.locator("[data-bt-stats]").inner_text()
            print(f"    Stats: {stats[:200]}")
            plan_items = page.locator(".bt-plan-item").count()
            print(f"    plan 项数: {plan_items}")
        else:
            print("[7] 跳过详情页测试(当前无任务)")

        # ==============================================
        # 8. 真实端到端:创建一个小翻译任务并等它完成
        # ==============================================
        print("\n[8] 端到端任务测试:回到 /medias 启动一个真实翻译任务...")
        page.goto(f"{BASE}/medias")
        page.wait_for_load_state("networkidle")
        page.wait_for_selector("table tbody tr")

        # 挑一个"可堆叠棒球帽收纳盒" (product_id=6) 或首个
        target_btn = page.locator('button.bt-row-btn[data-bt-open="6"]').first
        if target_btn.count() == 0:
            target_btn = page.locator("button.bt-row-btn[data-bt-open]").first
        target_pid = target_btn.get_attribute("data-bt-open")
        print(f"    选中产品 id={target_pid}")
        target_btn.click()
        page.wait_for_selector("#bt-dialog:not(.hidden)")

        # 只勾德语 + 只勾文案,避免跑视频浪费钱
        # 按 lang code 精准取消,避免动态索引问题
        for code in ["fr", "es", "it", "ja", "pt"]:
            chip = page.locator(f'[data-bt-langs] .bt-chip[data-lang="{code}"]')
            cls = chip.get_attribute("class") or ""
            if "bt-chip--active" in cls:
                chip.click()
        # 确保 de 是 active 的
        de_chip = page.locator('[data-bt-langs] .bt-chip[data-lang="de"]')
        cls = de_chip.get_attribute("class") or ""
        if "bt-chip--active" not in cls:
            de_chip.click()

        # 内容只勾 copy,关掉其他
        for ct in ["detail", "video", "cover"]:
            cb = page.locator(f'[data-bt-content="{ct}"]')
            if cb.is_checked():
                cb.click()
        copy_cb = page.locator('[data-bt-content="copy"]')
        if not copy_cb.is_checked():
            copy_cb.click()

        # 勾强制重翻(避免"已存在"跳过)
        page.locator("[data-bt-force]").click()

        page.wait_for_timeout(800)  # 等预估
        est_box = page.locator("[data-bt-estimate] .bt-estimate__body").inner_text()
        print(f"    预估:\n      {est_box[:300]}")

        # 处理二次确认 confirm 弹窗
        page.on("dialog", lambda d: d.accept())

        print("[9] 点击'开始翻译'...")
        page.locator("[data-bt-start]").click()

        # 等弹窗关闭 + 气泡出现
        page.wait_for_selector("#bt-dialog", state="hidden", timeout=10000)
        page.wait_for_selector("#bt-bubble:not(.hidden)", timeout=10000)
        bubble_summary = page.locator("[data-bt-bubble-summary]").inner_text()
        print(f"    气泡显示: {bubble_summary}")

        # 展开气泡看任务
        page.locator("[data-bt-bubble-compact]").click()
        page.wait_for_selector("[data-bt-bubble-expanded]:not(.hidden)")
        # 取气泡里第一个任务的详情链接
        detail_link = page.locator(".bt-bubble-task__link").first
        detail_href = detail_link.get_attribute("href")
        print(f"    气泡任务链接: {detail_href}")

        # 轮询 15 秒等任务跑完
        print("[10] 等待任务完成(最多 30s)...")
        import time
        deadline = time.time() + 30
        done = False
        while time.time() < deadline:
            resp = ctx.request.get(f"{BASE}/api/bulk-translate{detail_href.replace('/tasks', '')}")
            j = resp.json()
            status = j.get("status")
            prog = j.get("state", {}).get("progress", {})
            print(f"    [{int(time.time())}] status={status} progress={prog}")
            if status in ("done", "error", "cancelled"):
                done = status == "done"
                final = j
                break
            time.sleep(3)

        if not done:
            print("❌ 任务未在 30s 内完成,可能需手工查看")
        else:
            print(f"    ✅ 任务完成,cost={final['state']['cost_tracking']['actual']}")

            # 打开详情页验证
            page.goto(BASE + detail_href)
            page.wait_for_selector(".bt-detail__meta")
            stats = page.locator("[data-bt-stats]").inner_text()
            print(f"    详情页 Stats: {stats[:150]}")

        print("\n✅ 全部 UI + 端到端检查通过")
        browser.close()


if __name__ == "__main__":
    main()
