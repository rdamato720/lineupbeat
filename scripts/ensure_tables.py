#!/usr/bin/env python3
"""Import the historical tables the durability page needs, if they are missing.

    python3 scripts/ensure_tables.py

These were inside the once-a-day block in the workflow, so an afternoon run
had no weekly_stats table and the durability page skipped: it appeared each
morning and vanished for the rest of the day.

A finished season never changes, so once these tables exist there is nothing
to do. Cheap enough to check on every run, and it means the page cannot
silently disappear because of what time it is.
"""
import sqlite3
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "beatwire.db"

NEED = [
    ("weekly_stats", ["scripts/import_stats.py", "--seasons",
                      "2018,2019,2020,2021,2022,2023,2024,2025"]),
    ("weekly_status", ["scripts/import_status.py"]),
    ("injuries", ["scripts/import_injuries.py", "--season", "2026"]),
]


def main() -> int:
    if not DB.exists():
        print("  no database yet; nothing to check")
        return 0
    conn = sqlite3.connect(DB)
    have = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    conn.close()

    missing = [(t, c) for t, c in NEED if t not in have]
    if not missing:
        print(f"  all {len(NEED)} historical tables present")
        return 0

    for table, cmd in missing:
        print(f"  {table} missing, importing")
        r = subprocess.run([sys.executable] + cmd, cwd=ROOT)
        if r.returncode:
            print(f"  {table} import failed, continuing")
    return 0


if __name__ == "__main__":
    sys.exit(main())
