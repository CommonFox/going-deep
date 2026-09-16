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

from src import console
from src.silver import snapshots

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
    console.archived(raw_path, len(players))
    return raw_path


def _load_json_to_table(raw_path: Path, table_name: str) -> None:
    """Load a raw JSON file into a DuckDB table (idempotent)."""
    players = json.loads(raw_path.read_text())
    df = pd.json_normalize(players)

    WAREHOUSE_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(WAREHOUSE_PATH))
    con.execute(f"CREATE OR REPLACE TABLE {table_name} AS SELECT * FROM df")
    con.close()

    console.table(table_name, len(df))


def fetch_draft_rankings(scoring: str) -> Path:
    """Fetch preseason overall consensus rankings for a scoring format and save raw to JSON."""
    slug = DRAFT_SCORING_FORMATS[scoring]
    players = _get_players(f"{BASE_URL}/{slug}.php")
    return _save_raw_json(players, f"draft_{scoring}")


def load_draft_rankings(raw_path: Path, scoring: str) -> None:
    _load_json_to_table(raw_path, f"fantasypros_draft_rankings_{scoring.replace('-', '_')}")


def fetch_weekly_rankings(position: str, season: int, week: int) -> Path:
    """Fetch weekly consensus rankings for a position/week and archive as a new snapshot.

    Unlike `fetch_draft_rankings`, this is never a single overwritten raw file: rankings for the
    same (position, season, week) fetched again later in the week are archived alongside the
    earlier one rather than replacing it (#117) — a Monday ranking and a Thursday one for the same
    week are different facts. `save_json_snapshot` skips writing when nothing changed since the
    last snapshot, so rebuilding on a quiet day doesn't grow the archive.
    """
    players = _get_players(f"{BASE_URL}/{position}.php", params={"week": week})
    stem = f"weekly_{position}_{season}_{week}"
    path, is_new = snapshots.save_json_snapshot(RAW_DIR, stem, players)
    if is_new:
        console.archived(path, len(players))
    else:
        console.note(f"fantasypros weekly {position} week {week}: unchanged, skipping snapshot")
    return path


# One snapshot's filename, e.g. weekly_qb_2026_2_20260901T120000000000.json — position, season,
# week and captured_at, in that order. The raw payload carries none of the first three (week is a
# query parameter, not a response field, and the response has no season at all), so
# load_weekly_rankings reads them back out of the filename instead.
_WEEKLY_SNAPSHOT_NAME_RE = re.compile(
    r"^weekly_(?P<position>[a-z]+)_(?P<season>\d+)_(?P<week>\d+)_(?P<captured_at>\d{8}T\d{12})\.json$"
)


def _season_and_week_from_snapshot_name(path: Path) -> tuple[int, int]:
    """Recover (season, week) from a weekly-rankings snapshot's filename."""
    match = _WEEKLY_SNAPSHOT_NAME_RE.match(path.name)
    if not match:
        raise ValueError(f"{path} doesn't look like a fantasypros weekly rankings snapshot")
    return int(match["season"]), int(match["week"])


def load_weekly_rankings(position: str, season: int) -> None:
    """Load every weekly-rankings snapshot archived for this position and season, then materialize
    the original table name (`fantasypros_weekly_rankings_<position>`) as just each week's most
    recently captured snapshot — so `weekly_projections.py` and everything else already reading
    that name keeps working unchanged. The full history lives alongside it, in
    `fantasypros_weekly_rankings_<position>_snapshots`.
    """
    paths = snapshots.existing_snapshots(RAW_DIR, f"weekly_{position}_{season}", "json")
    if not paths:
        raise RuntimeError(
            f"No archived fantasypros weekly rankings found for {position} {season} in {RAW_DIR} "
            "— run fetch_weekly_rankings first."
        )

    frames = []
    for path in paths:
        season_of, week_of = _season_and_week_from_snapshot_name(path)
        df = pd.json_normalize(json.loads(path.read_text()))
        df["season"] = season_of
        df["week"] = week_of
        df["captured_at"] = snapshots.captured_at_of(path)
        frames.append(df)
    combined = pd.concat(frames, ignore_index=True)

    table_name = f"fantasypros_weekly_rankings_{position}"
    snapshots_table = f"{table_name}_snapshots"

    WAREHOUSE_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(WAREHOUSE_PATH))
    con.execute(f"CREATE OR REPLACE TABLE {snapshots_table} AS SELECT * FROM combined")
    snapshots.refresh_latest_snapshot(con, table_name, snapshots_table, ["season", "week"])
    rows = con.execute(f"SELECT count(*) FROM {table_name}").fetchone()[0]
    con.close()

    console.table(table_name, rows)


# FantasyPros' historical ADP-by-year page is a client-rendered app with no server-embedded data
# (unlike the rankings pages above), so there's no clean fetch_* for it. Instead this reads
# manually-exported CSVs the user downloads by hand from fantasypros.com/nfl/adp/overall.php and
# drops into RAW_DIR as `FantasyPros_<year>_Overall_ADP_Rankings.csv` — same raw-archive/load split
# as everywhere else, just with a human doing the "fetch" step instead of a network call.
_ADP_FILENAME_RE = re.compile(r"FantasyPros_(\d{4})_Overall_ADP_Rankings\.csv")

# "Player (Bye)" packs name/team/bye into one string with an irregular format: team+bye are
# omitted entirely for a large fraction of rows (most common in FantasyPros' older archives —
# ~90% of 2015 rows have no team/bye at all, dropping to ~5-20% by the mid-2020s), and DST rows
# spell out the full team name with no separate abbreviation token before the bye.
_PLAYER_BYE_RE = re.compile(r"^(?P<name>.+?)(?:\s{2,}(?:(?P<team>[A-Z]{2,3})\s)?\((?P<bye>\d+)\))?$")

# "POS" is normally a position + positional rank (e.g. "RB1"), but a handful of IDP-style rows
# (OL/CB/LB) carry just the bare position with no rank suffix.
_POSITION_RE = re.compile(r"^([A-Z]+?)(\d+)?$")


def load_adp_manual(raw_dir: Path = RAW_DIR) -> None:
    """Parse every manually-downloaded ADP CSV in raw_dir into one combined table (no network)."""
    frames = []
    for raw_path in sorted(raw_dir.glob("FantasyPros_*_Overall_ADP_Rankings.csv")):
        match = _ADP_FILENAME_RE.fullmatch(raw_path.name)
        if not match:
            continue
        season = int(match.group(1))

        df = pd.read_csv(raw_path)
        parsed_name = df["Player (Bye)"].str.extract(_PLAYER_BYE_RE)
        parsed_pos = df["POS"].str.extract(_POSITION_RE)

        frames.append(pd.DataFrame({
            "season": season,
            "rank": df["Rank"],
            "name": parsed_name["name"],
            "team": parsed_name["team"],
            "bye": pd.to_numeric(parsed_name["bye"]),
            "position": parsed_pos[0],
            "position_rank": pd.to_numeric(parsed_pos[1]),
            "adp": df["AVG"],
        }))

    if not frames:
        console.note("no FantasyPros_*_Overall_ADP_Rankings.csv files found — skipping fantasypros_adp")
        return

    df = pd.concat(frames, ignore_index=True)
    WAREHOUSE_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(WAREHOUSE_PATH))
    con.execute("CREATE OR REPLACE TABLE fantasypros_adp AS SELECT * FROM df")
    con.close()

    console.table("fantasypros_adp", len(df), f"from {len(frames)} files")


def _current_week(con: duckdb.DuckDBPyConnection) -> int:
    """The live NFL week, read from sleeper_nfl_state rather than a hand-typed constant.

    `src.silver.sleeper`'s `load_nfl_state` loads that table before this module runs in
    `scripts/build_warehouse.sh`. If it isn't there — a fresh clone, or a build that skipped that
    step — this raises rather than defaulting to week 1, since fetching and archiving the wrong
    week silently is worse than the build stopping.
    """
    try:
        return con.execute("SELECT week FROM sleeper_nfl_state").fetchone()[0]
    except duckdb.CatalogException as error:
        raise RuntimeError(
            "sleeper_nfl_state not found in the warehouse — run src.silver.sleeper "
            "(load_nfl_state) before src.silver.fantasypros."
        ) from error


def _current_season(con: duckdb.DuckDBPyConnection) -> int:
    """The season being played, read from sleeper_nfl_state alongside `_current_week`.

    Needed because weekly-rankings snapshots are now archived rather than overwritten (#117) and
    are keyed on (season, week) — the raw payload carries neither, so both are stamped on at load
    time rather than read back out of the response.
    """
    try:
        return con.execute("SELECT season FROM sleeper_nfl_state").fetchone()[0]
    except duckdb.CatalogException as error:
        raise RuntimeError(
            "sleeper_nfl_state not found in the warehouse — run src.silver.sleeper "
            "(load_nfl_state) before src.silver.fantasypros."
        ) from error


if __name__ == "__main__":
    con = duckdb.connect(str(WAREHOUSE_PATH))
    try:
        current_season = _current_season(con)
        current_week = _current_week(con)
    finally:
        con.close()
    console.note(f"fantasypros weekly rankings: current season/week is {current_season}/{current_week}")

    for scoring_format in DRAFT_SCORING_FORMATS:
        load_draft_rankings(fetch_draft_rankings(scoring_format), scoring_format)

    for position in WEEKLY_POSITIONS:
        fetch_weekly_rankings(position, current_season, current_week)
        load_weekly_rankings(position, current_season)

    load_adp_manual()
