"""Export the four sources to CSV, one file per source system.

Usage (repo root): python zetaris/export_csv.py
Writes runtime/zetaris/{accounts,kb,releases,issues}.csv for load_postgres.sh.
"""
import csv
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from resolve.config import data_dir  # noqa: E402
from resolve.research import seed_snapshot  # noqa: E402

TABLES = {
    "accounts": ["row_id", "discord_id", "client_version", "last_working_version", "payload"],
    "kb": ["row_id", "component", "payload"],
    "releases": ["row_id", "component", "payload"],
    "issues": ["row_id", "component", "state", "affects_version", "payload"],
}

out = ROOT / "runtime" / "zetaris"
out.mkdir(parents=True, exist_ok=True)
with tempfile.TemporaryDirectory() as tmp:
    db = Path(tmp) / "export.sqlite"
    seed_snapshot(data_dir(), db)
    with sqlite3.connect(db) as connection:
        for table, columns in TABLES.items():
            rows = connection.execute(f"SELECT {', '.join(columns)} FROM {table}").fetchall()
            with (out / f"{table}.csv").open("w", newline="", encoding="utf-8") as stream:
                writer = csv.writer(stream, lineterminator="\n")
                writer.writerow(columns)
                writer.writerows(rows)
            print(f"{table}: {len(rows)} rows -> {out / (table + '.csv')}")
