"""Price every draftable player for an upcoming draft, in one league's scoring and roster slots.

Pure warehouse-to-warehouse Python/SQL — no fetch step, no network. Built from
`consensus_projections`, `consensus_dst_projections`, `punter_projections`, `adp_consensus`,
`ffc_adp` and `league_settings`, all already loaded.

`points_over_replacement` answers this same question about seasons that have *happened*: it scores
real box scores and asks how far above a freely-available player someone finished. This module asks
it about a season that hasn't been played yet, off projections instead of actuals — which is a
different enough thing to deserve its own table. Keeping forecast rows out of
`points_over_replacement` matters practically, not just tidily: `boom_bust`, `player_archetypes`
and `draft_strategy` all join it expecting every row to have really happened, and a 2026 row with
no outcome would quietly corrupt all three. The replacement-level machinery itself is imported
rather than reimplemented, so the two tables can't drift apart on the one thing they share.

## Replacement level is the whole idea

A player's draft value is not his projection. It is his projection minus what the position costs
you for free — the last player who still has to start somewhere in the league, the one available
on waivers because 14 teams already have theirs. That baseline is what makes positions comparable,
and it is *not* "the player I could get with my next pick", which isn't free: spending that pick
has its own cost.

This is also the entire reason superflex changes a draft. The identical Josh Allen is worth about
+60 points in a one-quarterback league, where replacement is QB15 and perfectly startable, and
about +164 in this one, where 14 teams × 2 quarterback slots need 28 of the roughly 32 startable
NFL quarterbacks and replacement is a backup. Nothing about the player moved; the number of jobs
did. `_replacement_levels` already models exactly this — it pools leftover quarterbacks in with
RB/WR/TE whenever a league has a superflex slot — so the format arrives through `league_settings`
rather than through anything hardcoded here.

## Two point totals, because the sources don't discount for injury

Every external projection source publishes a health-neutral season: verified against CBS, the one
that shows per-game points alongside the total, they project 17.0 games for *everyone*, backups
included, and express role through the per-game term instead. So a running back's 347 assumes he
plays every week, which running backs do not.

The board therefore carries both, and ranks on the adjusted one:

- `projected_points` — the blend as the sources publish it, health-neutral.
- `projected_points_adjusted` — the same number scaled by how much of a season that player has
  historically been available for.

Availability is measured as **the share of his rostered weeks a player was not on injured
reserve**, over his last few seasons, shrunk toward his position's mean so that one unlucky year
in a short career doesn't become a verdict. Three more obvious measures were tried first and all
three are wrong here, which is worth recording so they don't get tried again:

- `inhouse_projections`' own `expected_games / season_games` ratio is nearly a flat 0.8 for
  everyone (0.746-0.951 across all full-role quarterbacks) and orders the positions *backwards*,
  scoring quarterbacks less available than running backs. It predicts games from a population
  full of players who never had a job, so it is mostly measuring role, which the projection has
  already priced.
- Raw games played conflates injury with benching, and does so worst exactly where it matters
  most: a benched quarterback plays zero snaps while a benched running back still plays some, so
  quarterbacks look like the most fragile position in the league (0.706 vs 0.787) when they are
  in fact the most durable.
- The weekly injury report can't see season-ending injuries at all — a player placed on IR stops
  appearing on it, which is why it reports an implausible 0.2 weeks "Out" per player-season.

Roster status avoids all three: `RES`/`PUP` is an injury fact recorded independently of whether
anyone chose to play the player. It is deliberately *conservative* — it catches multi-week and
season-ending absences but not the one- and two-game misses that also make running backs
unreliable, so the haircut here is a floor on the real durability gap, not an estimate of it.

`points_over_replacement` is computed on each basis against a replacement level computed on that
same basis, so the two are internally consistent and the gap between them is readable as exactly
what the durability haircut is doing to a given player.

## The offensive line, for running backs only

Each player carries his team's offensive line grade from the *prior* season — knowable at draft
time — and running backs additionally carry the quartile it falls in. Backs behind a bottom-quartile
line returned **-6.6** points of surplus against their draft price and beat that price only 35% of
the time; behind a top-quartile line, **+20.3** and 56%. A 27-point swing, p=0.0055, measured
against ADP, so it is signal the market is not already charging for.

It is deliberately a warning rather than a ranking input. The relationship is not monotonic — the
third quartile scores slightly better than the fourth — so it identifies backs to be wary of rather
than backs to chase, and the board leaves the value untouched and lets the reader apply it.

Only backs get the tier, because only backs show the effect. Against the same prior-season grade,
quarterbacks come out at r=-0.147 (positive in 2 of 7 seasons), tight ends +0.113 (1 of 5) and
receivers -0.048 (3 of 7) — all indistinguishable from nothing. Running backs are +0.194 at
p=0.0009 and positive in **7 of 7**. Extending the flag to the other three would be dressing noise
up as advice.

## What this does not model

Replacement level is treated as fixed for the season, and it isn't. Useful running backs appear on
waivers constantly — a backup inherits a job in week 3 and returns RB2 value — while useful
quarterbacks in a 14-team superflex essentially never do, because all 28 startable ones are
rostered. So the true in-season RB baseline is *higher* than RB36 suggests and the true QB baseline
is *lower* than QB29 suggests, and both errors push the same way: against running backs, in favour
of quarterbacks. Modelling it honestly needs historical waiver-add data this warehouse doesn't
archive, so it is named here rather than estimated. Read a close RB-vs-QB margin with a thumb on
the quarterback's side of the scale.
"""

from pathlib import Path

import duckdb
import pandas as pd

from src import console
from src.gold.points_over_replacement import _SKILL_POSITIONS, _build_league_season
from src.gold.sleeper_ids import report_unmapped, resolve_sleeper_ids
from src.silver.teams import normalize_team

WAREHOUSE_PATH = Path("data/warehouse.duckdb")

# Positions drafted out of a pool the whole league shares, but with no flex complications: their
# replacement level is just the last one who starts anywhere. Kickers and defenses come from the
# consensus tables; the punter is the ESPN league's own oddity and comes from `punter_projections`,
# the only source anywhere that prices one.
_FIXED_SLOT_POSITIONS = {"K": "k_slots", "DST": "dst_slots", "P": "p_slots"}

# How far back durability is measured, and how many pseudo-seasons of the position's mean a
# player's own record is shrunk toward. A player is only counted for a season in which he was a
# listed starter for at least this many weeks — durability is only meaningful for someone who had
# a job to miss.
_AVAILABILITY_SEASONS = 5
_AVAILABILITY_SHRINKAGE = 2.0
_STARTER_WEEKS_FOR_ROLE = 4

# `consensus_projections.scoring` for a league, keyed off what a reception is worth. Only the two
# bases the projection sources can actually be reconciled to are listed; a standard-scoring league
# would need a third arm in consensus.py before it could be priced here.
_SCORING_BASES = {1.0: "ppr", 0.5: "half_ppr"}

_OUTPUT_COLUMNS = [
    "league_key", "season", "format", "scoring", "player_id", "sleeper_id", "player_name",
    "position", "team",
    "ol_grade", "ol_tier",
    "projected_points", "projected_points_adjusted", "projected_floor", "projected_ceiling",
    "availability", "availability_source",
    "espn_points", "sleeper_points", "cbs_points", "fftoday_points", "inhouse_points",
    "num_sources",
    "position_rank", "starters_at_position", "replacement_level_points", "points_over_replacement",
    "replacement_level_raw", "points_over_replacement_raw",
    "consensus_adp", "adp_stdev", "adp_high", "adp_low",
]


def _scoring_basis(league: pd.Series) -> str:
    """Which `consensus_projections.scoring` rows this league should be priced from."""
    try:
        return _SCORING_BASES[round(float(league["rec_pts"]), 2)]
    except KeyError as error:
        raise ValueError(
            f"{league['league_key']} scores {league['rec_pts']} per reception, which no projection "
            f"source here is reconciled to (have: {sorted(_SCORING_BASES)})"
        ) from error


def _league_format(league: pd.Series) -> str:
    """`adp_consensus.format` for this league — which market priced the board it drafts against."""
    return "superflex" if league["superflex_slots"] else "1qb"


def _availability(con: duckdb.DuckDBPyConnection, season: int) -> pd.DataFrame:
    """Each player's share of a season historically spent off injured reserve, shrunk.

    Restricted to seasons in which the player actually held a job (a listed starter for at least
    a few weeks), because a player nobody rostered tells us nothing about durability. Shrunk
    toward the position mean by `_AVAILABILITY_SHRINKAGE` pseudo-seasons, so a rookie with one
    year on record isn't handed a career verdict and a five-year sample is mostly his own.
    """
    df = con.execute(
        f"""
        WITH had_a_job AS (
            SELECT gsis_id AS player_id, season, position
            FROM player_depth_chart
            WHERE is_starter AND season BETWEEN ? AND ?
            GROUP BY 1, 2, 3
            HAVING count(DISTINCT week) >= {_STARTER_WEEKS_FOR_ROLE}
        ),
        roster_weeks AS (
            SELECT player_id, season, count(*) AS weeks,
                   sum(CASE WHEN status IN ('RES', 'PUP') THEN 1 ELSE 0 END) AS hurt_weeks
            FROM rosters WHERE season BETWEEN ? AND ? GROUP BY 1, 2
        )
        SELECT j.player_id, j.position,
               count(*) AS seasons,
               avg(1.0 - r.hurt_weeks * 1.0 / nullif(r.weeks, 0)) AS availability
        FROM had_a_job j
        JOIN roster_weeks r ON r.player_id = j.player_id AND r.season = j.season
        GROUP BY 1, 2
        """,
        [season - _AVAILABILITY_SEASONS, season - 1] * 2,
    ).df()

    position_mean = df.groupby("position")["availability"].mean()
    prior = df["position"].map(position_mean)
    weight = df["seasons"] / (df["seasons"] + _AVAILABILITY_SHRINKAGE)
    df["availability"] = (weight * df["availability"] + (1 - weight) * prior).clip(0, 1)
    return df[["player_id", "position", "availability"]]


def _apply_availability(
    players: pd.DataFrame, availability: pd.DataFrame
) -> pd.DataFrame:
    """Attach each player's availability discount, falling back to the position median."""
    df = players.merge(availability, on=["player_id", "position"], how="left")
    position_median = availability.groupby("position")["availability"].median()

    df["availability_source"] = df["availability"].notna().map({True: "history", False: "position"})
    df["availability"] = df["availability"].fillna(df["position"].map(position_median))
    # A position the in-house model covers not at all (kickers, defenses, punters) has no discount
    # to borrow, so it is priced health-neutral and says so.
    df["availability_source"] = df["availability_source"].where(df["availability"].notna(), "none")
    df["availability"] = df["availability"].fillna(1.0)

    df["projected_points_adjusted"] = df["projected_points"] * df["availability"]
    return df


def _skill_players(con: duckdb.DuckDBPyConnection, scoring: str) -> pd.DataFrame:
    """QB/RB/WR/TE projections in one scoring basis, with each source's own number kept."""
    return con.execute(
        f"""
        SELECT gsis_id AS player_id, player_name, position,
               median_p50 AS projected_points,
               floor_p20 AS projected_floor, ceiling_p80 AS projected_ceiling,
               espn_points, sleeper_points, cbs_points, fftoday_points, inhouse_points,
               num_sources
        FROM consensus_projections
        WHERE scoring = ? AND position IN {_SKILL_POSITIONS} AND median_p50 IS NOT NULL
        """,
        [scoring],
    ).df()


def _fixed_slot_players(
    con: duckdb.DuckDBPyConnection, scoring: str, league: pd.Series, season: int
) -> pd.DataFrame:
    """Kickers, defenses and (ESPN only) punters — one shared pool, no flex, priced separately.

    Their replacement level is simply the last one who starts: with one kicker slot in a 14-team
    league, the 15th-best kicker is free, so the best one is worth what he beats *him* by. That is
    a much smaller number than his projection, which is the point — it is what stops a board from
    recommending a kicker in round nine.
    """
    frames = []

    kickers = con.execute(
        """
        SELECT gsis_id AS player_id, player_name, 'K' AS position,
               median_p50 AS projected_points,
               floor_p20 AS projected_floor, ceiling_p80 AS projected_ceiling,
               espn_points, sleeper_points, cbs_points, fftoday_points, inhouse_points, num_sources
        FROM consensus_projections
        WHERE scoring = ? AND position = 'K' AND median_p50 IS NOT NULL
        """,
        [scoring],
    ).df()
    frames.append(kickers)

    defenses = con.execute(
        """
        SELECT team AS player_id, team AS player_name, 'DST' AS position,
               median_p50 AS projected_points,
               floor_p20 AS projected_floor, ceiling_p80 AS projected_ceiling,
               espn_points, sleeper_points, cbs_points, fftoday_points,
               CAST(NULL AS DOUBLE) AS inhouse_points, num_sources
        FROM consensus_dst_projections
        WHERE median_p50 IS NOT NULL
        """
    ).df()
    frames.append(defenses)

    if league["p_slots"]:
        punters = con.execute(
            """
            SELECT player_id, player_name, 'P' AS position,
                   projected_points_espn AS projected_points,
                   CAST(NULL AS DOUBLE) AS projected_floor,
                   CAST(NULL AS DOUBLE) AS projected_ceiling,
                   CAST(NULL AS DOUBLE) AS espn_points, CAST(NULL AS DOUBLE) AS sleeper_points,
                   CAST(NULL AS DOUBLE) AS cbs_points, CAST(NULL AS DOUBLE) AS fftoday_points,
                   CAST(NULL AS DOUBLE) AS inhouse_points, 1 AS num_sources
            FROM punter_projections
            WHERE target_season = ? AND projected_points_espn IS NOT NULL
            """,
            [season],
        ).df()
        frames.append(punters)

    return pd.concat(frames, ignore_index=True)


def _fixed_slot_replacement(df: pd.DataFrame, league: pd.Series, points_column: str) -> pd.DataFrame:
    """Rank and price the fixed-slot positions against the last player who starts at each."""
    priced = []
    for position, slot_column in _FIXED_SLOT_POSITIONS.items():
        pool = df[df["position"] == position].sort_values(points_column, ascending=False)
        if pool.empty:
            continue
        starters = int(league["team_count"] * league[slot_column])
        pool = pool.reset_index(drop=True)
        cutoff = min(max(starters, 0), len(pool) - 1)
        pool["position_rank"] = pool.index + 1
        pool["starters_at_position"] = starters
        pool["replacement_level_points"] = pool.loc[cutoff, points_column]
        pool["points_over_replacement"] = pool[points_column] - pool["replacement_level_points"]
        priced.append(pool)
    return pd.concat(priced, ignore_index=True) if priced else df.iloc[:0]


def _price(df: pd.DataFrame, league: pd.Series, points_column: str) -> pd.DataFrame:
    """Rank and price every position on one points basis, skill and fixed-slot alike."""
    skill = df[df["position"].isin(_SKILL_POSITIONS)].copy()
    skill = skill.rename(columns={points_column: "league_points"})
    skill = _build_league_season(skill, league)
    skill = skill.rename(columns={"league_points": points_column})

    fixed = _fixed_slot_replacement(df, league, points_column)
    return pd.concat([skill, fixed], ignore_index=True)


def _adp(con: duckdb.DuckDBPyConnection, season: int, adp_format: str) -> pd.DataFrame:
    """The market's price for this league's format — players by id, defenses by team."""
    players = con.execute(
        """
        SELECT gsis_id AS player_id, consensus_adp, adp_stdev, adp_high, adp_low
        FROM adp_consensus
        WHERE season = ? AND format = ?
        """,
        [season, adp_format],
    ).df()

    # Defenses never enter the player crosswalk (see consensus.py's DST split), so their ADP is
    # resolved on the same normalized team abbreviation the projection side uses.
    defenses = con.execute(
        """
        SELECT normalize_team(team) AS player_id, adp AS consensus_adp,
               stdev AS adp_stdev, high AS adp_high, low AS adp_low
        FROM ffc_adp
        WHERE season = ? AND scoring_format = ? AND position = 'DEF'
        """,
        [season, "2qb" if adp_format == "superflex" else "ppr"],
    ).df()

    return pd.concat([players, defenses], ignore_index=True)


def _sleeper_crosswalk(con: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    """nflverse's player crosswalk, cut to the Sleeper mapping — see `sleeper_ids` for why this
    and not Sleeper's own `gsis_id` field, which is null for most of the board."""
    return con.execute(
        "SELECT gsis_id, sleeper_id FROM ids WHERE gsis_id IS NOT NULL AND sleeper_id IS NOT NULL"
    ).df()


def _sleeper_defenses(con: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    """Sleeper's own DEF rows, which key a defense on its team rather than on any player ID."""
    return con.execute("SELECT player_id FROM sleeper_players WHERE position = 'DEF'").df()


def _team_context(con: duckdb.DuckDBPyConnection, season: int) -> pd.DataFrame:
    """Each player's team and the line he runs behind, graded before the season starts.

    The grade is the prior season's, which is the only one that exists on draft day. `ol_tier` is
    left NULL for everyone but running backs — see the module docstring for why the other three
    positions don't get one.
    """
    df = con.execute(
        """
        WITH rostered AS (
            SELECT player_id, mode(team) AS team FROM rosters WHERE season = ? GROUP BY player_id
        ),
        -- Cut on the grade's own value, not on row position: ntile() would split the four teams
        -- that share a grade of exactly 66.1 across two different tiers depending on row order,
        -- which is precisely the kind of arbitrary boundary a draft-night warning must not have.
        cuts AS (
            SELECT quantile_cont(ol_grade, 0.25) AS q1,
                   quantile_cont(ol_grade, 0.50) AS q2,
                   quantile_cont(ol_grade, 0.75) AS q3
            FROM offensive_line_grades WHERE season = ?
        ),
        line AS (
            SELECT team, ol_grade,
                   CASE WHEN ol_grade <= (SELECT q1 FROM cuts) THEN 1
                        WHEN ol_grade <= (SELECT q2 FROM cuts) THEN 2
                        WHEN ol_grade <= (SELECT q3 FROM cuts) THEN 3
                        ELSE 4 END AS tier
            FROM offensive_line_grades WHERE season = ?
        )
        SELECT r.player_id, r.team, line.ol_grade, line.tier
        FROM rostered r LEFT JOIN line ON line.team = r.team
        """,
        [season, season - 1, season - 1],
    ).df()
    labels = {1: "Q1 worst", 2: "Q2", 3: "Q3", 4: "Q4 best"}
    df["ol_tier"] = df["tier"].map(labels)
    return df[["player_id", "team", "ol_grade", "ol_tier"]]


def _build_league(con: duckdb.DuckDBPyConnection, league: pd.Series, season: int) -> pd.DataFrame:
    scoring = _scoring_basis(league)
    adp_format = _league_format(league)

    players = pd.concat(
        [_skill_players(con, scoring), _fixed_slot_players(con, scoring, league, season)],
        ignore_index=True,
    )
    players = _apply_availability(players, _availability(con, season))

    # Priced twice on purpose: once on the health-neutral number the sources publish and once on
    # the durability-adjusted one, each against a replacement level drawn from the same basis. The
    # board ranks on the adjusted figure; the raw pair stays visible so the size of the haircut on
    # any given player is readable rather than buried.
    adjusted = _price(players, league, "projected_points_adjusted")
    raw = _price(players, league, "projected_points")[
        ["player_id", "position", "replacement_level_points", "points_over_replacement"]
    ].rename(
        columns={
            "replacement_level_points": "replacement_level_raw",
            "points_over_replacement": "points_over_replacement_raw",
        }
    )

    df = adjusted.merge(raw, on=["player_id", "position"], how="left")
    df = df.merge(_adp(con, season, adp_format), on="player_id", how="left")
    df = df.merge(_team_context(con, season), on="player_id", how="left")
    df["ol_tier"] = df["ol_tier"].where(df["position"] == "RB")
    # Defenses are keyed on their own team abbreviation, so they name themselves.
    df["team"] = df["team"].fillna(df["player_id"].where(df["position"] == "DST"))
    df["league_key"] = league["league_key"]
    df["season"] = season
    df["format"] = adp_format
    df["scoring"] = scoring

    # Resolved per league, not once for the whole board: a player holds one row per league, so a
    # board-wide uniqueness check would report every player as colliding with himself.
    df = resolve_sleeper_ids(df, _sleeper_crosswalk(con), _sleeper_defenses(con))
    report_unmapped(df, league["league_key"])

    return df[_OUTPUT_COLUMNS].sort_values("points_over_replacement", ascending=False)


def build_draft_board() -> None:
    con = duckdb.connect(str(WAREHOUSE_PATH))
    con.create_function("normalize_team", normalize_team, ["VARCHAR"], "VARCHAR")

    leagues = con.execute("SELECT * FROM league_settings").df()
    (season,) = con.execute("SELECT MAX(season) FROM adp_consensus").fetchone()

    board = pd.concat(
        [_build_league(con, league, season) for _, league in leagues.iterrows()],
        ignore_index=True,
    )
    con.execute("CREATE OR REPLACE TABLE draft_board AS SELECT * FROM board")
    (count,) = con.execute("SELECT COUNT(*) FROM draft_board").fetchone()
    con.close()

    console.table("draft_board", count)


if __name__ == "__main__":
    build_draft_board()
