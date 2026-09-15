# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path

ROOT = Path(SPECPATH).parents[1]
SRC = ROOT / "src"

a = Analysis(
    [str(ROOT / "packaging" / "pyinstaller" / "entrypoint.py")],
    pathex=[str(SRC)],
    binaries=[],
    datas=[(str(SRC / "annoweave" / "assets"), "annoweave/assets")],
    hiddenimports=["onnxruntime.capi._pybind_state"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["PySide6.Qt3D", "PySide6.QtBluetooth", "PySide6.QtCharts", "PySide6.QtLocation", "PySide6.QtWebEngine"],
    noarchive=False,
    optimize=1,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="AnnoWeave",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    icon=str(SRC / "annoweave" / "assets" / "annoweave.ico"),
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="AnnoWeave")
