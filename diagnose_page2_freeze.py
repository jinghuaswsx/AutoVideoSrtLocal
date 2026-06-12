"""逐个 API 排查 task #1039 卡死根因"""
from playwright.sync_api import sync_playwright
import sys, io, time

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
BASE = "http://172.16.254.106"

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    context = browser.new_context(viewport={"width": 1920, "height": 1080})
    page = context.new_page()

    # 登录
    print("登录中...")
    page.goto(f"{BASE}/login", timeout=15000)
    page.wait_for_load_state("networkidle")
    page.fill('input[name="username"]', 'admin')
    page.fill('input[name="password"]', '709709@')
    page.click('button[type="submit"]')
    page.wait_for_url(f"{BASE}/medias/", timeout=15000)
    print("登录成功\n")

    # Step 1: 快速获取 task 1039 基本信息（从列表 API）
    print("1) 获取 task 1039 基本信息...")
    try:
        info = page.evaluate("""
            async () => {
                const t0 = performance.now();
                const resp = await fetch('/tasks/api/list?tab=all&task_id=1039&include_archived=1&page_size=1');
                const json = await resp.json();
                const task = (json.items || [])[0];
                return {
                    elapsed_ms: Math.round(performance.now() - t0),
                    id: task?.id,
                    parent_task_id: task?.parent_task_id,
                    product_name: task?.product_name,
                    status: task?.status,
                    media_product_id: task?.media_product_id,
                    media_item_id: task?.media_item_id,
                    country_code: task?.country_code,
                    is_rework: task?.is_rework,
                    child_country_codes: task?.child_country_codes,
                };
            }
        """)
        print(f"   ✓ {info['elapsed_ms']}ms, id={info['id']}, parent_task_id={info['parent_task_id']}, "
              f"product={info['product_name']}, status={info['status']}")
        print(f"   child_country_codes={info['child_country_codes']}")
    except Exception as e:
        print(f"   ✗ 失败: {e}")

    # Step 2: Events API
    print("2) 获取 events...")
    try:
        events = page.evaluate("""
            async () => {
                const t0 = performance.now();
                const resp = await fetch('/tasks/api/1039/events');
                const json = await resp.json();
                const evts = json.events || [];
                const size = JSON.stringify(evts).length;
                return {
                    elapsed_ms: Math.round(performance.now() - t0),
                    count: evts.length,
                    size_kb: Math.round(size / 1024),
                };
            }
        """)
        print(f"   ✓ {events['elapsed_ms']}ms, {events['count']} events, {events['size_kb']}KB")
    except Exception as e:
        print(f"   ✗ 失败: {e}")

    # Step 3: Review Assets API
    print("3) 获取 review-assets...")
    try:
        ra = page.evaluate("""
            async () => {
                const t0 = performance.now();
                const resp = await fetch('/tasks/api/1039/review-assets');
                const json = await resp.json();
                const size = JSON.stringify(json).length;
                const images = json.images || [];
                const base64Size = images.reduce((s, i) => s + (i.data?.length || 0), 0);
                return {
                    elapsed_ms: Math.round(performance.now() - t0),
                    size_kb: Math.round(size / 1024),
                    images_count: images.length,
                    base64_kb: Math.round(base64Size / 1024),
                };
            }
        """)
        print(f"   ✓ {ra['elapsed_ms']}ms, {ra['size_kb']}KB, images={ra['images_count']}, base64={ra['base64_kb']}KB")
        if ra['base64_kb'] > 500:
            print(f"   ⚠️  BASE64 图片数据过大: {ra['base64_kb']}KB!")
    except Exception as e:
        print(f"   ✗ 失败: {e}")

    # Step 4: Artifacts API
    print("4) 获取 artifacts...")
    try:
        art = page.evaluate("""
            async () => {
                const t0 = performance.now();
                const resp = await fetch('/tasks/api/1039/artifacts');
                const json = await resp.json();
                const items = json.items || [];
                return {
                    elapsed_ms: Math.round(performance.now() - t0),
                    count: items.length,
                };
            }
        """)
        print(f"   ✓ {art['elapsed_ms']}ms, {art['count']} items")
    except Exception as e:
        print(f"   ✗ 失败: {e}")

    # Step 5: 如果 task 1039 是子任务，测试 readiness API
    if info['parent_task_id']:
        print("5) task 1039 是子任务，获取 readiness...")
        try:
            rd = page.evaluate("""
                async () => {
                    const t0 = performance.now();
                    const resp = await fetch('/tasks/api/child/1039/readiness');
                    const json = await resp.json();
                    const checks = json.checks || [];
                    const size = JSON.stringify(checks).length;
                    return {
                        elapsed_ms: Math.round(performance.now() - t0),
                        checks_count: checks.length,
                        size_kb: Math.round(size / 1024),
                    };
                }
            """)
            print(f"   ✓ {rd['elapsed_ms']}ms, {rd['checks_count']} checks, {rd['size_kb']}KB")
        except Exception as e:
            print(f"   ✗ 失败: {e}")
    else:
        print("5) task 1039 是父任务（去字幕），跳过 readiness")
        # 对于父任务，检查有没有 subtask 相关 API
        print("   获取 children/subtasks...")
        try:
            subtasks = page.evaluate("""
                async () => {
                    const t0 = performance.now();
                    const resp = await fetch('/tasks/api/list?tab=all&task_id=&page=1&page_size=200&task_type=translate&bucket=all');
                    const json = await resp.json();
                    const items = json.items || [];
                    // 找 parent_task_id = 1039 的
                    const children = items.filter(i => i.parent_task_id === 1039);
                    return {
                        elapsed_ms: Math.round(performance.now() - t0),
                        total_items: items.length,
                        matching_children: children.length,
                    };
                }
            """)
            print(f"   ✓ {subtasks['elapsed_ms']}ms, total items={subtasks['total_items']}, 1039's children={subtasks['matching_children']}")
        except Exception as e:
            print(f"   ✗ 失败: {e}")

    # Step 6: 模拟完整的 tcLoadDetail 过程 - 逐个执行
    print("\n6) 模拟完整的 tcLoadDetail 渲染 (逐步)...")
    page.goto(f"{BASE}/tasks/overview/all?keyword=碎蒜器", timeout=30000)
    try:
        page.wait_for_load_state("networkidle", timeout=15000)
        print("   列表页加载完成")
    except:
        print("   列表页加载超时")

    page.wait_for_timeout(500)

    dom_pre = page.evaluate("() => document.querySelectorAll('*').length")
    print(f"   渲染前 DOM: {dom_pre}")

    # 手动逐步执行 tcLoadDetail 的步骤
    print("   6a) 获取 task detail...")
    t0 = time.time()
    page.evaluate("""
        async () => { window.__diag_task = await tcFetchDetailTask(1039, true); }
    """)
    page.wait_for_timeout(1000)
    print(f"     ✓ {(time.time()-t0)*1000:.0f}ms")

    task_basic = page.evaluate("""
        () => {
            const t = window.__diag_task;
            return t ? {id: t.id, parent_task_id: t.parent_task_id, status: t.status, is_rework: t.is_rework} : null;
        }
    """)
    print(f"     task: {task_basic}")

    print("   6b) 获取 events + review-assets...")
    t0 = time.time()
    page.evaluate("""
        async () => {
            window.__diag_events = await tcFetchJson('/tasks/api/1039/events');
            window.__diag_review = await tcFetchJson('/tasks/api/1039/review-assets');
        }
    """)
    page.wait_for_timeout(1000)
    print(f"     ✓ {(time.time()-t0)*1000:.0f}ms")

    events_count = page.evaluate("() => (window.__diag_events?.events || []).length")
    print(f"     events: {events_count}")

    print("   6c) 渲染详情 HTML (tcRenderDetail)...")
    t0 = time.time()
    page.evaluate("""
        () => {
            const task = window.__diag_task;
            const events = window.__diag_events?.events || [];
            const review = window.__diag_review || {};
            const html = tcRenderDetail(task, events, review);
            window.__diag_html = html;
            return html.length;
        }
    """)
    html_len = page.evaluate("() => window.__diag_html?.length || 0")
    print(f"     ✓ {(time.time()-t0)*1000:.0f}ms, HTML长度={html_len} chars ({html_len/1024:.1f}KB)")

    if html_len > 200000:
        print(f"     ⚠️  详情 HTML 极大: {html_len/1024:.0f}KB!!")

    print("   6d) 写入 DOM (innerHTML)...")
    t0 = time.time()
    page.evaluate("""
        () => {
            const body = document.getElementById('tcDetailBody');
            body.innerHTML = window.__diag_html;
        }
    """)
    page.wait_for_timeout(1000)
    print(f"     ✓ {(time.time()-t0)*1000:.0f}ms")

    dom_post = page.evaluate("() => document.querySelectorAll('*').length")
    print(f"   渲染后 DOM: {dom_post} (增长 {dom_post - dom_pre})")

    # 检查 DOM 是否异常
    if dom_post > 50000:
        print(f"   ⚠️  DOM 节点数异常大: {dom_post}!")

    print("   6e) 加载 artifacts...")
    t0 = time.time()
    try:
        page.evaluate("tcLoadArtifacts(1039)")
        page.wait_for_timeout(2000)
        print(f"     ✓ {(time.time()-t0)*1000:.0f}ms")
    except Exception as e:
        print(f"     ✗ {(time.time()-t0)*1000:.0f}ms, 错误: {e}")

    print("   6f) 加载 readiness...")
    t0 = time.time()
    try:
        page.evaluate("""
            async () => { await tcLoadReadiness(1039, window.__diag_task); }
        """)
        page.wait_for_timeout(3000)
        print(f"     ✓ {(time.time()-t0)*1000:.0f}ms")
    except Exception as e:
        print(f"     ✗ {(time.time()-t0)*1000:.0f}ms, 错误: {e}")

    dom_final = page.evaluate("() => document.querySelectorAll('*').length")
    print(f"   最终 DOM: {dom_final}")

    page.screenshot(path="task1039_step_by_step.png", full_page=False)
    print("\n截图: task1039_step_by_step.png")
    print("逐步诊断完成")

    browser.close()
