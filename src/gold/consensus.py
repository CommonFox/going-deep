"""Build the consensus (median/floor/ceiling) projection tables from every projection source.

Pure warehouse-to-warehouse SQL — no fetch step, no network, just a transform over tables that
the other source modules (espn, sleeper, fftoday, cbs) and the in-house model
(inhouse_projections.py) have already loaded/built.

Two output tables, because team defenses need a different join key than individual players:
- consensus_projections: skill positions (QB/RB/WR/TE/K), joined through the nflverse `ids`
  player crosswalk (loaded by nfl_data.py) onto a common `gsis_id`. inhouse_projections is the one
  exception — it's already keyed on that same `gsis_id` (it's built from weekly_stats, which uses
  the same nflverse GSIS ID space), so it's unioned in directly with no crosswalk join.
- consensus_dst_projections: team defenses, joined on a normalized team abbreviation (see
  teams.py) instead, since `ids` is player-level and has no team-defense entries.

Both tables carry each source's own number alongside the blend (`espn_points`, `sleeper_points`,
`cbs_points`, `fftoday_points`, and `inhouse_points` on the player table), so a consensus row can
be traced back to what each site actually said and a missing source reads as a NULL column rather
than just a lower `num_sources`.

The external sources decompose a season differently from inhouse_projections, in a way that has to
be reconciled before the five numbers can be averaged at all. Verified against CBS, the one source
publishing per-game points alongside the season total: it projects 17.0 games for every player it
covers, backups included, so it never discounts for injury risk — but it does discount for *role*,
through the per-game term instead (Jake Browning: 0.9 points per game across a full 17). The other
three sites' totals behave the same way.

inhouse_projections is therefore blended in through `projected_points_full`, which reproduces that
split — role discount kept, injury discount dropped — rather than its `projected_points`, the
honest expectation including injury risk. Blending the latter would put the only availability-
discounted number in a pool of four health-neutral ones and drag every consensus percentile down on
exactly the players most likely to miss time.

inhouse_projections can lag behind the other four sources: its prior-year feature data comes from
nflverse, which publishes noticeably slower than the external sites' own current-season
projections, so its latest `target_season` isn't guaranteed to be the season the other four
sources actually represent. consensus_projections' inhouse arm is scoped to that season (derived
from the external sources themselves), not inhouse's own `MAX(target_season)` — otherwise a stale
inhouse projection would get silently blended in alongside four current ones under the same
player, dragging the consensus toward last season's number with no signal that anything was off.
"""

from pathlib import Path

import duckdb

from src import console
from src.silver.teams import normalize_team

WAREHOUSE_PATH = Path("data/warehouse.duckdb")

_SKILL_POSITIONS = ("QB", "RB", "WR", "TE", "K")

# The sources unioned into each table's `source_projections` CTE, in blend order. Team defenses
# have no in-house arm — inhouse_projections is player-level only.
_SOURCES = ("espn", "sleeper", "cbs", "fftoday", "inhouse")
_DST_SOURCES = ("espn", "sleeper", "cbs", "fftoday")


def _aggregates(sources: tuple[str, ...]) -> str:
    """Aggregate SELECT list for one consensus table: the blend, then each source on its own.

    Both tables get their column list from here so the two can't drift apart — the per-source
    columns are a pivot of the exact same `source_projections` rows the percentiles summarize,
    not a second pass over the underlying tables. MAX() is just the pivot's pick-one aggregate:
    every source contributes at most one row per key today (verified — non-null per-source columns
    add up to `num_sources` on every row of both tables), so it never actually collapses anything.
    """
    per_source = ",\n".join(
        f"    MAX(projected_points) FILTER (WHERE source = '{source}') AS {source}_points"
        for source in sources
    )
    return f"""
    COUNT(*) AS num_sources,
    MIN(projected_points) AS min_points,
    MAX(projected_points) AS max_points,
    PERCENTILE_CONT(0.2) WITHIN GROUP (ORDER BY projected_points) AS floor_p20,
    PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY projected_points) AS median_p50,
    PERCENTILE_CONT(0.8) WITHIN GROUP (ORDER BY projected_points) AS ceiling_p80,
    STDDEV(projected_points) AS stddev_points,
{per_source}
"""

# Every join also matches on position, since the `ids` crosswalk has a handful of duplicate
# external IDs among long-retired/free-agent players (verified harmless against current data —
# none of them appear in any of the active-player projection sources below — but matching on
# position too costs nothing and rules it out structurally rather than by luck). `ids` labels
# kickers "PK" where every source here uses "K", so the crosswalk is normalized to "K" first.
# Each source's expression for one scoring basis. Half-PPR is not a second opinion here, it is the
# same projection restated, and every source that can be reconciled to it exactly is: Sleeper and
# FFToday publish half-PPR directly, CBS's half-PPR page 404s but `(standard + ppr) / 2` is an
# identity at half a point per reception, and ESPN and inhouse publish PPR only, so they are
# converted with `ppr - 0.5 * rec` against Sleeper's own projected receptions (an identity too, not
# an estimate — see sleeper.load_projections).
#
# Quarterbacks and kickers take a structural zero for receptions. A pass-catcher Sleeper does not
# project has no reception count anywhere — CBS and FFToday publish points, not catches — and for
# him the count is estimated from the PPR total being converted, by the per-position fit below.
#
# That estimate replaces an earlier rule that yielded NULL instead, dropping the converted arms out
# of the half-PPR blend rather than reporting an unconverted PPR number as half-PPR. The rule was
# right about the risk and wrong about the cost, which grew: it was written when it silently removed
# 9 deep-roster players, and by 2026 it removed 357 — everyone the in-house model covers and Sleeper
# does not — including Jayden Higgins (superflex ADP 136) and Ricky Pearsall (129.5). Neither
# appeared anywhere on the half-PPR league's board, which is how a drafted player can look available:
# he was never on it to strike off. Being absent from a board is a worse error than being a point or
# two high on it, and here it is a point or two: see `reception_fit`.
_SCORINGS = ("ppr", "half_ppr")

# Receptions for the conversion: the projected count where a source publishes one, and the fitted
# estimate where none does. Clamped at zero so a near-zero projection cannot imply negative catches.
_RECEPTIONS_ADJUSTMENT = """0.5 * CASE WHEN {position} IN ('QB', 'K') THEN 0
        ELSE COALESCE(receptions.rec, GREATEST(fit.slope * {ppr_points} + fit.intercept, 0)) END"""


def _source_arm(scoring: str) -> str:
    """The `source_projections` UNION for one scoring basis, tagged with that basis."""
    if scoring == "ppr":
        espn_points = "e.projected_points"
        sleeper_points = "SUM(s.pts_ppr)"
        cbs_from = "cbs_projections c"
        cbs_where = "WHERE c.scoring = 'ppr'"
        fftoday_where = "WHERE f.scoring = 'ppr'"
        inhouse_points = "projected_points_full"
    else:
        espn_points = (
            "e.projected_points - "
            + _RECEPTIONS_ADJUSTMENT.format(
                position="ids.position", ppr_points="e.projected_points"
            )
        )
        sleeper_points = "SUM(s.pts_half_ppr)"
        cbs_from = "cbs_half c"
        cbs_where = ""
        fftoday_where = "WHERE f.scoring = 'half_ppr'"
        inhouse_points = (
            "projected_points_full - "
            + _RECEPTIONS_ADJUSTMENT.format(
                position="inhouse_projections.position", ppr_points="projected_points_full"
            )
        )

    return f"""
    SELECT ids.gsis_id, ids.name AS player_name, ids.position, 'espn' AS source,
           '{scoring}' AS scoring, {espn_points} AS projected_points
    FROM espn_projections e
    JOIN ids_normalized ids ON ids.espn_id = e.espn_id AND ids.position = e.position
    LEFT JOIN receptions ON receptions.gsis_id = ids.gsis_id
    LEFT JOIN reception_fit fit ON fit.position = ids.position

    UNION ALL

    SELECT ids.gsis_id, ids.name, ids.position, 'sleeper', '{scoring}', {sleeper_points}
    FROM sleeper_projections s
    JOIN ids_normalized ids
        ON TRY_CAST(s.sleeper_id AS DOUBLE) = ids.sleeper_id AND ids.position = s.position
    GROUP BY ids.gsis_id, ids.name, ids.position

    UNION ALL

    SELECT ids.gsis_id, ids.name, ids.position, 'cbs', '{scoring}', c.projected_points
    FROM {cbs_from}
    JOIN ids_normalized ids ON ids.cbs_id = c.cbs_id AND ids.position = c.position
    {cbs_where}

    UNION ALL

    SELECT ids.gsis_id, ids.name, ids.position, 'fftoday', '{scoring}', f.projected_points
    FROM fftoday_projections f
    JOIN ids_normalized ids ON ids.merge_name = f.merge_name AND ids.position = f.position
    {fftoday_where}

    UNION ALL

    -- No ids crosswalk join here (see module docstring) and no position-matching safety net
    -- either — that guards against ids' duplicate-external-ID problem, which doesn't apply since
    -- this source never goes through ids at all. Scoped to current_season, not inhouse's own
    -- MAX(target_season) — if inhouse hasn't caught up to the season the other sources represent,
    -- it drops out of the blend entirely rather than contributing a stale number.
    --
    -- projected_points_full, not projected_points: the external sources all publish a
    -- health-neutral full-season number (see module docstring), so blending in inhouse's
    -- availability-discounted one would be a units mismatch, not a difference of opinion.
    SELECT player_id, player_name, inhouse_projections.position, 'inhouse', '{scoring}',
           {inhouse_points}
    FROM inhouse_projections
    LEFT JOIN receptions ON receptions.gsis_id = inhouse_projections.player_id
    LEFT JOIN reception_fit fit ON fit.position = inhouse_projections.position
    WHERE target_season = (SELECT season FROM current_season)
"""


# Every join also matches on position, since the `ids` crosswalk has a handful of duplicate
# external IDs among long-retired/free-agent players (verified harmless against current data —
# none of them appear in any of the active-player projection sources below — but matching on
# position too costs nothing and rules it out structurally rather than by luck). `ids` labels
# kickers "PK" where every source here uses "K", so the crosswalk is normalized to "K" first.
_BUILD_SQL = f"""
CREATE OR REPLACE TABLE consensus_projections AS
WITH ids_normalized AS (
    SELECT gsis_id, name, espn_id, sleeper_id, cbs_id, merge_name,
           CASE WHEN position = 'PK' THEN 'K' ELSE position END AS position
    FROM ids
),
current_season AS (
    SELECT MAX(season) AS season FROM (
        SELECT MAX(season) AS season FROM espn_projections
        UNION ALL
        SELECT MAX(TRY_CAST(season AS BIGINT)) AS season FROM sleeper_projections
        UNION ALL
        SELECT MAX(season) AS season FROM cbs_projections
        UNION ALL
        SELECT MAX(season) AS season FROM fftoday_projections
    )
),
-- Sleeper's projected receptions, the conversion factor for the PPR-only sources, alongside the
-- PPR total they go with. Deliberately unfiltered by season, matching the sleeper arm's own SUM()
-- below, since the projections archive holds one season at a time.
sleeper_receptions AS (
    SELECT ids.gsis_id, ids.position, SUM(s.rec) AS rec, SUM(s.pts_ppr) AS ppr
    FROM sleeper_projections s
    JOIN ids_normalized ids
        ON TRY_CAST(s.sleeper_id AS DOUBLE) = ids.sleeper_id AND ids.position = s.position
    GROUP BY ids.gsis_id, ids.position
),
receptions AS (
    SELECT gsis_id, SUM(rec) AS rec FROM sleeper_receptions GROUP BY gsis_id
),
-- Receptions against PPR points, per position, over everyone Sleeper projects both for — the
-- estimate used for a pass-catcher Sleeper does not project at all. A season's catches are very
-- nearly a fixed share of a season's PPR points, because a reception is a point plus the yards it
-- came with: fitted on 2026, r-squared is 0.990 for tight ends, 0.987 for receivers and 0.874 for
-- backs, whose points lean on carries instead.
--
-- What that buys is small by construction. The players who need the estimate are exactly the ones
-- no external site projects, so every one of them sits under 80 PPR points, and across that range
-- the fit is worth 0.55 (TE), 0.57 (WR) and 1.30 (RB) points of half-PPR error at one standard
-- deviation. A board row is ranked on a number carrying rather more uncertainty than that.
--
-- Fitted on Sleeper's own PPR total rather than the consensus median, which is tighter (the same
-- source's two numbers agree with each other) and available before any blend exists. Applied
-- against whichever PPR total is being converted, so each arm is corrected on its own scale.
reception_fit AS (
    SELECT position, regr_slope(rec, ppr) AS slope, regr_intercept(rec, ppr) AS intercept
    FROM sleeper_receptions
    WHERE rec IS NOT NULL AND ppr > 0
    GROUP BY position
),
-- CBS publishes standard and PPR but not half-PPR; the midpoint of the two is exactly half-PPR.
cbs_half AS (
    SELECT cbs_id, position,
           (MAX(projected_points) FILTER (WHERE scoring = 'standard')
            + MAX(projected_points) FILTER (WHERE scoring = 'ppr')) / 2 AS projected_points
    FROM cbs_projections
    GROUP BY cbs_id, position
),
source_projections AS (
{"    UNION ALL".join(_source_arm(scoring) for scoring in _SCORINGS)}
)
SELECT
    gsis_id,
    scoring,
    ANY_VALUE(player_name) AS player_name,
    ANY_VALUE(position) AS position,
    {_aggregates(_SOURCES)}
FROM source_projections
WHERE gsis_id IS NOT NULL
    AND projected_points IS NOT NULL
    AND position IN {_SKILL_POSITIONS}
GROUP BY gsis_id, scoring
"""

# `team` values are normalized (via the normalize_team Python UDF registered below) before this
# runs, since each source represents defenses differently — a full name, a bare nickname, or a
# non-canonical abbreviation (e.g. "LAR" instead of this warehouse's "LA" for the Rams).
_BUILD_DST_SQL = f"""
CREATE OR REPLACE TABLE consensus_dst_projections AS
WITH source_projections AS (
    -- espn_projections.team is already normalized at load time (see espn.py:load_projections).
    SELECT team, 'espn' AS source, projected_points
    FROM espn_projections
    WHERE position = 'DST'

    UNION ALL

    SELECT normalize_team(team), 'sleeper', SUM(pts_ppr)
    FROM sleeper_projections
    WHERE position = 'DEF'
    GROUP BY team

    UNION ALL

    SELECT team, 'cbs', projected_points
    FROM cbs_projections
    WHERE position = 'DST' AND scoring = 'ppr'

    UNION ALL

    SELECT team, 'fftoday', projected_points
    FROM fftoday_projections
    WHERE position = 'DST' AND scoring = 'ppr'
)
SELECT
    team,
    {_aggregates(_DST_SOURCES)}
FROM source_projections
WHERE team IS NOT NULL AND projected_points IS NOT NULL
GROUP BY team
"""


def build_consensus_projections() -> None:
    con = duckdb.connect(str(WAREHOUSE_PATH))
    con.create_function("normalize_team", normalize_team, ["VARCHAR"], "VARCHAR")

    con.execute(_BUILD_SQL)
    con.execute(_BUILD_DST_SQL)
    (count,) = con.execute("SELECT COUNT(*) FROM consensus_projections").fetchone()
    (dst_count,) = con.execute("SELECT COUNT(*) FROM consensus_dst_projections").fetchone()
    con.close()

    console.table("consensus_projections", count)
    console.table("consensus_dst_projections", dst_count)


if __name__ == "__main__":
    build_consensus_projections()
