"""Fetch and load FantasyPros consensus expert rankings (ECR) into the DuckDB warehouse.

FantasyPros doesn't offer a free public API, but its rankings pages embed the full ranking
dataset as a `var ecrData = {...}` JSON blob in the page HTML. We extract that JSON and save it
as the raw archive, same as the partial-extraction approach used in espn.py (e.g. saving just
`data["teams"]` rather than the full API envelope).
"""

import json
import re
from pathlib import Path

import duckdb
import pandas as pd
import requests

RAW_DIR = Path("data/raw/fantasypros")
WAREHOUSE_PATH = Path("data/warehouse.duckdb")
BASE_URL = "https://www.fantasypros.com/nfl/rankings"
HEADERS = {"User-Agent": "Mozilla/5.0"}

# Preseason draft rankings, one per scoring format.
DRAFT_SCORING_FORMATS = {
    "standard": "consensus-cheatsheets",
    "half-ppr": "half-point-ppr-cheatsheets",
    "ppr": "ppr-cheatsheets",
}

# In-season weekly rankings, one per position.
WEEKLY_POSITIONS = ["qb", "rb", "wr", "te", "flex", "k", "dst"]

_ECR_DATA_RE = re.compile(r"var ecrData = (\{.*?\});", re.S)


def _get_players(url: str, params: dict | None = None) -> list[dict]:
    """Fetch a FantasyPros rankings page and extract its embedded player list."""
    response = requests.get(url, params=params, headers=HEADERS)
    response.raise_for_status()
    match = _ECR_DATA_RE.search(response.text)
    if not match:
        raise ValueError(f"Could not find ecrData on {response.url}")
    return json.loads(match.group(1))["players"]


def _save_raw_json(players: list[dict], filename_stem: str) -> Path:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    raw_path = RAW_DIR / f"{filename_stem}.json"
    raw_path.write_text(json.dumps(players))
    print(f"Saved {len(players)} rows to {raw_path}")
    return raw_path


def _load_json_to_table(raw_path: Path, table_name: str) -> None:
    """Load a raw JSON file into a DuckDB table (idempotent)."""
    players = json.loads(raw_path.read_text())
    df = pd.json_normalize(players)

    WAREHOUSE_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(WAREHOUSE_PATH))
    con.execute(f"CREATE OR REPLACE TABLE {table_name} AS SELECT * FROM df")
    con.close()

    print(f"Loaded {raw_path} into {WAREHOUSE_PATH} (table: {table_name})")


def fetch_draft_rankings(scoring: str) -> Path:
    """Fetch preseason overall consensus rankings for a scoring format and save raw to JSON."""
    slug = DRAFT_SCORING_FORMATS[scoring]
    players = _get_players(f"{BASE_URL}/{slug}.php")
    return _save_raw_json(players, f"draft_{scoring}")


def load_draft_rankings(raw_path: Path, scoring: str) -> None:
    _load_json_to_table(raw_path, f"fantasypros_draft_rankings_{scoring.replace('-', '_')}")


def fetch_weekly_rankings(position: str, week: int) -> Path:
    """Fetch weekly consensus rankings for a position/week and save raw to JSON."""
    players = _get_players(f"{BASE_URL}/{position}.php", params={"week": week})
    return _save_raw_json(players, f"weekly_{position}_{week}")


def load_weekly_rankings(raw_path: Path, position: str) -> None:
    _load_json_to_table(raw_path, f"fantasypros_weekly_rankings_{position}")


if __name__ == "__main__":
    current_week = 1

    for scoring_format in DRAFT_SCORING_FORMATS:
        load_draft_rankings(fetch_draft_rankings(scoring_format), scoring_format)

    for position in WEEKLY_POSITIONS:
        load_weekly_rankings(fetch_weekly_rankings(position, current_week), position)
