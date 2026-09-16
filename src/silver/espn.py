"""Fetch and load ESPN fantasy league data into the DuckDB warehouse."""

import json
import os
from pathlib import Path

import duckdb
import pandas as pd
import requests
from dotenv import load_dotenv

from src import console
from src.silver import snapshots
from src.silver.teams import normalize_team

load_dotenv()

RAW_DIR = Path("data/raw/espn")
WAREHOUSE_PATH = Path("data/warehouse.duckdb")
FANTASY_BASE_URL = "https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl"

# TODO: set this to your league's ID (the numeric ID in the league's ESPN URL) and season.
LEAGUE_ID = "96973123"
SEASON = 2026

# Private leagues require the espn_s2 and SWID cookies from a logged-in browser session.
# Set ESPN_S2 and SWID in a gitignored .env file (see .env.example) — never hardcode them here.
ESPN_S2 = os.environ.get("ESPN_S2")
SWID = os.environ.get("SWID")


def _cookies() -> dict:
    if not ESPN_S2 or not SWID:
        raise RuntimeError(
            "ESPN_S2 and SWID environment variables must be set to access a private league "
            "(see .env.example)."
        )
    return {"espn_s2": ESPN_S2, "SWID": SWID}


def _get(url: str, params: dict | None = None, headers: dict | None = None):
    response = requests.get(url, params=params, headers=headers, cookies=_cookies())
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
    df = pd.json_normalize(data if isinstance(data, list) else [data])

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


def _league_url(league_id: str, season: int) -> str:
    return f"{FANTASY_BASE_URL}/seasons/{season}/segments/0/leagues/{league_id}"


def fetch_league(league_id: str, season: int) -> Path:
    """Fetch league settings/metadata and save raw to JSON."""
    data = _get(_league_url(league_id, season), params={"view": "mSettings"})
    return _save_raw_json(data, f"league_{league_id}_{season}")


def load_league(raw_path: Path) -> None:
    _load_json_to_table(raw_path, "espn_league")


def fetch_teams(league_id: str, season: int) -> Path:
    """Fetch teams (owners, standings) and save raw to JSON."""
    data = _get(_league_url(league_id, season), params={"view": "mTeam"})
    return _save_raw_json(data["teams"], f"teams_{league_id}_{season}")


def load_teams(raw_path: Path) -> None:
    _load_json_to_table(raw_path, "espn_teams")


def fetch_rosters(league_id: str, season: int) -> Path:
    """Fetch team rosters (owned players) and save raw to JSON."""
    data = _get(_league_url(league_id, season), params={"view": "mRoster"})
    return _save_raw_json(data["teams"], f"rosters_{league_id}_{season}")


def load_rosters(raw_path: Path) -> None:
    _load_json_to_table(raw_path, "espn_rosters")


def fetch_matchups(league_id: str, season: int) -> Path:
    """Fetch weekly matchups (scores, box scores) and save raw to JSON."""
    data = _get(_league_url(league_id, season), params={"view": "mMatchup"})
    return _save_raw_json(data["schedule"], f"matchups_{league_id}_{season}")


def load_matchups(raw_path: Path) -> None:
    _load_json_to_table(raw_path, "espn_matchups")


def fetch_players(season: int) -> Path:
    """Fetch the active player pool and save raw to JSON."""
    data = _get(
        f"{FANTASY_BASE_URL}/seasons/{season}/players",
        params={"scoringPeriodId": 0, "view": "players_wl"},
        headers={"x-fantasy-filter": json.dumps({"filterActive": {"value": True}})},
    )
    return _save_raw_json(data, f"players_{season}")


def load_players(raw_path: Path) -> None:
    _load_json_to_table(raw_path, "espn_players")


def fetch_player_ownership(league_id: str, season: int) -> Path:
    """Fetch the player pool with ownership %, ADP, and projections, and archive as a new snapshot.

    This player pool carries the one live per-week projection ESPN publishes (see
    `load_weekly_projections`), which stops existing once the week is over — so, unlike most raw
    files in this codebase, a re-fetch is archived as a new snapshot rather than overwriting the
    last one (#117). `load_player_ownership` and `load_projections` (the season-total board) don't
    need that history and keep reading just the snapshot this returns, exactly as before;
    `save_json_snapshot` skips writing when nothing has changed since the last one, so rebuilding
    on a quiet day doesn't grow the archive.
    """
    data = _get(
        _league_url(league_id, season),
        params={"view": "kona_player_info"},
        headers={
            "x-fantasy-filter": json.dumps(
                {"players": {"limit": 3000, "sortPercOwned": {"sortAsc": False, "sortPriority": 1}}}
            )
        },
    )
    path, is_new = snapshots.save_json_snapshot(
        RAW_DIR, f"player_ownership_{league_id}_{season}", data["players"]
    )
    if is_new:
        console.archived(path, len(data["players"]))
    else:
        console.note(f"espn player pool {league_id}/{season}: unchanged, skipping snapshot")
    return path


def load_player_ownership(raw_path: Path) -> None:
    _load_json_to_table(raw_path, "espn_player_ownership")


# ESPN's numeric defaultPositionId, mapped to the position strings used elsewhere in this
# warehouse (e.g. the nflverse `ids` crosswalk). Punters are here because this league starts one
# (lineup slot 18) — ESPN projects them in full, and no other source in this warehouse projects
# punters at all.
POSITION_IDS = {1: "QB", 2: "RB", 3: "WR", 4: "TE", 5: "K", 7: "P", 16: "DST"}


def load_projections(raw_path: Path, season: int) -> None:
    """Parse ESPN's own season-total point projection out of the player pool raw file.

    Reuses the raw file already saved by fetch_player_ownership (no new network call) — each
    player's `stats` list mixes actuals and projections across seasons/weeks, so we pick out
    the season-total projection row (statSourceId=1, scoringPeriodId=0, seasonId=season).
    """
    players = json.loads(raw_path.read_text())

    rows = []
    for row in players:
        player = row.get("player", {})
        for stat in player.get("stats", []):
            if (
                stat.get("statSourceId") == 1
                and stat.get("scoringPeriodId") == 0
                and stat.get("seasonId") == season
            ):
                position = POSITION_IDS.get(player.get("defaultPositionId"))
                full_name = player.get("fullName")
                rows.append(
                    {
                        "espn_id": player.get("id"),
                        "player_name": full_name,
                        "position": position,
                        # DST rows carry no useful proTeamId; derive the team abbreviation
                        # from the name (e.g. "Texans D/ST") instead, for the team-based join
                        # DST needs (no player ID crosswalk covers team defenses).
                        "team": normalize_team(full_name) if position == "DST" else None,
                        "season": season,
                        "projected_points": stat.get("appliedTotal"),
                    }
                )
                break

    df = pd.DataFrame(rows)
    WAREHOUSE_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(WAREHOUSE_PATH))
    con.execute("CREATE OR REPLACE TABLE espn_projections AS SELECT * FROM df")
    con.close()

    console.table("espn_projections", len(df))


def _weekly_projection_rows(players: list[dict], season: int) -> list[dict]:
    """Pick out this season's per-week point projections from a player-pool payload.

    ESPN's `kona_player_info` view carries the season-total projection (`scoringPeriodId=0`)
    alongside a weekly one (`scoringPeriodId` = the week) per player; `statSourceId=1` is a
    projection, as opposed to an actual (`0`) once the week has been played.
    """
    rows = []
    for row in players:
        player = row.get("player", {})
        for stat in player.get("stats", []):
            if (
                stat.get("statSourceId") == 1
                and stat.get("scoringPeriodId")
                and stat.get("seasonId") == season
            ):
                rows.append(
                    {
                        "espn_id": player.get("id"),
                        "position": POSITION_IDS.get(player.get("defaultPositionId")),
                        "season": season,
                        "week": stat.get("scoringPeriodId"),
                        "projected_points": stat.get("appliedTotal"),
                    }
                )
    return rows


def load_weekly_projections(league_id: str, season: int) -> None:
    """Load every player-pool snapshot archived for this league/season (see
    `fetch_player_ownership`), then materialize `espn_weekly_projections` as just each week's most
    recently captured snapshot — so `waiver_rankings` and everything else already reading that name
    keeps working unchanged. The full history lives alongside it, in
    `espn_weekly_projections_snapshots`.

    Exists for `waiver_rankings` (#104) to fall back onto when Sleeper's own weekly projection is
    null for an ESPN free agent — see that module's docstring for why Sleeper's number is missing
    for so much of ESPN's pool in the first place, and why a second source is worth keeping
    distinct rather than blended into `sleeper_points`.
    """
    paths = snapshots.existing_snapshots(RAW_DIR, f"player_ownership_{league_id}_{season}", "json")
    if not paths:
        raise RuntimeError(
            f"No archived espn player pool snapshots found for {league_id}/{season} in {RAW_DIR} "
            "— run fetch_player_ownership first."
        )

    frames = []
    for path in paths:
        rows = _weekly_projection_rows(json.loads(path.read_text()), season)
        df = pd.DataFrame(rows, columns=["espn_id", "position", "season", "week", "projected_points"])
        df["captured_at"] = snapshots.captured_at_of(path)
        frames.append(df)
    combined = pd.concat(frames, ignore_index=True)

    WAREHOUSE_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(WAREHOUSE_PATH))
    con.execute(
        "CREATE OR REPLACE TABLE espn_weekly_projections_snapshots AS SELECT * FROM combined"
    )
    snapshots.refresh_latest_snapshot(
        con, "espn_weekly_projections", "espn_weekly_projections_snapshots", ["season", "week"]
    )
    rows = con.execute("SELECT count(*) FROM espn_weekly_projections").fetchone()[0]
    con.close()

    console.table("espn_weekly_projections", rows)


def fetch_transactions(league_id: str, season: int) -> Path:
    """Fetch waiver claims, trades, and adds/drops and save raw to JSON."""
    data = _get(_league_url(league_id, season), params={"view": "mTransactions2"})
    return _save_raw_json(data.get("transactions", []), f"transactions_{league_id}_{season}")


def load_transactions(raw_path: Path) -> None:
    _load_json_to_table(raw_path, "espn_transactions")


def fetch_boxscores(league_id: str, season: int) -> Path:
    """Fetch per-player boxscore stats within each matchup and save raw to JSON."""
    data = _get(_league_url(league_id, season), params={"view": "mBoxscore"})
    return _save_raw_json(data["schedule"], f"boxscores_{league_id}_{season}")


def load_boxscores(raw_path: Path) -> None:
    _load_json_to_table(raw_path, "espn_boxscores")


if __name__ == "__main__":
    load_league(fetch_league(LEAGUE_ID, SEASON))
    load_teams(fetch_teams(LEAGUE_ID, SEASON))
    load_rosters(fetch_rosters(LEAGUE_ID, SEASON))
    load_matchups(fetch_matchups(LEAGUE_ID, SEASON))
    load_players(fetch_players(SEASON))
    ownership_raw_path = fetch_player_ownership(LEAGUE_ID, SEASON)
    load_player_ownership(ownership_raw_path)
    load_projections(ownership_raw_path, SEASON)
    load_weekly_projections(LEAGUE_ID, SEASON)
    load_transactions(fetch_transactions(LEAGUE_ID, SEASON))
    load_boxscores(fetch_boxscores(LEAGUE_ID, SEASON))
