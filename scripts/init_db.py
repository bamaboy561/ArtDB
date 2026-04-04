from __future__ import annotations

from pathlib import Path
import sys

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from db import database_enabled, ensure_database_ready


def main() -> int:
    if not database_enabled():
        print("DATABASE_URL не задан. Инициализация PostgreSQL пропущена.")
        return 0

    ensure_database_ready()
    print("PostgreSQL schema is ready.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
