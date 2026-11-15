import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files

root = Path(SPECPATH).resolve().parent
pkg = root / "src" / "mrfinancer"
datas = [
    (str(path), "mrfinancer/web")
    for path in sorted((pkg / "web").iterdir())
    if path.suffix in {".html", ".css", ".js", ".json", ".md", ".png", ".ico"}
]
datas.append((str(pkg / "data" / "incorporation.json.gz"), "mrfinancer/data"))
for package in ("docx", "pptx", "reportlab"):
    datas += collect_data_files(package)

analysis = Analysis(
    [str(root / "scripts" / "launch-executable.py")],
    pathex=[str(root / "src")],
    datas=datas,
    hiddenimports=["uvicorn.loops.asyncio", "uvicorn.protocols.http.h11_impl", "uvicorn.lifespan.on"],
)
exe = EXE(
    PYZ(analysis.pure),
    analysis.scripts,
    analysis.binaries,
    analysis.datas,
    name="mrfinancer",
    console=True,
    upx=False,
    icon=str(pkg / "web" / "favicon.ico") if sys.platform == "win32" else None,
)
