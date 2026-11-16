# PyInstaller spec for the single-file executable (RenfeEnjoyer.exe on Windows).
#
# Build from the repository root, with the SPA already compiled:
#   cd frontend && npm run build && cd ..
#   pyinstaller --noconfirm packaging/renfe-enjoyer.spec
#
# PyInstaller cannot cross-compile: the Windows .exe has to be built on Windows, which CI
# does on every push. On Linux the same spec produces a Linux binary, handy to check that
# the bundle starts without a Windows machine at hand.
#
# Selenium, uvicorn and websockets need no hidden imports here: pyinstaller-hooks-contrib
# already collects Selenium's data files and both servers' dynamically imported modules.
import sys
from pathlib import Path

ROOT = Path(SPECPATH).parent
DIST = ROOT / "frontend" / "dist"
if not (DIST / "index.html").is_file():
    raise SystemExit("Falta el frontend compilado: ejecuta antes 'npm run build' en frontend/")

# Selenium ships a selenium-manager binary for every platform (some 20 MB in all); only
# this platform's is ever run.
PLATFORM_DIR = {"win32": "windows", "darwin": "macos"}.get(sys.platform, "linux")


def foreign_manager(entry: tuple[str, str, str]) -> bool:
    path = Path(entry[0])
    return path.name.startswith("selenium-manager") and path.parent.name in {
        "windows",
        "macos",
        "linux-x86_64",
        "linux-arm64",
    } and not path.parent.name.startswith(PLATFORM_DIR)


analysis = Analysis(
    [str(ROOT / "packaging" / "launch.py")],
    pathex=[str(ROOT / "backend")],
    datas=[(str(DIST), "frontend/dist")],
    excludes=["tkinter", "pytest", "mypy", "ruff"],
)
analysis.datas = [entry for entry in analysis.datas if not foreign_manager(entry)]
analysis.binaries = [entry for entry in analysis.binaries if not foreign_manager(entry)]

pyz = PYZ(analysis.pure)
exe = EXE(
    pyz,
    analysis.scripts,
    analysis.binaries,
    analysis.datas,
    name="RenfeEnjoyer",
    icon=str(ROOT / "frontend" / "public" / "favicon.ico"),
    # The console is the app's on/off switch: closing it stops the server.
    console=True,
    # UPX-packed executables are flagged by antivirus far more often.
    upx=False,
)
