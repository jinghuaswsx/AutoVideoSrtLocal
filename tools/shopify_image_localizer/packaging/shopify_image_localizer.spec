import os
import re
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules


root = Path.cwd()

# 由 build_exe.py 在调用 PyInstaller 前注入版本号，构建出 ShopifyImageLocalizer_<major>_<minor>.exe；
# 没注入时退回到不带版本号的 ShopifyImageLocalizer.exe（直接跑 spec 调试场景）。
_release_version = os.environ.get("SHOPIFY_LOCALIZER_RELEASE_VERSION", "").strip().lstrip("vV")
if _release_version:
    _suffix = re.sub(r"[^A-Za-z0-9]+", "_", _release_version).strip("_")
    EXE_NAME = f"ShopifyImageLocalizer_{_suffix}" if _suffix else "ShopifyImageLocalizer"
else:
    EXE_NAME = "ShopifyImageLocalizer"
hiddenimports = sorted(set(
    collect_submodules("playwright")
    + [
        "link_check_desktop",
        "link_check_desktop.image_compare",
        "websocket",
        "websocket._abnf",
        "websocket._core",
        "websocket._exceptions",
        "websocket._handshake",
        "websocket._http",
        "websocket._logging",
        "websocket._socket",
        "websocket._ssl_compat",
        "websocket._url",
        "websocket._utils",
    ]
))
datas = collect_data_files("playwright")

a = Analysis(
    [str(root / "tools" / "shopify_image_localizer" / "main.py")],
    pathex=[str(root)],
    hiddenimports=hiddenimports,
    datas=datas,
    excludes=[
        "pytest",
        "sklearn",
        "torch",
        "tools.shopify_image_localizer.browser.orchestrator",
        "tools.shopify_image_localizer.matcher",
    ],
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name=EXE_NAME,
    console=False,
)
coll = COLLECT(exe, a.binaries, a.datas, name="ShopifyImageLocalizer")
