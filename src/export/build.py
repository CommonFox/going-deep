"""`src/export/`'s runnable edge — issue #126, under the front-end-rebuild epic (#111).

The only module in this package that touches the world: it reads the finished warehouse read-only
through `src.query.q()` (never `duckdb.connect()` directly — the file-lock reason `src/query.py`
documents applies here exactly as it does to a notebook, and matters more inside a build script,
where taking a write-blocking lock would be actively bad) and writes the JSON files the SPA reads
statically. Everything else in this package (`shape.py`, `manifest.py`, `current_week.py`) is pure
— a frame or a list of keys in, a JSON-ready structure out.

Runs as the tail of `scripts/build_warehouse.sh`, after every gold table it reads from has already
been rebuilt, so the export can never drift from the warehouse that produced it.

Only exports the tables an existing page actually reads for its rendered data today — see #125's
spike and #126's own scope rule. `my_roster`, `ros_points` and `weekly_projections` go in a
follow-up once a page needs them, not preemptively. `current_week.json` is the one exception: not
a warehouse table, but a value the lineup-optimizer page (#128) needs and `schedules` alone can't
give it without a live query — see `current_week.py`.
"""

import json
from datetime import UTC, datetime
from pathlib import Path

from src import console
from src.export.current_week import resolve_current_week
from src.export.manifest import build_manifest
from src.export.shape import partition_by_key, to_json_rows
from src.query import q

EXPORT_PATH = Path("data/export")

# Bumped on any breaking shape change, so the SPA can refuse to render a mismatch rather than
# silently showing blanks — the #125 spike's freshness-contract answer.
SCHEMA_VERSION = 1

_KEY_COLUMNS = ["league_key", "season", "week"]

# Table -> the columns its export files are sorted by within one (league_key, season, week) file.
# `player_id` first for the roster-shaped tables (nullable, so `player_name` breaks ties for the
# unresolved rows the docstrings in optimal_lineup.py/waiver_rankings.py both call out, and for
# weekly_player_context, which has no natural row order the way optimal_lineup has `slot`); `slot`
# for optimal_lineup, since a starting lineup's own slot ("QB", "RB1", ...) is already the row
# identity within a single (league, week) — no player_id collision to break a tie on.
_TABLE_SORT_BY = {
    "optimal_lineup": ["slot"],
    "optimal_lineup_bench": ["player_id", "player_name"],
    "waiver_rankings": ["player_id", "player_name"],
    "weekly_player_context": ["player_id", "player_name"],
    # Broadcast-window grouping and per-window ordering both happen client-side (#169), not from
    # file order — this is just a stable base order, kickoff first since that's the one dimension
    # every window itself is ordered by.
    "viewing_guide": ["kickoff", "game_id"],
    # Slot order here, unlike optimal_lineup's own file, so each game's starters read in the same
    # per-game grouping the lineup page already uses.
    "viewing_guide_starters": ["game_id", "slot"],
}


def _write_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True))


def _current_week_entries(today: str) -> list[dict]:
    """One `{league_key, season, week}` entry per league in `league_settings`, `week` resolved by
    `resolve_current_week` against that league's own season. UTC rather than local time, matching
    every other build-time value in this module — the freshness contract is already build-relative,
    not clock-relative, so this doesn't introduce a new notion of "now"."""
    league_settings = q("SELECT DISTINCT league_key, season FROM league_settings")
    entries = []
    for _, row in league_settings.iterrows():
        league_key, season = row["league_key"], int(row["season"])
        weeks = q(
            "SELECT week, MAX(gameday) AS ends FROM schedules WHERE season = ? GROUP BY week",
            [season],
        )
        week = resolve_current_week(weeks, today)
        entries.append({"league_key": league_key, "season": season, "week": week})
    return sorted(entries, key=lambda entry: (entry["league_key"], entry["season"]))


def build_export() -> None:
    available = []

    for table, sort_by in _TABLE_SORT_BY.items():
        frame = q(f"SELECT * FROM {table}")
        table_rows = 0
        file_count = 0
        for (league_key, season, week), group in partition_by_key(frame, _KEY_COLUMNS).items():
            rows = to_json_rows(group, sort_by)
            path = EXPORT_PATH / table / league_key / f"{season}-{week}.json"
            _write_json(path, rows)
            console.archived(path, len(rows))
            available.append({
                "table": table, "league_key": league_key, "season": season, "week": week,
            })
            table_rows += len(rows)
            file_count += 1
        # One always-shown line per table, same guarantee every silver/gold step gets — the
        # per-file console.archived calls above are exactly the archive chatter GOING_DEEP_QUIET
        # is documented to suppress, so without this line a quiet build would print nothing at
        # all for a table this step wrote.
        console.table(table, table_rows, detail=f"across {file_count} files")

    manifest = build_manifest(
        available, SCHEMA_VERSION, datetime.now(UTC).isoformat(timespec="seconds"),
    )
    manifest_path = EXPORT_PATH / "manifest.json"
    _write_json(manifest_path, manifest)
    console.archived(manifest_path, len(available))
    console.table("export_manifest", len(available))

    current_week = _current_week_entries(datetime.now(UTC).date().isoformat())
    current_week_path = EXPORT_PATH / "current_week.json"
    _write_json(current_week_path, current_week)
    console.archived(current_week_path, len(current_week))
    console.table("current_week", len(current_week))


if __name__ == "__main__":
    build_export()
