# -*- coding: utf-8 -*-
"""
数据修复脚本：
1. 扫描 media_products 中所有名称非中文的产品。
2. 检索明空选品数据源（字典、明空快照、明空 Top 榜、素材预选），获取正确的中文产品名并更新产品名称。
3. 针对所有存在中文产品名的产品，扫描其下属素材的 display_name，若含有英文 product_code，则替换为正确的中文产品名。
"""
from __future__ import annotations

import sys
import re
from pathlib import Path

# 确保 utf-8 编码输出
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# 将项目根目录加入 sys.path
project_root = Path(__file__).resolve().parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from appcore.db import execute, query_all
from appcore.mk_import import _is_chinese, _find_fallback_chinese_name, _normalize_product_code


def fix_product_and_material_names() -> None:
    print("=== 开始执行产品及素材中文名修复脚本 ===")

    # 1. 查询所有未删除的产品
    products = query_all("SELECT id, name, product_code FROM media_products WHERE deleted_at IS NULL")
    if not products:
        print("[*] 未找到活跃产品记录。")
        return

    print(f"[*] 找到 {len(products)} 个产品记录，开始扫描...")

    updated_products_count = 0
    updated_materials_count = 0

    for p in products:
        product_id = int(p["id"])
        original_name = str(p.get("name") or "").strip()
        product_code = str(p.get("product_code") or "").strip()
        normalized_code = _normalize_product_code(product_code)

        cn_name = original_name
        is_updated = False

        # 如果当前名称不是中文，尝试寻找 fallback 中文名
        if not _is_chinese(original_name):
            print(f"\n[*] 产品 #{product_id} (code: {product_code}) 当前名称 '{original_name}' 非中文，尝试检索 fallback...")
            fallback = _find_fallback_chinese_name(product_code)
            if fallback:
                print(f"  [+] 找到中文名 fallback: '{fallback}'")
                try:
                    execute("UPDATE media_products SET name = %s, updated_at = NOW() WHERE id = %s", (fallback, product_id))
                    cn_name = fallback
                    is_updated = True
                    updated_products_count += 1
                    print(f"  [+] 成功更新产品 #{product_id} 名称为 '{fallback}'")
                    
                    # 同步到字典
                    try:
                        from appcore.product_name_dictionary import sync_names
                        sync_names(product_code, fallback, None)
                    except Exception as exc:
                        print(f"  [!] 同步字典失败: {exc}")
                except Exception as exc:
                    print(f"  [!] 更新产品 #{product_id} 失败: {exc}")
            else:
                print("  [-] 未检索到有效的中文名 fallback。")

        # 2. 如果产品有名为中文的正确名称，扫描下属素材
        if _is_chinese(cn_name):
            materials = query_all("SELECT id, display_name FROM media_items WHERE product_id = %s AND deleted_at IS NULL", (product_id,))
            for m in materials:
                material_id = int(m["id"])
                disp_name = str(m.get("display_name") or "").strip()
                if not disp_name:
                    continue

                # 替换 display_name 中的英文 product_code / normalized_code 字段
                new_disp_name = disp_name
                # 支持替换带 -rjc 后缀或不带的 product_code (不区分大小写)
                for code_pattern in (product_code, normalized_code):
                    if not code_pattern:
                        continue
                    # 仅在包含该英文片段时替换
                    if code_pattern.lower() in new_disp_name.lower():
                        # 使用正则做不区分大小写替换
                        try:
                            pattern = re.compile(re.escape(code_pattern), re.IGNORECASE)
                            new_disp_name = pattern.sub(cn_name, new_disp_name)
                        except Exception as exc:
                            print(f"  [!] 正则替换失败 material_id={material_id}: {exc}")

                if new_disp_name != disp_name:
                    print(f"  [*] 素材 #{material_id} 命名更新: '{disp_name}' -> '{new_disp_name}'")
                    try:
                        execute("UPDATE media_items SET display_name = %s WHERE id = %s", (new_disp_name, material_id))
                        updated_materials_count += 1
                    except Exception as exc:
                        print(f"  [!] 更新素材 #{material_id} 失败: {exc}")

    print("\n=== 修复执行完毕 ===")
    print(f"[+] 成功更新产品中文名: {updated_products_count} 个")
    print(f"[+] 成功修正素材显示名: {updated_materials_count} 个")


if __name__ == "__main__":
    fix_product_and_material_names()
