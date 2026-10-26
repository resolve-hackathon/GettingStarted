"""Refresh selected KB records in the demo PostgreSQL source used by Zetaris.

Run from the repo root:
  python3 zetaris/refresh_kb.py --bundle /path/to/zetaris-platform --article-id KB-011

Updates matching IDs and inserts missing IDs. Does not drop tables, delete other
articles, or touch the accounts, releases, or issues source databases.
"""

import argparse
import csv
import io
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from resolve.config import data_dir  # noqa: E402
from resolve.research import seed_snapshot  # noqa: E402


def refresh_sql(rows) -> str:
    csv_data = io.StringIO(newline="")
    csv.writer(csv_data, lineterminator="\n").writerows(rows)
    return """BEGIN;
CREATE TEMP TABLE resolve_kb_refresh (
    row_id text, component text, payload text
) ON COMMIT DROP;
COPY resolve_kb_refresh (row_id, component, payload) FROM STDIN WITH (FORMAT csv);
""" + csv_data.getvalue() + "\\.\n" + """
UPDATE kb AS target
SET component = fresh.component, payload = fresh.payload
FROM resolve_kb_refresh AS fresh WHERE target.row_id = fresh.row_id;
INSERT INTO kb (row_id, component, payload)
SELECT fresh.row_id, fresh.component, fresh.payload FROM resolve_kb_refresh AS fresh
WHERE NOT EXISTS (SELECT 1 FROM kb AS target WHERE target.row_id = fresh.row_id);
COMMIT;
"""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, required=True,
                        help="Existing local zetaris-platform Docker Compose directory")
    parser.add_argument("--article-id", action="append", required=True,
                        help="KB ID to refresh; repeat to update several articles")
    args = parser.parse_args()
    bundle = args.bundle.expanduser().resolve()
    if not bundle.is_dir():
        parser.error("--bundle must point to the existing Zetaris platform directory")
    wanted = set(args.article_id)
    with tempfile.TemporaryDirectory() as directory:
        database = Path(directory) / "export.sqlite"
        seed_snapshot(data_dir(), database)
        with sqlite3.connect(database) as connection:
            rows = [row for row in connection.execute("SELECT row_id, component, payload FROM kb")
                    if row[0] in wanted]
    missing = wanted - {row[0] for row in rows}
    if missing:
        parser.error("Unknown KB article IDs: " + ", ".join(sorted(missing)))
    subprocess.run([
        "docker", "compose", "exec", "-T", "postgres", "psql", "-U", "zetaris",
        "-d", "resolve_kb", "-v", "ON_ERROR_STOP=1",
    ], cwd=bundle, input=refresh_sql(rows), text=True, check=True)
    print("Refreshed KB source rows: " + ", ".join(sorted(wanted)))


if __name__ == "__main__":
    main()
