#!/usr/bin/env python3
"""Launch QAmate in screenshot mode (Electron --capture-screenshots)."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_DIST = ROOT / "node_modules" / "electron" / "dist"
if sys.platform == "win32":
    ELECTRON = _DIST / "electron.exe"
elif sys.platform == "darwin":
    ELECTRON = _DIST / "Electron.app" / "Contents" / "MacOS" / "Electron"
else:
    ELECTRON = _DIST / "electron"
OUT = ROOT / "website" / "assets" / "screenshots"


def main() -> int:
    if not ELECTRON.exists():
        print(f"Electron not found: {ELECTRON}", file=sys.stderr)
        print("Run: npm install", file=sys.stderr)
        return 1

    OUT.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(
        [str(ELECTRON), str(ROOT), "--capture-screenshots"],
        cwd=str(ROOT),
        env={**dict(**__import__("os").environ), "ATS_NO_MANUAL_INPUT": "1"},
    )
    if proc.returncode != 0:
        return proc.returncode

    pngs = sorted(OUT.glob("*.png"))
    if not pngs:
        print("No screenshots were written.", file=sys.stderr)
        return 1

    print(f"Saved {len(pngs)} screenshot(s) to {OUT}:")
    for p in pngs:
        print(f"  - {p.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
