"""Scan pipeline.db for canonical keys that appear under >1 source.

These are the rows where the license-to-business COALESCE join would fire.
If this prints nothing, the Sheet showing no merged rows is expected — the
data simply has no overlap at this key. If it prints matches but the Sheet
doesn't reflect them, the bug is downstream of the key (orchestrator or
Sheet writer).

Usage:
    python scripts/find_joins.py [path/to/pipeline.db]
"""
from __future__ import annotations

import sqlite3
import sys
from collections import defaultdict
from pathlib import Path


def find_cross_source_keys(db_path: Path) -> dict[str, set[str]]:
    conn = sqlite3.connect(str(db_path))
    try:
        rows = conn.execute(
            "SELECT canonical_key, source FROM raw_fetches"
        ).fetchall()
    finally:
        conn.close()

    by_key: dict[str, set[str]] = defaultdict(set)
    for key, source in rows:
        by_key[key].add(source)
    return {k: s for k, s in by_key.items() if len(s) > 1}


def main() -> int:
    db_path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("data/pipeline.db")
    if not db_path.exists():
        print(f"db not found: {db_path}", file=sys.stderr)
        return 2

    matches = find_cross_source_keys(db_path)
    if not matches:
        print("no cross-source canonical keys in raw_fetches")
        return 0

    print(f"{len(matches)} canonical key(s) seen under >1 source:")
    for key, sources in sorted(matches.items()):
        print(f"  {key}  {sorted(sources)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
