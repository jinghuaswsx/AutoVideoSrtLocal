from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules


root = Path.cwd()
hiddenimports = collect_submodules("skimage")

a = Analysis(
    [str(root / "link_check_desktop" / "main.py")],
    pathex=[str(root)],
    hiddenimports=hiddenimports,
    datas=[],
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="LinkCheckDesktop",
    console=False,
)
coll = COLLECT(exe, a.binaries, a.datas, name="LinkCheckDesktop")
