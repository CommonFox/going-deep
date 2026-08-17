"""Build a per-team-per-season offensive line strength grade.

Pure warehouse-to-warehouse SQL — no fetch step, no network. Built from PFR's advanced pass/rush
stats (loaded by nfl_data.py's fetch_pfr_advstats/load_pfr_advstats), which nflverse already
parses and publishes as clean parquet, rather than scraping Pro-Football-Reference HTML directly.

Two independent signals, since pass protection and run blocking are different jobs for an O-line:
- Pass block: how often a team's QBs get pressured, weighted by their pass attempts (a raw
  per-QB rate would let a 10-attempt backup's outlier game swing the team grade as much as a
  full-season starter).
- Run block: yards before contact per rush attempt for the team's RBs/FBs — QB scrambles and WR
  jet sweeps are excluded, since those happen behind a different blocking look and would add
  noise rather than signal about the O-line itself.

Both are converted to a 0-100 percentile grade within their season (since raw pressure/YBC rates
drift year to year with rule and play-calling changes), then averaged into one overall grade.
"""

from pathlib import Path

import duckdb

from src import console
from src.silver.teams import normalize_team

WAREHOUSE_PATH = Path("data/warehouse.duckdb")

# PFR reports a player's season as a single "2TM"/"3TM" row when they changed teams, instead of
# splitting it per team — that row can't be attributed to one team's O-line, so it's dropped.
_BUILD_SQL = """
CREATE OR REPLACE TABLE offensive_line_grades AS
WITH pass_block AS (
    SELECT
        season,
        normalize_team(team) AS team,
        SUM(times_pressured) / SUM(pass_attempts) AS pressure_rate_allowed
    FROM pfr_advstats_pass
    WHERE team NOT IN ('2TM', '3TM') AND pass_attempts > 0
    GROUP BY season, normalize_team(team)
),
run_block AS (
    SELECT
        season,
        normalize_team(tm) AS team,
        SUM(ybc) / SUM(att) AS run_block_ybc_per_att
    FROM pfr_advstats_rush
    WHERE tm NOT IN ('2TM', '3TM') AND pos IN ('RB', 'FB') AND att > 0
    GROUP BY season, normalize_team(tm)
),
combined AS (
    SELECT
        COALESCE(p.season, r.season) AS season,
        COALESCE(p.team, r.team) AS team,
        p.pressure_rate_allowed,
        r.run_block_ybc_per_att
    FROM pass_block p
    FULL OUTER JOIN run_block r ON p.season = r.season AND p.team = r.team
)
SELECT
    season,
    team,
    pressure_rate_allowed,
    run_block_ybc_per_att,
    -- Lower pressure rate is better, so the percentile is inverted; higher YBC/att is better, so
    -- it isn't.
    100 * (1 - PERCENT_RANK() OVER (PARTITION BY season ORDER BY pressure_rate_allowed)) AS pass_block_grade,
    100 * PERCENT_RANK() OVER (PARTITION BY season ORDER BY run_block_ybc_per_att) AS run_block_grade,
    (
        100 * (1 - PERCENT_RANK() OVER (PARTITION BY season ORDER BY pressure_rate_allowed))
        + 100 * PERCENT_RANK() OVER (PARTITION BY season ORDER BY run_block_ybc_per_att)
    ) / 2 AS ol_grade
FROM combined
ORDER BY season, ol_grade DESC
"""


def build_offensive_line_grades() -> None:
    con = duckdb.connect(str(WAREHOUSE_PATH))
    con.create_function("normalize_team", normalize_team, ["VARCHAR"], "VARCHAR")

    con.execute(_BUILD_SQL)
    (count,) = con.execute("SELECT COUNT(*) FROM offensive_line_grades").fetchone()
    con.close()

    console.table("offensive_line_grades", count)


if __name__ == "__main__":
    build_offensive_line_grades()
