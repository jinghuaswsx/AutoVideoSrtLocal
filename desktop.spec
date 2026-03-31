# -*- mode: python ; coding: utf-8 -*-
import os
from PyInstaller.utils.hooks import collect_all

block_cipher = None

pyside6_datas, pyside6_binaries, pyside6_hiddenimports = collect_all("PySide6")

datas = [
    ("voices/voices.json", "voices"),
]
if os.path.exists(".env"):
    datas.append((".env", "."))
if os.path.exists("capcut_example"):
    datas.append(("capcut_example", "capcut_example"))

datas += pyside6_datas

a = Analysis(
    ["desktop/main.py"],
    pathex=["."],
    binaries=pyside6_binaries,
    datas=datas,
    hiddenimports=[
        "pipeline.alignment",
        "pipeline.asr",
        "pipeline.capcut",
        "pipeline.compose",
        "pipeline.extract",
        "pipeline.localization",
        "pipeline.storage",
        "pipeline.subtitle",
        "pipeline.subtitle_alignment",
        "pipeline.timeline",
        "pipeline.translate",
        "pipeline.tts",
        "pipeline.voice_library",
        "appcore.events",
        "appcore.task_state",
        "appcore.runtime",
        "config",
    ] + pyside6_hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="AutoVideoSrt",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    onefile=True,
)
