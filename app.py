from __future__ import annotations

import importlib.util
from pathlib import Path
import runpy
import sys


APP_DIR = Path(__file__).resolve().parent / "app"
APP_ENTRYPOINT = APP_DIR / "app.py"
PRELOAD_MODULES = [
    "db",
    "sales_analytics",
    "auth_store",
    "plan_store",
    "salon_data_store",
]

if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))


def preload_module(module_name: str) -> None:
    if module_name in sys.modules:
        return

    module_path = APP_DIR / f"{module_name}.py"
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot prepare module '{module_name}' from {module_path}")

    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)


for _module_name in PRELOAD_MODULES:
    preload_module(_module_name)

runpy.run_path(str(APP_ENTRYPOINT), run_name="__main__")
