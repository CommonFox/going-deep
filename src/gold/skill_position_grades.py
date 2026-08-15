"""Build a per-team-per-season skill-position (WR/TE/RB) corps strength grade.

Pure warehouse-to-warehouse SQL — no fetch step, no network. Built from nflverse's Next Gen Stats
(already loaded by nfl_data.py's fetch_ngs_data/load_ngs_data), using "over expectation" metrics
rather than raw production, so the grade reflects talent/skill instead of just recycling the
volume and scoring this warehouse is ultimately trying to project.

Three position groups, each graded on the metric(s) NGS actually offers for that role:
- WR/TE: average separation created (route-running skill, independent of who's throwing to them)
  and YAC over expectation (playmaking after the catch, already adjusted for context by NGS's own
  model), each target/reception-weighted across a team's receivers at that position.
- RB: rush yards over expectation per attempt — the rushing equivalent of YAC over expectation,
  attempt-weighted across a team's backs. NGS has no separate "over expectation" receiving metric
  for backs (its receiving stat type only covers WR/TE), so RB grading is scoped to rushing.

Each metric is converted to a 0-100 percentile within its season and position, then averaged
(where more than one metric exists) into one grade per season/team/position.
"""

from pathlib import Path

import duckdb

from src.silver.teams import normalize_team

WAREHOUSE_PATH = Path("data/warehouse.duckdb")

# week = 0 is nflverse's season-aggregate row; season_type = 'REG' excludes playoffs, which would
# otherwise mix a handful of extra games for a few teams into the grade.
#
# The week = 0 row's own team_abbr is unreliable for 2021 (nflverse left it NULL for a chunk of
# players that season, including some with real volume, e.g. Travis Kelce) — so team is instead
# resolved from each player's most-played team across that season's weekly (week 1-18) rows,
# which also handles in-season trades more sensibly than whatever the aggregate row happened to
# carry.
_BUILD_SQL = """
CREATE OR REPLACE TABLE skill_position_grades AS
WITH team_by_player_season AS (
    SELECT
        stat_type, season, player_gsis_id, team_abbr,
        ROW_NUMBER() OVER (
            PARTITION BY stat_type, season, player_gsis_id ORDER BY COUNT(*) DESC
        ) AS rn
    FROM ngs_data
    WHERE week BETWEEN 1 AND 18 AND team_abbr IS NOT NULL
    GROUP BY stat_type, season, player_gsis_id, team_abbr
),
receiving_agg AS (
    SELECT
        r.season,
        normalize_team(t.team_abbr) AS team,
        r.player_position AS position,
        SUM(r.avg_separation * r.targets) / SUM(r.targets) AS separation,
        SUM(r.avg_yac_above_expectation * r.receptions) / SUM(r.receptions) AS yac_over_expected
    FROM ngs_data r
    JOIN team_by_player_season t
        ON t.stat_type = 'receiving' AND t.season = r.season
        AND t.player_gsis_id = r.player_gsis_id AND t.rn = 1
    WHERE r.stat_type = 'receiving' AND r.week = 0 AND r.season_type = 'REG'
        AND r.player_position IN ('WR', 'TE') AND r.targets > 0
    GROUP BY r.season, normalize_team(t.team_abbr), r.player_position
),
rushing_agg AS (
    SELECT
        r.season,
        normalize_team(t.team_abbr) AS team,
        SUM(r.rush_yards_over_expected) / SUM(r.rush_attempts) AS rush_yards_over_expected_per_att
    FROM ngs_data r
    JOIN team_by_player_season t
        ON t.stat_type = 'rushing' AND t.season = r.season
        AND t.player_gsis_id = r.player_gsis_id AND t.rn = 1
    WHERE r.stat_type = 'rushing' AND r.week = 0 AND r.season_type = 'REG' AND r.rush_attempts > 0
    GROUP BY r.season, normalize_team(t.team_abbr)
),
receiving_graded AS (
    SELECT
        season, team, position, separation, yac_over_expected,
        CAST(NULL AS DOUBLE) AS rush_yards_over_expected_per_att,
        (
            100 * PERCENT_RANK() OVER (PARTITION BY season, position ORDER BY separation)
            + 100 * PERCENT_RANK() OVER (PARTITION BY season, position ORDER BY yac_over_expected)
        ) / 2 AS grade
    FROM receiving_agg
),
rushing_graded AS (
    SELECT
        season, team, 'RB' AS position,
        CAST(NULL AS DOUBLE) AS separation, CAST(NULL AS DOUBLE) AS yac_over_expected,
        rush_yards_over_expected_per_att,
        100 * PERCENT_RANK() OVER (PARTITION BY season ORDER BY rush_yards_over_expected_per_att) AS grade
    FROM rushing_agg
)
SELECT * FROM receiving_graded
UNION ALL
SELECT * FROM rushing_graded
ORDER BY season, position, grade DESC
"""


def build_skill_position_grades() -> None:
    con = duckdb.connect(str(WAREHOUSE_PATH))
    con.create_function("normalize_team", normalize_team, ["VARCHAR"], "VARCHAR")

    con.execute(_BUILD_SQL)
    (count,) = con.execute("SELECT COUNT(*) FROM skill_position_grades").fetchone()
    con.close()

    print(f"Built {count} rows into {WAREHOUSE_PATH} (table: skill_position_grades)")


if __name__ == "__main__":
    build_skill_position_grades()
