from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules


root = Path.cwd()
hiddenimports = sorted(set(
    collect_submodules("playwright")
    + [
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
        "link_check_desktop",
        "pytest",
        "scipy",
        "skimage",
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
    name="ShopifyImageLocalizer",
    console=False,
)
coll = COLLECT(exe, a.binaries, a.datas, name="ShopifyImageLocalizer")
