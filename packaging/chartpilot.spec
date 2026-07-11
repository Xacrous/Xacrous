# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for ChartPilot. Build from the repo root with:

    pyinstaller --noconfirm packaging/chartpilot.spec

Paths are resolved relative to this file (via SPECPATH) so the build
works the same regardless of the invoking working directory.
"""

import os

from PyInstaller.utils.hooks import collect_all

REPO_ROOT = os.path.join(SPECPATH, "..")  # noqa: F821 — SPECPATH is injected by PyInstaller

datas = [(os.path.join(REPO_ROOT, "chartpilot", "chart_view", "web"), os.path.join("chartpilot", "chart_view", "web"))]
binaries = []
hiddenimports = ["ccxt.pro"]

# PyQt6 needs its plugins/QML/translations; qfluentwidgets needs its qss/
# image resources; pandas-ta-classic introspects its own package directory
# at import time (a plain PyInstaller freeze doesn't capture that unless
# told to collect the whole package).
for pkg in ("PyQt6", "qfluentwidgets", "pandas_ta_classic"):
    pkg_datas, pkg_binaries, pkg_hiddenimports = collect_all(pkg)
    datas += pkg_datas
    binaries += pkg_binaries
    hiddenimports += pkg_hiddenimports

a = Analysis(  # noqa: F821
    [os.path.join(REPO_ROOT, "chartpilot", "main.py")],
    pathex=[REPO_ROOT],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)  # noqa: F821

exe = EXE(  # noqa: F821
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="ChartPilot",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
)
coll = COLLECT(  # noqa: F821
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="ChartPilot",
)
