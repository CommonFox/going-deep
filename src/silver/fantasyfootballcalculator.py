"""Fetch and load FantasyFootballCalculator's historical ADP into the DuckDB warehouse.

FFC offers a free, public, unauthenticated JSON API for average draft position, broken out by
scoring format and year, going back to at least 2012. No official API rate limit is published;
FFC's own docs just ask not to call it too frequently, so requests here are spaced out.

The `teams` query parameter turns out to be cosmetic: comparing responses for the same
season/format across `teams=8/10/12/14` shows identical underlying `adp` values and
`total_drafts` counts, only the `adp_formatted` round.pick display string changes. So this is one
pooled ADP dataset per scoring format/season (not genuinely split by league size), fixed here at
teams=12 since that's the value FFC's own site defaults to.

One fetch call per (scoring format, season) pair (FFC has no batched multi-year endpoint), so raw
files are saved one per pair and `load_adp_all` rebuilds the whole table from every raw file
already on disk — no network access, consistent with every other `load_*` in this warehouse.
"""

import json
import time
from pathlib import Path

import duckdb
import pandas as pd
import requests

RAW_DIR = Path("data/raw/fantasyfootballcalculator")
WAREHOUSE_PATH = Path("data/warehouse.duckdb")
BASE_URL = "https://fantasyfootballcalculator.com/api/v1"

TEAMS = 12
SCORING_FORMATS = ["standard", "half-ppr", "ppr"]

# 2026 included even though the season hasn't been played yet: this year's preseason ADP is
# useful as live input to a trained model, just not as a backtestable season.
SEASONS = list(range(2015, 2027))

# Be a good citizen against an unofficial, undocumented rate limit.
_REQUEST_DELAY_SECONDS = 0.5


def fetch_adp(scoring_format: str, season: int) -> Path:
    """Fetch one scoring-format/season of ADP and save the raw response to JSON."""
    response = requests.get(
        f"{BASE_URL}/adp/{scoring_format}", params={"teams": TEAMS, "year": season}
    )
    response.raise_for_status()
    time.sleep(_REQUEST_DELAY_SECONDS)

    RAW_DIR.mkdir(parents=True, exist_ok=True)
    raw_path = RAW_DIR / f"adp_{scoring_format}_{season}.json"
    raw_path.write_text(json.dumps(response.json()))
    print(f"Saved {raw_path}")
    return raw_path


def load_adp_all() -> None:
    """Load every raw ADP file in the archive into one combined table (no network).

    Some early scoring-format/season combinations (e.g. half-PPR wasn't common yet in 2015-2017)
    have no drafts on record; FFC returns `{"status": "Error", ...}` for those instead of a
    players list, so they're skipped rather than treated as a load failure.
    """
    rows = []
    skipped = []
    raw_paths = sorted(RAW_DIR.glob("adp_*.json"))
    for raw_path in raw_paths:
        scoring_format, season = raw_path.stem.removeprefix("adp_").rsplit("_", 1)
        data = json.loads(raw_path.read_text())
        if "players" not in data:
            skipped.append(raw_path.name)
            continue
        for player in data["players"]:
            rows.append({**player, "scoring_format": scoring_format, "season": int(season)})

    if skipped:
        print(f"Skipped {len(skipped)} file(s) with no ADP data: {', '.join(skipped)}")

    df = pd.DataFrame(rows)
    WAREHOUSE_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(WAREHOUSE_PATH))
    con.execute("CREATE OR REPLACE TABLE ffc_adp AS SELECT * FROM df")
    con.close()

    print(f"Loaded {len(df)} rows from {len(raw_paths)} files into {WAREHOUSE_PATH} (table: ffc_adp)")


if __name__ == "__main__":
    for scoring_format in SCORING_FORMATS:
        for season in SEASONS:
            fetch_adp(scoring_format, season)
    load_adp_all()
