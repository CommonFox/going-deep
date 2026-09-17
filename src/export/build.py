"""`src/export/`'s runnable edge — issue #126, under the front-end-rebuild epic (#111).

The only module in this package that touches the world: it reads the finished warehouse read-only
through `src.query.q()` (never `duckdb.connect()` directly — the file-lock reason `src/query.py`
documents applies here exactly as it does to a notebook, and matters more inside a build script,
where taking a write-blocking lock would be actively bad) and writes the JSON files the SPA reads
statically. Everything else in this package (`shape.py`, `manifest.py`) is pure — a frame or a
list of keys in, a JSON-ready structure out.

Runs as the tail of `scripts/build_warehouse.sh`, after every gold table it reads from has already
been rebuilt, so the export can never drift from the warehouse that produced it.

Only exports the tables an existing page actually reads for its rendered data today — see #125's
spike and #126's own scope rule. `my_roster`, `ros_points` and `weekly_projections` go in a
follow-up once a page needs them, not preemptively.
"""

import json
from datetime import UTC, datetime
from pathlib import Path

from src import console
from src.export.manifest import build_manifest
from src.export.shape import partition_by_key, to_json_rows
from src.query import q

EXPORT_PATH = Path("data/export")

# Bumped on any breaking shape change, so the SPA can refuse to render a mismatch rather than
# silently showing blanks — the #125 spike's freshness-contract answer.
SCHEMA_VERSION = 1

_KEY_COLUMNS = ["league_key", "season", "week"]

# Table -> the columns its export files are sorted by within one (league_key, season, week) file.
# `player_id` first for the two roster-shaped tables (nullable, so `player_name` breaks ties for
# the unresolved rows the docstrings in optimal_lineup.py/waiver_rankings.py both call out); `slot`
# for optimal_lineup, since a starting lineup's own slot ("QB", "RB1", ...) is already the row
# identity within a single (league, week) — no player_id collision to break a tie on.
_TABLE_SORT_BY = {
    "optimal_lineup": ["slot"],
    "optimal_lineup_bench": ["player_id", "player_name"],
    "waiver_rankings": ["player_id", "player_name"],
}


def _write_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True))


def build_export() -> None:
    available = []

    for table, sort_by in _TABLE_SORT_BY.items():
        frame = q(f"SELECT * FROM {table}")
        for (league_key, season, week), group in partition_by_key(frame, _KEY_COLUMNS).items():
            rows = to_json_rows(group, sort_by)
            path = EXPORT_PATH / table / league_key / f"{season}-{week}.json"
            _write_json(path, rows)
            console.archived(path, len(rows))
            available.append({
                "table": table, "league_key": league_key, "season": season, "week": week,
            })

    manifest = build_manifest(
        available, SCHEMA_VERSION, datetime.now(UTC).isoformat(timespec="seconds"),
    )
    manifest_path = EXPORT_PATH / "manifest.json"
    _write_json(manifest_path, manifest)
    console.archived(manifest_path, len(available))


if __name__ == "__main__":
    build_export()
