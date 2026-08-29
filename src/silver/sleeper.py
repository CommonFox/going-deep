"""Fetch and load Sleeper league data into the DuckDB warehouse."""

import json
import time
from pathlib import Path

import duckdb
import pandas as pd
import requests

from src import console

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
    console.archived(raw_path)
    return raw_path


def _load_json_to_table(raw_path: Path, table_name: str) -> None:
    """Load a raw JSON file into a DuckDB table (idempotent)."""
    data = json.loads(raw_path.read_text())
    df = pd.json_normalize(data)

    WAREHOUSE_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(WAREHOUSE_PATH))
    if df.empty and len(df.columns) == 0:
        con.execute(f"DROP TABLE IF EXISTS {table_name}")
        con.close()
        console.note(f"{table_name}: source returned no rows — table dropped")
        return

    con.execute(f"CREATE OR REPLACE TABLE {table_name} AS SELECT * FROM df")
    con.close()

    console.table(table_name, len(df))


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


def select_draft(drafts: list[dict], season) -> dict:
    """This season's draft, out of every draft the league has on record.

    Pure, and shared with `src.draft.live`, which watches the draft this picks out. The two
    choosing differently would mean the tool drafting from one draft while the archive kept
    another, which is a disagreement neither screen would show.

    Sleeper spells the season as a string and everything here holds an int, so both are compared
    as strings. Where a season has more than one draft — a league that restarted or redrafted
    keeps the abandoned one on record beside the real one — the newest wins.
    """
    this_season = [
        draft for draft in drafts if str(draft.get("season")) == str(season)
    ]
    if not this_season:
        raise RuntimeError(
            f"No {season} draft among the {len(drafts)} this league has on record."
        )
    return max(this_season, key=lambda draft: draft.get("created") or 0)


def _draft_id(league_id: str, season: int) -> str:
    """The ID of this season's draft, which both fetches below are keyed on."""
    return select_draft(_get(f"/league/{league_id}/drafts"), season)["draft_id"]


def fetch_draft(league_id: str, season: int) -> Path:
    """Fetch this season's draft record whole and save raw to JSON.

    Two calls, because the league's draft list is not the record: it carries the season and the
    created time `select_draft` chooses on, but not `slot_to_roster_id` or the settings, and those
    are what say how many teams and rounds the draft ran and which roster each seat's picks landed
    on. The archive keeps the fuller one.

    Named by draft ID rather than league ID, unlike every other file here, because a league drafts
    again next season: keying on the draft means each year's record lands beside the last rather
    than over it.
    """
    record = _get(f"/draft/{_draft_id(league_id, season)}")
    return _save_raw_json([record], f"draft_{record['draft_id']}")


def load_draft(raw_path: Path) -> None:
    _load_json_to_table(raw_path, "sleeper_draft")


def fetch_draft_picks(league_id: str, season: int) -> Path:
    """Fetch every pick made in this season's draft and save raw to JSON.

    This is the archive step the live draft tool deliberately does not do. `src.draft.live` polls
    this same endpoint every few seconds for two hours and writes none of it, because that would
    be thousands of near-identical files; the picks are archived once, from here, after the draft.
    """
    draft_id = _draft_id(league_id, season)
    return _save_raw_json(_get(f"/draft/{draft_id}/picks"), f"draft_picks_{draft_id}")


def load_draft_picks(raw_path: Path) -> None:
    """Load the archived picks into the warehouse, taking the payload's own shape.

    Nothing is projected out by hand here, unlike `load_projections` below. These picks have never
    been seen — this league has drafted once, in a draft that has not been made yet — so a written
    column list would be a guess against a payload nobody has read, and a wrong guess in one would
    load as nulls rather than fail. `json_normalize` takes whatever Sleeper sends, and the raw file
    beside it stays the source of truth either way.

    Before the draft the payload is an empty list and the table is dropped with a note, rather than
    created with a schema invented to fill the gap. So `sleeper_draft_picks` exists from the first
    pick onwards and not before, which is the honest answer to "what did this league draft".
    """
    _load_json_to_table(raw_path, "sleeper_draft_picks")


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
            console.note(
                f"skipped fetch: {raw_path.name} is {age_seconds / 3600:.1f}h old (< 24h)"
            )
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

    console.table("sleeper_players", len(df))


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
    """Load weekly projections, keeping all three scoring flavours and projected receptions.

    Sleeper hands back `pts_std`, `pts_half_ppr` and `pts_ppr` side by side, so a league's own
    reception value is a column choice rather than a conversion — which matters here because the
    Sleeper league is half-PPR and every other projection source in this warehouse publishes full
    PPR. `rec` is kept for the same reason from the other direction: `half = ppr - 0.5 * rec` is an
    identity, not an approximation (checked against a real row: 5.17 - 0.5 * 1.78 = 4.28 =
    `pts_half_ppr`), so these receptions are what lets a PPR-only source be restated in half-PPR.
    """
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
            "pts_std": (row.get("stats") or {}).get("pts_std"),
            "pts_half_ppr": (row.get("stats") or {}).get("pts_half_ppr"),
            "pts_ppr": (row.get("stats") or {}).get("pts_ppr"),
            "rec": (row.get("stats") or {}).get("rec"),
        }
        for row in data
    ]
    df = pd.DataFrame(rows)

    WAREHOUSE_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(WAREHOUSE_PATH))
    con.execute("CREATE OR REPLACE TABLE sleeper_projections AS SELECT * FROM df")
    con.close()

    console.table("sleeper_projections", len(df))


if __name__ == "__main__":
    load_league(fetch_league(LEAGUE_ID))
    load_rosters(fetch_rosters(LEAGUE_ID))
    load_users(fetch_users(LEAGUE_ID))
    load_draft(fetch_draft(LEAGUE_ID, SEASON))
    load_draft_picks(fetch_draft_picks(LEAGUE_ID, SEASON))
    load_matchups(fetch_matchups(LEAGUE_ID, WEEKS))
    load_transactions(fetch_transactions(LEAGUE_ID, WEEKS))
    load_nfl_state(fetch_nfl_state())
    load_players(fetch_players())
    load_projections(fetch_projections(SEASON, WEEKS))
