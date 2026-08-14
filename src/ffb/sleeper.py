"""Fetch and load Sleeper league data into the DuckDB warehouse."""

import json
import time
from pathlib import Path

import duckdb
import pandas as pd
import requests

RAW_DIR = Path("data/raw/sleeper")
WAREHOUSE_PATH = Path("data/warehouse.duckdb")
BASE_URL = "https://api.sleeper.app/v1"
PROJECTIONS_BASE_URL = "https://api.sleeper.app"

# TODO: set this to your league's ID (the numeric ID in the league's Sleeper URL).
LEAGUE_ID = "1390836961870090240"

SEASON = 2026

# Regular season + playoffs; trim if your league's schedule is shorter.
WEEKS = list(range(1, 19))


def _get(path: str):
    response = requests.get(f"{BASE_URL}{path}")
    response.raise_for_status()
    return response.json()


def _save_raw_json(data, filename_stem: str) -> Path:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    raw_path = RAW_DIR / f"{filename_stem}.json"
    raw_path.write_text(json.dumps(data))
    print(f"Saved {raw_path}")
    return raw_path


def _load_json_to_table(raw_path: Path, table_name: str) -> None:
    """Load a raw JSON file into a DuckDB table (idempotent)."""
    data = json.loads(raw_path.read_text())
    df = pd.json_normalize(data)

    WAREHOUSE_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(WAREHOUSE_PATH))
    if df.empty and len(df.columns) == 0:
        con.execute(f"DROP TABLE IF EXISTS {table_name}")
    else:
        con.execute(f"CREATE OR REPLACE TABLE {table_name} AS SELECT * FROM df")
    con.close()

    print(f"Loaded {raw_path} into {WAREHOUSE_PATH} (table: {table_name})")


def fetch_league(league_id: str) -> Path:
    """Fetch league settings/metadata and save raw to JSON."""
    data = _get(f"/league/{league_id}")
    return _save_raw_json([data], f"league_{league_id}")


def load_league(raw_path: Path) -> None:
    _load_json_to_table(raw_path, "sleeper_league")


def fetch_rosters(league_id: str) -> Path:
    """Fetch team rosters (owned players, roster settings) and save raw to JSON."""
    data = _get(f"/league/{league_id}/rosters")
    return _save_raw_json(data, f"rosters_{league_id}")


def load_rosters(raw_path: Path) -> None:
    _load_json_to_table(raw_path, "sleeper_rosters")


def fetch_users(league_id: str) -> Path:
    """Fetch league members (owners) and save raw to JSON."""
    data = _get(f"/league/{league_id}/users")
    return _save_raw_json(data, f"users_{league_id}")


def load_users(raw_path: Path) -> None:
    _load_json_to_table(raw_path, "sleeper_users")


def fetch_matchups(league_id: str, weeks: list[int]) -> Path:
    """Fetch weekly matchups (starters, points, roster_id) and save raw to JSON."""
    rows = []
    for week in weeks:
        week_rows = _get(f"/league/{league_id}/matchups/{week}")
        for row in week_rows:
            row["week"] = week
        rows.extend(week_rows)
    return _save_raw_json(rows, f"matchups_{league_id}")


def load_matchups(raw_path: Path) -> None:
    _load_json_to_table(raw_path, "sleeper_matchups")


def fetch_transactions(league_id: str, weeks: list[int]) -> Path:
    """Fetch weekly transactions (waivers, free agent moves, trades) and save raw to JSON."""
    rows = []
    for week in weeks:
        week_rows = _get(f"/league/{league_id}/transactions/{week}")
        for row in week_rows:
            row["week"] = week
        rows.extend(week_rows)
    return _save_raw_json(rows, f"transactions_{league_id}")


def load_transactions(raw_path: Path) -> None:
    _load_json_to_table(raw_path, "sleeper_transactions")


def fetch_nfl_state() -> Path:
    """Fetch the current NFL season/week state and save raw to JSON."""
    data = _get("/state/nfl")
    return _save_raw_json([data], "state_nfl")


def load_nfl_state(raw_path: Path) -> None:
    _load_json_to_table(raw_path, "sleeper_nfl_state")


PLAYERS_MAX_AGE_SECONDS = 24 * 60 * 60


def fetch_players(sport: str = "nfl") -> Path:
    """Fetch the full player dictionary and save raw to JSON.

    Sleeper asks that this endpoint be called at most once per day, so if the
    raw file already exists and is less than a day old, the cached copy is
    reused instead of hitting the network again.
    """
    raw_path = RAW_DIR / f"players_{sport}.json"
    if raw_path.exists():
        age_seconds = time.time() - raw_path.stat().st_mtime
        if age_seconds < PLAYERS_MAX_AGE_SECONDS:
            print(f"Skipped fetch: {raw_path} is {age_seconds / 3600:.1f}h old (< 24h)")
            return raw_path

    data = _get(f"/players/{sport}")
    return _save_raw_json(data, f"players_{sport}")


def load_players(raw_path: Path) -> None:
    data = json.loads(raw_path.read_text())
    df = pd.json_normalize(list(data.values()))

    WAREHOUSE_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(WAREHOUSE_PATH))
    con.execute("CREATE OR REPLACE TABLE sleeper_players AS SELECT * FROM df")
    con.close()

    print(f"Loaded {raw_path} into {WAREHOUSE_PATH} (table: sleeper_players)")


def fetch_projections(season: int, weeks: list[int]) -> Path:
    """Fetch weekly player projections (public, not league-scoped) and save raw to JSON.

    This is a different, unauthenticated Sleeper API surface (no /v1 prefix, no league ID) —
    it's the feed Sleeper's own app uses to show projected points, sourced from RotoWire.
    """
    rows = []
    for week in weeks:
        response = requests.get(
            f"{PROJECTIONS_BASE_URL}/projections/nfl/{season}/{week}",
            params={"season_type": "regular"},
        )
        response.raise_for_status()
        week_rows = response.json()
        for row in week_rows:
            row["week"] = week
        rows.extend(week_rows)
    return _save_raw_json(rows, f"projections_{season}")


def load_projections(raw_path: Path) -> None:
    data = json.loads(raw_path.read_text())
    rows = [
        {
            "sleeper_id": row.get("player_id"),
            "player_name": (row.get("player") or {}).get("first_name", "")
            + " "
            + (row.get("player") or {}).get("last_name", ""),
            "position": (row.get("player") or {}).get("position"),
            "team": row.get("team"),
            "season": row.get("season"),
            "week": row.get("week"),
            "pts_ppr": (row.get("stats") or {}).get("pts_ppr"),
        }
        for row in data
    ]
    df = pd.DataFrame(rows)

    WAREHOUSE_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(WAREHOUSE_PATH))
    con.execute("CREATE OR REPLACE TABLE sleeper_projections AS SELECT * FROM df")
    con.close()

    print(f"Loaded {len(df)} rows into {WAREHOUSE_PATH} (table: sleeper_projections)")


if __name__ == "__main__":
    load_league(fetch_league(LEAGUE_ID))
    load_rosters(fetch_rosters(LEAGUE_ID))
    load_users(fetch_users(LEAGUE_ID))
    load_matchups(fetch_matchups(LEAGUE_ID, WEEKS))
    load_transactions(fetch_transactions(LEAGUE_ID, WEEKS))
    load_nfl_state(fetch_nfl_state())
    load_players(fetch_players())
    load_projections(fetch_projections(SEASON, WEEKS))
