"""Describe the punting situation each team creates, on both sides of the ball.

Pure warehouse-to-warehouse Python/SQL — no fetch step, no network. Built from `pbp_punts` (loaded
by nfl_data.py), team game counts from `schedules`, and the punting scoring in `league_settings`.

`punters.py` establishes that a punter's fantasy volume belongs to his team rather than to him.
This is the table that says what that team actually does, and it exists because the obvious
intuition about it is only half right.

## Punting more is worth less per punt — and worth more anyway

The instinct is that a bad offense makes a good fantasy punter: three-and-outs mean punts, and
every punt is a point. The complication is *where* those drives die. Under this league's scoring a
punt is only really valuable if it can be dropped inside the 20, and a punter pinned on his own
goal line can't do that — he has to hit it as far as he can and live with the return:

    punt struck from        gross   inside-20   returned   points per punt
    own 1-10                 49.2       0.004      0.681              0.39
    own 11-20                49.0       0.014      0.636              0.48
    own 21-30                48.9       0.109      0.597              0.74
    own 31-40                48.3       0.415      0.474              1.67
    own 41-50                43.6       0.689      0.222              2.84
    opponent's side          36.2       0.785      0.061              3.73

A punt from plus territory is worth nearly ten times a punt from your own end zone, and a punt from
inside your own 10 is worth *less than the one point* the flat rate pays, because two-thirds of
them get returned. So the same three-and-out is worth wildly different amounts depending on where
it started, and teams that punt most do punt from deeper (own 33.4 on average, against own 35.0 for
the teams that punt least), taking about 11% off their value per punt.

Volume still wins comfortably. Sorted into quintiles by how often they punt:

    fewest punts   3.03/game × 1.85 per punt = 5.60 punt points per game
    most punts     5.33/game × 1.64 per punt = 8.72 punt points per game

The efficiency penalty is real but small next to a 76% swing in volume. The best fantasy situation
of all is a bad offense whose drives still reach midfield before stalling — 8.91 punt points a
game, against 5.75 for a good offense that punts from deep — but "bad offense" is doing most of
that work.

## Why none of this is in the projection

Because it doesn't forecast. Average punt spot explains plenty *within* a season (r=0.61 with a
team's points per punt) but is barely a team trait year to year (r=0.21), and last season's punt
spot predicts next season's punter scoring rate at r=0.02 — nothing. Adding it alongside volume
moves the prediction of next season's punt points per game from r=0.32 to r=0.34.

The same holds for matchup. Opponent identity explains ~9% of the variance in a single punter-game
— nearly as much as which punter it is (12.7%) — and the spread from the most to least
punter-friendly defense is 2.44 points a game pooled over eleven seasons (Denver 8.60, Las Vegas
6.16). But a defense's punter points allowed carries year to year at only r=0.16, and out of
sample, matchup-aware weekly
rankings score no better than team punt rate alone (within-week Spearman 0.091 vs 0.092). Weather
is weaker still: dome and outdoor punters score 9.56 and 9.69 a game, and wind buckets don't order
at all.

So this table is diagnostic rather than predictive: it explains why a punter scored what he did,
and it is where to look first if the punting environment ever does turn out to be forecastable.
The one column with real forward signal is `punts_forced_per_game`, which persists at r=0.39.
"""

from pathlib import Path

import duckdb

from src import console
from src.gold.punters import league_scoring

WAREHOUSE_PATH = Path("data/warehouse.duckdb")

# Punt outcomes derived from play-by-play. weekly_stats carries the same counts per punter and is
# the authority `punters.py` scores against, but it has no field position, which is the whole point
# of this table — so the flags are rebuilt here and agree with weekly_stats to within 0.1%
# (returned 10,270 vs 10,283; fair catches exact; blocked 120 vs 116) across 24k punts.
#
# `returned` is the residual case: nflverse flags fair catches, touchbacks, downed punts, punts out
# of bounds, blocks and punts into the end zone explicitly, and anything left over is a punt the
# returner actually ran with.
_PUNT_FLAGS = """
    SELECT
        posteam AS team, defteam AS opponent, season, game_id,
        100 - yardline_100 AS own_yard,
        kick_distance,
        CASE WHEN punt_inside_twenty = 1
              AND yardline_100 - kick_distance + COALESCE(return_yards, 0) < 10
             THEN 1 ELSE 0 END AS in10,
        COALESCE(punt_inside_twenty, 0) AS in20,
        COALESCE(touchback, 0) AS touchback,
        COALESCE(punt_fair_catch, 0) AS fair_catch,
        COALESCE(punt_blocked, 0) AS blocked,
        CASE WHEN punt_fair_catch = 1 OR touchback = 1 OR punt_downed = 1
                  OR punt_out_of_bounds = 1 OR punt_blocked = 1 OR punt_in_endzone = 1
             THEN 0 ELSE 1 END AS returned
    FROM pbp_punts
    WHERE season_type = 'REG' AND yardline_100 IS NOT NULL AND kick_distance IS NOT NULL
"""


def _points_per_punt(scoring) -> str:
    """League points earned by the average punt in a group.

    The per-game gross-average bonus is deliberately absent: it is awarded once per game, not once
    per punt, so it belongs to `punt_points_per_game` below rather than to a per-punt rate.
    """
    return (
        f"{scoring.punt_pts}"
        f" + {scoring.punt_in10_pts} * AVG(in10)"
        f" + {scoring.punt_in20_pts} * AVG(in20)"
        f" + {scoring.punt_touchback_pts} * AVG(touchback)"
        f" + {scoring.punt_fair_catch_pts} * AVG(fair_catch)"
        f" + {scoring.punt_blocked_pts} * AVG(blocked)"
        f" + {scoring.punt_returned_pts} * AVG(returned)"
    )


# Games come from `schedules` rather than from counting distinct game_ids in the punt archive: a
# team that never punted in a game still played it, and dividing by punt-games instead would
# quietly inflate the per-game rates of exactly the teams this table is about.
_TEAM_GAMES = """
    SELECT team, season, COUNT(*) AS games FROM (
        SELECT home_team AS team, season FROM schedules WHERE game_type = 'REG'
        UNION ALL
        SELECT away_team, season FROM schedules WHERE game_type = 'REG'
    ) GROUP BY team, season
"""

_TEAM_SCORING = """
    SELECT team, season, AVG(points_for) AS points_for, AVG(points_against) AS points_against
    FROM (
        SELECT home_team AS team, season, home_score AS points_for, away_score AS points_against
        FROM schedules WHERE game_type = 'REG' AND home_score IS NOT NULL
        UNION ALL
        SELECT away_team, season, away_score, home_score
        FROM schedules WHERE game_type = 'REG' AND away_score IS NOT NULL
    ) GROUP BY team, season
"""


def _build_sql(scoring) -> str:
    per_punt = _points_per_punt(scoring)
    return f"""
CREATE OR REPLACE TABLE punt_environment AS
WITH punts AS ({_PUNT_FLAGS}),
games AS ({_TEAM_GAMES}),
scoring AS ({_TEAM_SCORING}),
-- The punting a team does itself: how often, from where, and what it's worth.
offense AS (
    SELECT
        team, season,
        COUNT(*) AS punts,
        AVG(own_yard) AS avg_punt_spot,
        AVG(CASE WHEN own_yard > 40 THEN 1.0 ELSE 0.0 END) AS plus_territory_share,
        AVG(CASE WHEN own_yard <= 20 THEN 1.0 ELSE 0.0 END) AS backed_up_share,
        AVG(kick_distance) AS gross_average,
        AVG(in20) AS in20_rate,
        AVG(in10) AS in10_rate,
        AVG(returned) AS returned_rate,
        {per_punt} AS points_per_punt
    FROM punts GROUP BY team, season
),
-- The punting a team's defense forces on everyone else — the matchup side.
defense AS (
    SELECT
        opponent AS team, season,
        COUNT(*) AS punts_forced,
        AVG(own_yard) AS opp_avg_punt_spot,
        {per_punt} AS opp_points_per_punt
    FROM punts GROUP BY opponent, season
)
SELECT
    o.team,
    o.season,
    g.games,
    s.points_for,
    s.points_against,
    o.punts,
    o.punts / g.games AS punts_per_game,
    o.avg_punt_spot,
    o.plus_territory_share,
    o.backed_up_share,
    o.gross_average,
    o.in20_rate,
    o.in10_rate,
    o.returned_rate,
    o.points_per_punt,
    o.punts / g.games * o.points_per_punt AS punt_points_per_game,
    d.punts_forced / g.games AS punts_forced_per_game,
    d.opp_avg_punt_spot,
    d.opp_points_per_punt,
    d.punts_forced / g.games * d.opp_points_per_punt AS punt_points_allowed_per_game
FROM offense o
JOIN defense d ON d.team = o.team AND d.season = o.season
JOIN games g ON g.team = o.team AND g.season = o.season
JOIN scoring s ON s.team = o.team AND s.season = o.season
ORDER BY o.season DESC, punt_points_per_game DESC
"""


@console.analysis
def _print_field_position_report(con: duckdb.DuckDBPyConnection, scoring) -> None:
    """The value-by-field-position curve — the reason volume alone doesn't explain a punter."""
    rows = con.sql(f"""
        WITH punts AS ({_PUNT_FLAGS})
        SELECT
            CASE WHEN own_yard <= 10 THEN 'own 1-10'
                 WHEN own_yard <= 20 THEN 'own 11-20'
                 WHEN own_yard <= 30 THEN 'own 21-30'
                 WHEN own_yard <= 40 THEN 'own 31-40'
                 WHEN own_yard <= 50 THEN 'own 41-50'
                 ELSE 'opponent side' END AS punt_from,
            MIN(own_yard) AS sort_key,
            COUNT(*) AS punts,
            AVG(kick_distance) AS gross,
            AVG(in20) AS in20_rate,
            AVG(returned) AS returned_rate,
            {_points_per_punt(scoring)} AS points_per_punt
        FROM punts GROUP BY 1 ORDER BY sort_key
    """).df()

    print("\n  what a punt is worth, by where it was struck from")
    print(f"    {'punt from':16}{'punts':>8}{'gross':>8}{'in-20':>8}{'returned':>10}"
          f"{'pts/punt':>10}")
    for _, row in rows.iterrows():
        print(f"    {row.punt_from:16}{int(row.punts):>8}{row.gross:8.1f}"
              f"{row.in20_rate:8.3f}{row.returned_rate:10.3f}{row.points_per_punt:10.2f}")


@console.analysis
def _print_volume_tension_report(con: duckdb.DuckDBPyConnection) -> None:
    """Teams that punt most punt from deeper — and still generate far more punter points."""
    rows = con.sql("""
        SELECT
            NTILE(5) OVER (ORDER BY punts_per_game) AS quintile,
            punts_per_game, avg_punt_spot, points_per_punt, punt_points_per_game, points_for
        FROM punt_environment
    """).df().groupby("quintile").mean().reset_index()

    labels = {1: "fewest punts", 2: "2", 3: "3", 4: "4", 5: "most punts"}
    print("\n  volume vs value, team-seasons in quintiles of punts per game")
    print(f"    {'':16}{'punts/g':>9}{'punt spot':>11}{'pts/punt':>10}{'punt pts/g':>12}"
          f"{'team pts/g':>12}")
    for _, row in rows.iterrows():
        print(f"    {labels[int(row.quintile)]:16}{row.punts_per_game:9.2f}"
              f"{row.avg_punt_spot:11.1f}{row.points_per_punt:10.2f}"
              f"{row.punt_points_per_game:12.2f}{row.points_for:12.1f}")


@console.analysis
def _print_matchup_report(con: duckdb.DuckDBPyConnection) -> None:
    """Which defenses are worth punting against, pooled over every season in the archive."""
    rows = con.sql("""
        SELECT team,
               SUM(punts_forced_per_game * games) / SUM(games) AS punts_forced_per_game,
               SUM(punt_points_allowed_per_game * games) / SUM(games) AS points_allowed_per_game
        FROM punt_environment GROUP BY team ORDER BY points_allowed_per_game DESC
    """).df()

    print("\n  punter points allowed per game, pooled 2015-2025")
    print(f"    {'most generous':22}{'':4}{'stingiest':22}")
    for i in range(5):
        top, bottom = rows.iloc[i], rows.iloc[-(i + 1)]
        print(f"    {top.team:>4} {top.points_allowed_per_game:6.2f}"
              f" ({top.punts_forced_per_game:.2f} punts)   "
              f"  {bottom.team:>4} {bottom.points_allowed_per_game:6.2f}"
              f" ({bottom.punts_forced_per_game:.2f} punts)")
    spread = rows.points_allowed_per_game.max() - rows.points_allowed_per_game.min()
    print(f"    spread across 32 defenses: {spread:.2f} points per game")


def build_punt_environment() -> None:
    con = duckdb.connect(str(WAREHOUSE_PATH))
    scoring = league_scoring(con)
    con.execute(_build_sql(scoring))
    (count,) = con.execute("SELECT COUNT(*) FROM punt_environment").fetchone()
    console.table("punt_environment", count)

    _print_field_position_report(con, scoring)
    _print_volume_tension_report(con)
    _print_matchup_report(con)
    con.close()


if __name__ == "__main__":
    build_punt_environment()
