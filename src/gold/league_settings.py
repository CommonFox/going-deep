"""Normalize each league's scoring rules and starting-roster construction into one shared table.

Pure warehouse-to-warehouse Python/SQL — no fetch step, no network. Built from `sleeper_league`
and `espn_league` (already loaded by sleeper.py's/espn.py's load_league).

Sleeper's league settings arrive as flat columns (`scoring_settings.rec`, etc.) and a plain list
of roster slot codes (`roster_positions`). ESPN's arrive as a nested array of `{statId, points}`
scoring items and a slot-id-keyed lineup count dict, both keyed by numeric IDs ESPN doesn't
document anywhere. The statId/slot-id mappings below come from the widely-used cwendt94/espn-api
project's `SETTINGS_SCORING_FORMAT_MAP`/`POSITION_MAP`, and have been spot-checked against this
league's actual raw scoring items (statId 53 = 1.0 points, confirming full PPR).

Downstream models that care about "how many points is a lot" (e.g. league-winning-RB thresholds,
points-over-replacement) can join against this table and get a league-appropriate number instead
of hardcoding a constant tuned to one scoring format.
"""

import ast
from collections import Counter
from pathlib import Path

import duckdb
import pandas as pd

WAREHOUSE_PATH = Path("data/warehouse.duckdb")

# ESPN's numeric stat IDs -> our normalized scoring column names.
_ESPN_STAT_IDS = {
    "pass_yd_pts": 3,
    "pass_td_pts": 4,
    "pass_2pt_pts": 19,
    "pass_int_pts": 20,
    "rush_yd_pts": 24,
    "rush_td_pts": 25,
    "rush_2pt_pts": 26,
    "rec_pts": 53,
    "rec_yd_pts": 42,
    "rec_td_pts": 43,
    "rec_2pt_pts": 44,
    "fum_lost_pts": 72,
}

# ESPN's numeric lineup slot IDs -> our normalized roster slot columns. Slot 7 ("OP") is ESPN's
# superflex slot (QB eligible); slot 23 ("RB/WR/TE") is the standard non-QB flex.
_ESPN_SLOT_IDS = {
    "qb_slots": "0",
    "rb_slots": "2",
    "wr_slots": "4",
    "te_slots": "6",
    "flex_slots": "23",
    "superflex_slots": "7",
    "k_slots": "17",
    "dst_slots": "16",
    "bench_slots": "20",
    "ir_slots": "21",
}

_COLUMNS = [
    "league_key", "platform", "league_id", "season", "team_count",
    "rec_pts", "pass_yd_pts", "pass_td_pts", "pass_2pt_pts", "pass_int_pts",
    "rush_yd_pts", "rush_td_pts", "rush_2pt_pts",
    "rec_yd_pts", "rec_td_pts", "rec_2pt_pts", "fum_lost_pts",
    "qb_slots", "rb_slots", "wr_slots", "te_slots", "flex_slots", "superflex_slots",
    "k_slots", "dst_slots", "bench_slots", "ir_slots",
]


def _sleeper_settings(con: duckdb.DuckDBPyConnection) -> dict:
    row = con.sql("""
        SELECT
            "league_id" AS league_id,
            "season" AS season,
            "settings.num_teams" AS team_count,
            "roster_positions" AS roster_positions,
            "scoring_settings.rec" AS rec_pts,
            "scoring_settings.pass_yd" AS pass_yd_pts,
            "scoring_settings.pass_td" AS pass_td_pts,
            "scoring_settings.pass_2pt" AS pass_2pt_pts,
            "scoring_settings.pass_int" AS pass_int_pts,
            "scoring_settings.rush_yd" AS rush_yd_pts,
            "scoring_settings.rush_td" AS rush_td_pts,
            "scoring_settings.rush_2pt" AS rush_2pt_pts,
            "scoring_settings.rec_yd" AS rec_yd_pts,
            "scoring_settings.rec_td" AS rec_td_pts,
            "scoring_settings.rec_2pt" AS rec_2pt_pts,
            "scoring_settings.fum_lost" AS fum_lost_pts
        FROM sleeper_league
    """).df().iloc[0]

    slots = Counter(row["roster_positions"])
    return {
        "league_key": "sleeper",
        "platform": "sleeper",
        "league_id": str(row["league_id"]),
        "season": int(row["season"]),
        "team_count": int(row["team_count"]),
        "rec_pts": row["rec_pts"],
        "pass_yd_pts": row["pass_yd_pts"],
        "pass_td_pts": row["pass_td_pts"],
        "pass_2pt_pts": row["pass_2pt_pts"],
        "pass_int_pts": row["pass_int_pts"],
        "rush_yd_pts": row["rush_yd_pts"],
        "rush_td_pts": row["rush_td_pts"],
        "rush_2pt_pts": row["rush_2pt_pts"],
        "rec_yd_pts": row["rec_yd_pts"],
        "rec_td_pts": row["rec_td_pts"],
        "rec_2pt_pts": row["rec_2pt_pts"],
        "fum_lost_pts": row["fum_lost_pts"],
        "qb_slots": slots.get("QB", 0),
        "rb_slots": slots.get("RB", 0),
        "wr_slots": slots.get("WR", 0),
        "te_slots": slots.get("TE", 0),
        "flex_slots": slots.get("FLEX", 0),
        "superflex_slots": slots.get("SUPER_FLEX", 0),
        "k_slots": slots.get("K", 0),
        "dst_slots": slots.get("DEF", 0),
        "bench_slots": slots.get("BN", 0),
        "ir_slots": slots.get("IR", 0),
    }


def _espn_settings(con: duckdb.DuckDBPyConnection) -> dict:
    slot_cols = ", ".join(
        f'"settings.rosterSettings.lineupSlotCounts.{slot_id}" AS slot_{slot_id}'
        for slot_id in set(_ESPN_SLOT_IDS.values())
    )
    row = con.sql(f"""
        SELECT
            "id" AS league_id,
            "seasonId" AS season,
            "settings.size" AS team_count,
            "settings.scoringSettings.scoringItems" AS scoring_items,
            {slot_cols}
        FROM espn_league
    """).df().iloc[0]

    points_by_stat_id = {
        item["statId"]: item["points"] for item in ast.literal_eval(row["scoring_items"])
    }

    settings = {
        "league_key": "espn",
        "platform": "espn",
        "league_id": str(row["league_id"]),
        "season": int(row["season"]),
        "team_count": int(row["team_count"]),
    }
    for column, stat_id in _ESPN_STAT_IDS.items():
        settings[column] = points_by_stat_id.get(stat_id, 0.0)
    for column, slot_id in _ESPN_SLOT_IDS.items():
        settings[column] = int(row[f"slot_{slot_id}"])
    return settings


def build_league_settings() -> None:
    con = duckdb.connect(str(WAREHOUSE_PATH))
    df = pd.DataFrame([_sleeper_settings(con), _espn_settings(con)])[_COLUMNS]
    con.execute("CREATE OR REPLACE TABLE league_settings AS SELECT * FROM df")
    con.close()

    print(f"Built {len(df)} rows into {WAREHOUSE_PATH} (table: league_settings)")


if __name__ == "__main__":
    build_league_settings()
