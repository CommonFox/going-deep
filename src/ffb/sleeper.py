"""Fetch and load Sleeper league data into the DuckDB warehouse."""

import json
from pathlib import Path

import duckdb
import pandas as pd
import requests

RAW_DIR = Path("data/raw/sleeper")
WAREHOUSE_PATH = Path("data/warehouse.duckdb")
BASE_URL = "https://api.sleeper.app/v1"

# TODO: set this to your league's ID (the numeric ID in the league's Sleeper URL).
LEAGUE_ID = "REPLACE_WITH_YOUR_LEAGUE_ID"

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


if __name__ == "__main__":
    load_league(fetch_league(LEAGUE_ID))
    load_rosters(fetch_rosters(LEAGUE_ID))
    load_users(fetch_users(LEAGUE_ID))
    load_matchups(fetch_matchups(LEAGUE_ID, WEEKS))
