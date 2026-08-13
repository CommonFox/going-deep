"""Fetch and load CBS Sports' own season-long fantasy point projections into the DuckDB
warehouse.

CBS doesn't offer an API; each position/scoring-format's projections page is a single
unauthenticated HTML table, which we parse directly with BeautifulSoup (the same
partial-extraction approach used elsewhere in this project for sites without a real API).
"""

import re
from pathlib import Path

import duckdb
import pandas as pd
import requests
from bs4 import BeautifulSoup

RAW_DIR = Path("data/raw/cbs")
WAREHOUSE_PATH = Path("data/warehouse.duckdb")
BASE_URL = "https://www.cbssports.com/fantasy/football/stats"
HEADERS = {"User-Agent": "Mozilla/5.0"}

# CBS's positions and URL scoring-format slugs. There's no half-PPR variant (that slug 404s);
# DST is fetched for completeness even though it's excluded from the cross-source join later
# (no entry in the nflverse player ID crosswalk, since it's a team, not a player).
POSITIONS = ["QB", "RB", "WR", "TE", "K", "DST"]
SCORING_FORMATS = {"standard": "nonppr", "ppr": "ppr"}

_CBS_ID_RE = re.compile(r"/nfl/players/(\d+)/")


def fetch_season_projections(position: str, scoring: str, season: int) -> Path:
    """Fetch one position/scoring format's season-long projections and save raw HTML."""
    url = f"{BASE_URL}/{position}/{season}/season/projections/{SCORING_FORMATS[scoring]}/"
    response = requests.get(url, headers=HEADERS)
    response.raise_for_status()
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    raw_path = RAW_DIR / f"proj_{position.lower()}_{scoring}_{season}.html"
    raw_path.write_text(response.text)
    print(f"Saved {raw_path}")
    return raw_path


def _parse_table(html: str) -> pd.DataFrame:
    soup = BeautifulSoup(html, "html.parser")

    records = []
    for row in soup.find_all("tr", class_="TableBase-bodyTr"):
        name_cell = row.find("td")
        long_name = name_cell.find("span", class_="CellPlayerName--long")
        anchor = long_name.find("a") if long_name else name_cell.find("a")
        if not anchor:
            continue
        match = _CBS_ID_RE.search(anchor.get("href", ""))
        team_span = name_cell.find("span", class_="CellPlayerName-team")

        number_cells = row.find_all("td", class_=lambda c: c and "TableBase-bodyTd--number" in c)
        if len(number_cells) < 2:
            continue
        try:
            fpts = float(number_cells[-2].get_text(strip=True).replace(",", ""))
            fppg = float(number_cells[-1].get_text(strip=True).replace(",", ""))
        except ValueError:
            continue

        records.append(
            {
                "cbs_id": int(match.group(1)) if match else None,
                "player_name": anchor.get_text(strip=True),
                "team": team_span.get_text(strip=True) if team_span else None,
                "projected_points": fpts,
                "fppg": fppg,
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
        df = _parse_table(raw_path.read_text())
        df["position"] = position
        df["scoring"] = scoring
        df["season"] = season
        dfs.append(df)

    combined = pd.concat(dfs, ignore_index=True)
    WAREHOUSE_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(WAREHOUSE_PATH))
    con.execute("CREATE OR REPLACE TABLE cbs_projections AS SELECT * FROM combined")
    con.close()

    print(f"Loaded {len(combined)} rows into {WAREHOUSE_PATH} (table: cbs_projections)")


if __name__ == "__main__":
    season = 2026

    fetched = [
        (position, scoring, season, fetch_season_projections(position, scoring, season))
        for position in POSITIONS
        for scoring in SCORING_FORMATS
    ]
    load_season_projections(fetched)
