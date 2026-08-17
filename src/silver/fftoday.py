"""Fetch and load FFToday's own season-long fantasy point projections into the DuckDB warehouse.

FFToday doesn't offer an API; each position's projections page is a single unauthenticated HTML
table, which we parse directly with BeautifulSoup (the same partial-extraction approach used
elsewhere in this project for sites without a real API).
"""

import re
import time
from pathlib import Path

import duckdb
import pandas as pd
import requests
from bs4 import BeautifulSoup

from src import console
from src.silver.teams import normalize_team

RAW_DIR = Path("data/raw/fftoday")
WAREHOUSE_PATH = Path("data/warehouse.duckdb")
BASE_URL = "https://www.fftoday.com/rankings/playerproj.php"
HEADERS = {"User-Agent": "Mozilla/5.0"}

# FFToday starts returning 403s after a burst of rapid requests; a short delay between page
# fetches keeps a full run (6 positions x 3 scoring formats) under that threshold.
REQUEST_DELAY_SECONDS = 2

# FFToday's PosID param.
POSITIONS = {"QB": 10, "RB": 20, "WR": 30, "TE": 40, "K": 80, "DST": 99}

# FFToday's LeagueID param selects a named scoring preset — confirmed against the page's own
# <select name="LeagueID"> options, not guessed from URL patterns.
SCORING_FORMATS = {"standard": "1", "half_ppr": "193033", "ppr": "107644"}

_SUFFIXES_RE = re.compile(r"\s+(jr\.?|sr\.?|ii|iii|iv|v)$", re.I)
_PUNCTUATION_RE = re.compile(r"[.'’]")


def _merge_name(name: str) -> str:
    """Normalize a player name to match the nflverse `ids.merge_name` crosswalk column."""
    name = _PUNCTUATION_RE.sub("", name)
    name = _SUFFIXES_RE.sub("", name)
    return re.sub(r"\s+", " ", name).strip().lower()


def fetch_season_projections(position: str, scoring: str, season: int) -> Path:
    """Fetch one position/scoring format's season-long projections and save raw HTML."""
    time.sleep(REQUEST_DELAY_SECONDS)
    response = requests.get(
        BASE_URL,
        params={
            "Season": season,
            "PosID": POSITIONS[position],
            "LeagueID": SCORING_FORMATS[scoring],
        },
        headers=HEADERS,
    )
    response.raise_for_status()
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    raw_path = RAW_DIR / f"proj_{position.lower()}_{scoring}_{season}.html"
    raw_path.write_text(response.text)
    console.archived(raw_path)
    return raw_path


def _find_projections_table(soup: BeautifulSoup) -> tuple[list, list]:
    """Find the (leaf) table whose header row ends in 'FPts', and split header from data rows.

    FFToday nests the real data table inside layout tables, so a naive first-table-found search
    picks up an outer wrapper whose flattened text coincidentally contains 'FPts' too.
    """
    for table in soup.find_all("table"):
        if table.find("table"):
            continue
        rows = table.find_all("tr")
        for i, row in enumerate(rows):
            cells = [c.get_text(strip=True) for c in row.find_all(["td", "th"])]
            if cells and cells[-1] == "FPts":
                return rows[i + 1 :], cells
    raise ValueError("Could not find a projections table with an 'FPts' column")


def _parse_table(html: str, position: str) -> pd.DataFrame:
    soup = BeautifulSoup(html, "html.parser")
    data_rows, _header = _find_projections_table(soup)

    records = []
    for row in data_rows:
        cells = [c.get_text(strip=True) for c in row.find_all("td")]
        if len(cells) < 3:
            continue
        try:
            projected_points = float(cells[-1].replace(",", ""))
        except ValueError:
            continue

        if position == "DST":
            # DST rows have a full team name ("Houston Texans") in place of a player name, and
            # no separate team column — there's no player crosswalk for team defenses, so this
            # joins by team abbreviation instead in consensus.py.
            team_name = cells[1]
            records.append(
                {
                    "merge_name": None,
                    "player_name": team_name,
                    "team": normalize_team(team_name),
                    "projected_points": projected_points,
                }
            )
        else:
            player_name = cells[1]
            records.append(
                {
                    "merge_name": _merge_name(player_name),
                    "player_name": player_name,
                    "team": cells[2],
                    "projected_points": projected_points,
                }
            )
    return pd.DataFrame(records)


def load_season_projections(fetched: list[tuple[str, str, int, Path]]) -> None:
    """Parse raw HTML files (from fetch_season_projections) into one unified table.

    `fetched` is a list of (position, scoring, season, raw_path) tuples, so metadata comes from
    the fetch call directly rather than being re-derived from filenames.
    """
    dfs = []
    for position, scoring, season, raw_path in fetched:
        df = _parse_table(raw_path.read_text(), position)
        df["position"] = position
        df["scoring"] = scoring
        df["season"] = season
        dfs.append(df)

    combined = pd.concat(dfs, ignore_index=True)
    WAREHOUSE_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(WAREHOUSE_PATH))
    con.execute("CREATE OR REPLACE TABLE fftoday_projections AS SELECT * FROM combined")
    con.close()

    console.table("fftoday_projections", len(combined))


if __name__ == "__main__":
    season = 2026

    fetched = [
        (position, scoring, season, fetch_season_projections(position, scoring, season))
        for position in POSITIONS
        for scoring in SCORING_FORMATS
    ]
    load_season_projections(fetched)
