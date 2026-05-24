"""Shopify Image Localizer - AI Listing Tab Panel.

This panel serves as the user interface in the desktop client for Phase 1 pre-reservation,
allowing future RPA / CDP automation tasks to run locally on the merchant's machine.
"""
from __future__ import annotations

import flet as ft


def build_ai_listing_tab(page: ft.Page) -> ft.Control:
    """Builds and returns the AI Listing Control tab for the desktop Flet client."""
    return ft.Container(
        content=ft.Column(
            controls=[
                ft.Text(
                    "AI 自动上品控制台 (Phase 1 架构预留)",
                    size=20,
                    weight=ft.FontWeight.BOLD,
                    color=ft.colors.BLUE_700,
                ),
                ft.Text(
                    "本桌面端面板是专门为全自动化上架 (Phase 2) 预备的本地执行引擎。\n"
                    "它将无缝融合进 Shopify Image Localizer 换图工具中，共享已在本地 Chrome 登录的店铺域 Session、CDP 浏览器驱动与 PyInstaller 打包管线。",
                    size=14,
                ),
                ft.Divider(),
                ft.Card(
                    content=ft.Container(
                        content=ft.Column(
                            controls=[
                                ft.Text("📋 本地执行引擎状态", weight=ft.FontWeight.BOLD),
                                ft.Row(
                                    controls=[
                                        ft.Icon(ft.icons.PLAY_CIRCLE_FILLED, color=ft.colors.GREEN),
                                        ft.Text("后台监听中 (HTTP Bridge Ready) - 等待服务器下发二跳 CDP 解析"),
                                    ]
                                ),
                            ]
                        ),
                        padding=15,
                    )
                ),
            ],
            spacing=15,
        ),
        padding=20,
    )
