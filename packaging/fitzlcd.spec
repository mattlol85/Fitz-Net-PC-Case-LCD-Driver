# PyInstaller build spec.  Build with:  python -m PyInstaller packaging/fitzlcd.spec --noconfirm
#
# One-dir rather than one-file: startup is much faster, and the panel should come
# alive quickly at login when autostart is enabled.

from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

ROOT = Path(SPECPATH).parent

hidden = [
    # Layer classes register themselves on import, so they must not be pruned.
    *collect_submodules("fitzlcd.render.layers"),
    "fitzlcd.panels.ds916.driver",
    "fitzlcd.panels.virtual",
    "pynvml",
]

a = Analysis(
    [str(ROOT / "src" / "fitzlcd" / "__main__.py")],
    pathex=[str(ROOT / "src")],
    binaries=[],
    datas=[],
    hiddenimports=hidden,
    excludes=[
        # Qt modules the app never touches; they roughly double the bundle.
        "PySide6.QtWebEngineCore",
        "PySide6.QtWebEngineWidgets",
        "PySide6.Qt3DCore",
        "PySide6.QtCharts",
        "PySide6.QtDataVisualization",
        "PySide6.QtQuick3D",
        "tkinter",
    ],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    exclude_binaries=True,
    name="FitzLCD",
    debug=False,
    strip=False,
    upx=False,
    console=False,  # tray app: no console window at login
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="FitzLCD",
)
