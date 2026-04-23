from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules


root = Path.cwd()
hiddenimports = sorted(set(
    collect_submodules("playwright")
))
datas = collect_data_files("playwright")

a = Analysis(
    [str(root / "tools" / "shopify_image_localizer" / "main.py")],
    pathex=[str(root)],
    hiddenimports=hiddenimports,
    datas=datas,
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
