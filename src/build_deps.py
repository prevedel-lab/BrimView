#!/usr/bin/env python3
import subprocess
import shutil
import sys
from pathlib import Path
import platform

# Paths
ROOT = Path(__file__).parent.parent.resolve() # This file resides in {project}/src/
PYODIDE_DIR = ROOT / "pyodide"
DEPENDENCIES = [
    ROOT / "../BrimView-widgets",
    ROOT / "../brimfile",
]

def venv_python(path: Path) -> Path:
    """Return the python executable inside a dependency's .venv."""
    if platform.system() == "Windows":
        return path / ".venv" / "Scripts" / "python.exe"
    else:
        return path / ".venv" / "bin" / "python"
    
def run(cmd, cwd):
    print(f"\n[RUN] {' '.join(cmd)} (in {cwd})")
    subprocess.check_call(cmd, cwd=cwd)

def build_package(path: Path):
    python_exe = venv_python(path)
    if not python_exe.exists():
        raise RuntimeError(f"Expected venv python not found: {python_exe}")
    
    dist = path / "dist"
    if dist.exists():
        shutil.rmtree(dist)

    run([str(python_exe), "-m", "build"], cwd=path)

    # pick the newest wheel
    wheels = sorted(dist.glob("*.whl"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not wheels:
        raise RuntimeError(f"No wheels built in {dist}")
    wheel = wheels[0]
    print(f"[INFO] Built wheel: {wheel.name}")

    # copy into pyodide
    dest = PYODIDE_DIR / wheel.name
    shutil.copy2(wheel, dest)
    print(f"[INFO] Copied -> {dest}")

def main():
    PYODIDE_DIR.mkdir(exist_ok=True)
    for dep in DEPENDENCIES:
        print(f"=== {dep} ===")
        build_package(dep)

if __name__ == "__main__":
    main()
