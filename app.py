from __future__ import annotations

from pathlib import Path
import runpy
import sys


APP_DIR = Path(__file__).resolve().parent / "app"
APP_ENTRYPOINT = APP_DIR / "app.py"

if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

runpy.run_path(str(APP_ENTRYPOINT), run_name="__main__")
