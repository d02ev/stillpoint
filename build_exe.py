"""Build the Windows .exe bundle: `uv run build-exe`.

Equivalent to running `uv run pyinstaller --noconfirm stillpoint.spec`.
"""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def main() -> int:
    spec = ROOT / "stillpoint.spec"
    result = subprocess.run([sys.executable, "-m", "PyInstaller", "--noconfirm", str(spec)], cwd=ROOT)
    if result.returncode != 0:
        print("build failed")
        return result.returncode
    print(f"Done. Look in {ROOT / 'dist' / 'Stillpoint'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
