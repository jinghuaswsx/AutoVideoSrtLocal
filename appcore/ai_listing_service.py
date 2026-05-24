"""AI Listing Service.

Handles Phase 1 automated landing page links extraction, AI details generation, 
pricing conversions, image assets download & diagnostic classification.
"""
from __future__ import annotations

import json
import logging
import re
from urllib.parse import urlparse, urljoin
import requests
from bs4 import BeautifulSoup

from appcore import db, local_media_storage, llm_client
from appcore.meta_hot_posts.product_analysis import fetch_product_analysis, detect_product_link_type

log = logging.getLogger(__name__)

# Heuristics for detecting e-commerce indicators and social domains
SHOPIFY_INDICATORS = {
    "/products/", "/cart/", "/checkout/", "myshopify.com", "checkout", 
    "buy", "shop", "product", "order", "get-yours", "cart"
}

SOCIAL_DOMAINS = {
    "facebook.com", "instagram.com", "twitter.com", "pinterest.com", 
    "youtube.com", "google.com", "doubleclick.net", "tiktok.com", 
    "apple.com", "snapchat.com"
}


def extract_outbound_links_heuristic(html_content: str, base_url: str) -> list[str]:
    """启发式提取可能是真实商品下单页的链接."""
    soup = BeautifulSoup(html_content, "html.parser")
    candidates = []
    
    parsed_base = urlparse(base_url)
    base_domain = parsed_base.netloc.lower()
    
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if not href or href.startswith("#") or href.startswith("javascript:"):
            continue
            
        full_url = urljoin(base_url, href)
        parsed_url = urlparse(full_url)
        domain = parsed_url.netloc.lower()
        path = parsed_url.path.lower()
        
        # 排除社交媒体和广告网络
        if any(social in domain for social in SOCIAL_DOMAINS):
            continue
            
        # 检查是否有显式电商指示词
        is_ecommerce = False
        if any(indicator in path or indicator in domain for indicator in SHOPIFY_INDICATORS):
            is_ecommerce = True
            
        # 如果是不同的域名（outbound），或者有电商特征，计入候选
        if domain != base_domain or is_ecommerce:
            score = 0
            if is_ecommerce:
                score += 10
            # 按钮文本匹配
            link_text = a.get_text(strip=True).lower()
            if any(btn in link_text for btn in ["shop", "buy", "order", "get", "grab", "claim", "now", "here"]):
                score += 5
                
            candidates.append((full_url, score))
            
    # 按优先级得分降序
    candidates.sort(key=lambda x: x[1], reverse=True)
    return [c[0] for c in candidates]


def extract_transit_link_via_ai(html_content: str, base_url: str, user_id: int | None = None) -> str | None:
    """利用 Gemini 智能识别并提取出真正的 Shopify 下单页链接."""
    soup = BeautifulSoup(html_content, "html.parser")
    links_data = []
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        text = a.get_text(strip=True)
        if href and not href.startswith("#") and not href.startswith("javascript:"):
            full_url = urljoin(base_url, href)
            links_data.append({"text": text[:100], "url": full_url})
            if len(links_data) >= 50:
                break
                
    if not links_data:
        return None
        
    prompt = (
        "You are an outbound pre-lander page analyzer.\n"
        "Identify the exact buying/checkout/product landing link pointing to the real e-commerce shop page (e.g. Shopify PDP).\n"
        "Ignore social links, menus, homepages, privacy pages.\n"
        f"Pre-lander URL: {base_url}\n\n"
        f"Available links:\n{json.dumps(links_data, ensure_ascii=False, indent=2)}\n\n"
        "Return a valid JSON with key 'exact_buying_link' containing the target URL."
    )
    
    try:
        response = llm_client.invoke_generate(
            "ai_listing.transit_parse",
            prompt=prompt,
            user_id=user_id,
            response_schema={
                "type": "object",
                "properties": {
                    "exact_buying_link": {"type": "string"}
                },
                "required": ["exact_buying_link"],
                "additionalProperties": False
            },
            temperature=0,
            max_output_tokens=256
        )
        data = response.get("json") or {}
        link = data.get("exact_buying_link")
        if link:
            return str(link).strip()
    except Exception as e:
        log.error("AI 提取二跳链接失败: %s", e)
    return None


def parse_transit_link(task_id: int, user_id: int | None = None) -> str:
    """运行启发式 + AI 二跳博客预热页解析链路."""
    task = db.query_one("SELECT * FROM ai_listing_tasks WHERE id = %s", (task_id,))
    if not task:
        raise ValueError(f"Task {task_id} not found")
        
    db.execute(
        "UPDATE ai_listing_tasks SET status = 'parsing', error_message = NULL WHERE id = %s",
        (task_id,)
    )
    
    source_link = task["source_link"]
    
    # 如果 source_link 已经是真实商品落地页，直接短路
    if detect_product_link_type(source_link) != "generic_product":
        db.execute(
            "UPDATE ai_listing_tasks SET transit_link = %s, status = 'generating' WHERE id = %s",
            (source_link, task_id)
        )
        return source_link
        
    try:
        resp = requests.get(
            source_link, 
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/137.0.0.0 Safari/537.36"
            },
            timeout=20
        )
        resp.raise_for_status()
        html = resp.text
    except Exception as e:
        err_msg = f"抓取引流博客页失败: {e}"
        db.execute(
            "UPDATE ai_listing_tasks SET status = 'failed', error_message = %s WHERE id = %s",
            (err_msg, task_id)
        )
        raise RuntimeError(err_msg)
        
    # 启发式匹配
    candidates = extract_outbound_links_heuristic(html, resp.url)
    transit_link = None
    if candidates:
        transit_link = candidates[0]
        
    # AI 提炼兜底
    ai_link = extract_transit_link_via_ai(html, resp.url, user_id=user_id)
    if ai_link:
        transit_link = ai_link
        
    # fallback
    if not transit_link:
        transit_link = source_link
        
    db.execute(
        "UPDATE ai_listing_tasks SET transit_link = %s, status = 'generating' WHERE id = %s",
        (transit_link, task_id)
    )
    return transit_link


def generate_ai_listing_assets(task_id: int, user_id: int | None = None) -> None:
    """根据真实商品链接自动提取结构、SKU、定价与爆款 HTML 文案，并抓取保存图片资源."""
    task = db.query_one("SELECT * FROM ai_listing_tasks WHERE id = %s", (task_id,))
    if not task:
        raise ValueError(f"Task {task_id} not found")
        
    transit_link = task["transit_link"]
    if not transit_link:
        raise ValueError(f"Task {task_id} has no transit_link. Run parse_transit_link first.")
        
    try:
        analysis = fetch_product_analysis(transit_link)
    except Exception as e:
        err_msg = f"抓取竞品商品页详情失败: {e}"
        db.execute(
            "UPDATE ai_listing_tasks SET status = 'failed', error_message = %s WHERE id = %s",
            (err_msg, task_id)
        )
        return
        
    # SKU 价格重算与映射
    pricing_ratio = float(task["pricing_ratio"])
    pricing_offset = float(task["pricing_offset"])
    
    converted_skus = []
    for item in analysis.skus:
        orig_price = item.get("price")
        if orig_price is not None:
            new_price = round(float(orig_price) * pricing_ratio + pricing_offset, 2)
            # E-commerce e.g. .99 / .95
            decimal_part = new_price - int(new_price)
            if 0.3 <= decimal_part <= 0.8:
                new_price = int(new_price) + 0.95
            else:
                new_price = int(new_price) + 0.99
        else:
            new_price = 0.00
            
        converted_skus.append({
            "sku": item.get("sku") or "",
            "title": item.get("title") or "Default Title",
            "price": new_price,
            "currency": "USD"
        })
        
    # AI 详情页生成 (Classic CRO flow)
    comp_title = analysis.title
    comp_desc_raw = (
        analysis.raw.get("product", {}).get("body_html") 
        or analysis.raw.get("product", {}).get("description") 
        or ""
    )
    if not comp_desc_raw:
        comp_desc_raw = f"Product Title: {comp_title}. No description provided."
        
    prompt = (
        "You are an expert Shopify conversion rate optimizer (CRO) and professional copywriter.\n"
        "Your task is to take the following competitor product details and restructure it into a highly compelling, "
        "beautifully styled, high-converting English description.\n\n"
        "Strictly follow this e-commerce landing page flow and output clean HTML (NO raw CSS styles, NO layout columns, "
        "NO wrapper container div styles. Only standard HTML tags: <h3>, <p>, <ul>, <li>, <strong>, <em>, <br>):\n"
        "1. **Pain Point Hook (Hook引入)**: Start with 1-2 powerful bullet points/pain-point hooks in bold to catch interest.\n"
        "2. **Core Features (核心功能 - Emoji化)**: Create an elegant bullet list with expressive emojis describing how this product solves the problem.\n"
        "3. **How It Works / Benefit Comparison (使用方法/核心对比)**: A brief h3 section with 2-3 steps or comparison benefits.\n"
        "4. **Specifications & Packaging (规格参数与清单)**: A clear, organized h3 section outlining what is included and details.\n"
        "5. **E-commerce Trust Badges (物流与信任徽章)**: Append 3 trust badge bullet points (e.g. 30-Day Money-Back Guarantee, Fast Shipping, Safe Checkout).\n\n"
        f"Competitor Product Title: {comp_title}\n"
        f"Competitor Product Description:\n{comp_desc_raw}\n\n"
        "Please output valid JSON matching the schema.\n"
        "Both 'title' and 'html_description' fields must be in English. Restructure the title to be extremely high-converting and punchy."
    )
    
    try:
        response = llm_client.invoke_generate(
            "ai_listing.copywriting",
            prompt=prompt,
            response_schema={
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "html_description": {"type": "string"}
                },
                "required": ["title", "html_description"],
                "additionalProperties": False
            },
            temperature=0.3,
            max_output_tokens=3000
        )
        ai_data = response.get("json") or {}
        generated_title = ai_data.get("title") or comp_title
        generated_html_desc = ai_data.get("html_description") or comp_desc_raw
    except Exception as e:
        log.error("AI 详情页生成失败: %s", e)
        generated_title = comp_title
        generated_html_desc = f"<p>{comp_desc_raw}</p>"
        
    db.execute(
        "UPDATE ai_listing_tasks SET generated_title = %s, generated_skus_json = %s, generated_html_desc = %s, status = 'completed' WHERE id = %s",
        (generated_title, json.dumps(converted_skus), generated_html_desc, task_id)
    )
    
    # 抓取图片资源
    images = []
    if analysis.main_image_url:
        images.append((analysis.main_image_url, "carousel"))
        
    # Detail images embedded in competitive description
    soup = BeautifulSoup(comp_desc_raw, "html.parser")
    for img in soup.find_all("img", src=True):
        src = img["src"].strip()
        if src and src.startswith("http"):
            images.append((src, "detail_image"))
            
    # Shopify payload images
    product_images = analysis.raw.get("product", {}).get("images") or []
    for p_img in product_images:
        src = None
        if isinstance(p_img, dict):
            src = p_img.get("src") or p_img.get("url")
        elif isinstance(p_img, str):
            src = p_img
        if src and src.startswith("http") and src not in [x[0] for x in images]:
            images.append((src, "carousel"))
            
    # Unique images
    unique_images = []
    seen = set()
    for src, asset_type in images:
        if src not in seen:
            seen.add(src)
            unique_images.append((src, asset_type))
            
    # Download images & save to media store
    for idx, (src, asset_type) in enumerate(unique_images):
        try:
            img_resp = requests.get(
                src, 
                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/137.0.0.0 Safari/537.36"},
                timeout=15
            )
            img_resp.raise_for_status()
            payload = img_resp.content
            
            ext = "png"
            content_type = img_resp.headers.get("content-type", "").lower()
            if "jpg" in content_type or "jpeg" in content_type:
                ext = "jpg"
            elif "gif" in content_type:
                ext = "gif"
            elif "webp" in content_type:
                ext = "webp"
                
            filename = f"{asset_type}_{idx}.{ext}"
            object_key = f"uploads/ai_listing/{task_id}/{filename}"
            
            local_media_storage.write_bytes(object_key, payload)
            
            # AI diagnosis tags classification
            ai_classification = "showcase"
            if any(badge in src.lower() for badge in ["badge", "trust", "payment", "guarantee"]):
                ai_classification = "badge"
            elif any(rev in src.lower() for rev in ["review", "star", "rating"]):
                ai_classification = "review"
                
            db.execute(
                "INSERT INTO ai_listing_assets (task_id, asset_type, original_url, transformed_url, ai_classification, is_selected, sort_order) VALUES (%s, %s, %s, %s, %s, 1, %s)",
                (task_id, asset_type, src, object_key, ai_classification, idx)
            )
        except Exception as e:
            log.warning("下载/保存图片资产失败: %s, url: %s", e, src)


def translate_asset_image(asset_id: int, prompt_text: str = "", user_id: int | None = None) -> str:
    """使用 Gemini Image (generate_image) 本地化或翻译单张商品图，隔离版权风险."""
    asset = db.query_one("SELECT * FROM ai_listing_assets WHERE id = %s", (asset_id,))
    if not asset:
        raise ValueError(f"Asset {asset_id} not found")
        
    local_path = local_media_storage.safe_local_path_for(asset["transformed_url"])
    if not local_path.is_file():
        raise FileNotFoundError(f"Local copy of asset {asset_id} not found")
        
    with open(local_path, "rb") as f:
        img_bytes = f.read()
        
    mime = "image/png"
    if local_path.suffix.lower() in [".jpg", ".jpeg"]:
        mime = "image/jpeg"
    elif local_path.suffix.lower() == ".webp":
        mime = "image/webp"
    elif local_path.suffix.lower() == ".gif":
        mime = "image/gif"
        
    if not prompt_text:
        prompt_text = (
            "This is a product detail image for an e-commerce store. "
            "Please redraw this image keeping the core product shape, features, and layout exactly identical, "
            "but replace any visible foreign/non-English text with clean, highly professional English descriptions. "
            "Ensure the final image looks stunning, sharp, premium, and free of copyright or logo issues."
        )
        
    from appcore.gemini_image import generate_image
    
    new_bytes, new_mime = generate_image(
        prompt_text,
        source_image=img_bytes,
        source_mime=mime,
        model="google/gemini-3.1-flash-image-preview",
        user_id=user_id,
        service="image_translate.generate"
    )
    
    task_id = asset["task_id"]
    new_filename = f"transformed_{asset['id']}.png"
    new_object_key = f"uploads/ai_listing/{task_id}/{new_filename}"
    local_media_storage.write_bytes(new_object_key, new_bytes)
    
    db.execute(
        "UPDATE ai_listing_assets SET transformed_url = %s WHERE id = %s",
        (new_object_key, asset_id)
    )
    return new_object_key
