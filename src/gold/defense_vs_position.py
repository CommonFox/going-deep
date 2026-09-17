"""Build `defense_vs_position`: how a defense has actually performed against a position, as of a
given week — issue #120, under the weekly player context epic (#108).

Pure warehouse-to-warehouse Python/SQL — no fetch step, no network. Built from `weekly_stats`
(already loaded by nfl_data.py) and `league_settings` (already built by league_settings.py). One
row per (league_key, season, week, defense_team, position): the points that position group actually
scored against that defense, per game, as of that week — never including the game the row itself
describes.

Reuses `league_scoring`'s `league_points`, the same stat-to-points arithmetic `points_over_
replacement.py` and `ros_points.py` already share, rather than a second scoring path. `weekly_stats`
rows are already per-game (not season-cumulative), so `league_points` scores each player's game
directly; grouping those by (season, week, opponent_team, position) and summing gives exactly what
that position group put up against that defense in that one game.

## Scoped to skill positions, not every slot a league starts

The obvious reading of "every position the league starts" would reach for `league_settings`' K,
P and DST slots too. None of the three is a fit:

- **K**: `league_settings` carries no scoring coefficients for kicking at all (`league_points`
  only ever multiplies pass/rush/rec/fumble stats), so a kicker's row would always be a fabricated
  zero rather than an absence.
- **DST**: a team-level fact with no `weekly_stats` rows to aggregate — there is no "player" on
  defense here to sum.
- **P**: `weekly_stats` does carry punter rows, and ESPN starts one (`p_slots`), but scoring it
  needs `punters.py`'s own punt-specific coefficients (`league_settings`' `punt_*` columns), a
  different scoring path from `league_scoring.league_points` — exactly the "second scoring path"
  this table's own design point says to avoid building. It's also a different question: `punters.py`
  finds punt volume "a property of a bad offense, not of the man kicking" — a punter's production is
  driven by his own team's field position and possession patterns, not by the competence of whichever
  defense he's punting away from, so "defense vs. punter" doesn't ask the same thing "defense vs.
  position" asks for the four skill positions.

`points_over_replacement.py` and `ros_points.py` hit the K/DST wall and scope to skill positions for
the same reason. Both current leagues start all four (`qb_slots`, `rb_slots`, `wr_slots`, `te_slots`
are each >= 1 for both), so within what's computable, every position either league starts does get a
row.

## As-of-week semantics: walk the actual games, not the calendar weeks

`_walk_forward` shifts each defense/position's own per-game series by one before averaging, the same
technique `weekly_backtest._walk_forward_baselines` uses for a player's season-to-date PPG. Grouping
by `season` alongside `defense_team`/`position` makes it reset at a new season rather than carrying
last year's history into week 1 — a defense's roster and scheme aren't the same team eight months
later. A bye week needs no special handling: it simply produces no row for that team that week, so
the shift walks over it to the team's actual next game, not an empty calendar slot.

A defense/position's first game of a season has no prior game to report a fact from — `games_
observed` is 0 and every walk-forward figure is null — but that row still ships, rather than being
dropped, because the row for a game that was actually played and a bye week must stay
distinguishable: a bye week produces no row at all (there's no game to attach a fact to), and
silently dropping the season-opening row too would make the two indistinguishable to a consumer
doing a plain join. The ticket's own instruction is to expose the count and "leave the shrinkage to
#114 to justify" — a table that already discarded the zero-evidence rows would have made that call
for it.

## Rank direction, made explicit because either reading is defensible

`rank` 1 is the defense that has allowed the *most* points to a position — the softest matchup, the
one a matchup-conscious lineup call wants to know about — not the stingiest. `vs_league_avg_ratio`
and `vs_league_avg_zscore` follow the same sign: above 1.0 / positive means this defense has allowed
more than the league's own average to this position, as of the same week. All three, plus `rank`
itself, are null wherever `points_allowed_per_game_season_to_date` is null (no prior game, e.g. every
defense/position's season opener) — a group with fewer than two defenses holding a real figure that
week also nulls `vs_league_avg_zscore` on its own (`std` of one value is undefined), which is the
correct "no comparison exists yet" answer, not a bug to guard against.

## Left out on purpose

Adjusting for the strength of the offenses a defense has actually faced, and any shrinkage of the
thin early-season rows toward a league mean, are both explicitly out of scope per the ticket: they
are modelling decisions that should follow #114 establishing the raw signal has value at all, not
be baked into the table it would measure.
"""

from pathlib import Path

import duckdb
import pandas as pd

from src import console
from src.gold.league_scoring import STAT_COLUMNS, league_points

WAREHOUSE_PATH = Path("data/warehouse.duckdb")

_SKILL_POSITIONS = ("QB", "RB", "WR", "TE")

# Recency windows kept as separate columns rather than blended into the season-to-date figure — a
# defense that lost a starting corner three weeks ago is a different defense than its full-season
# average says it is.
_RECENCY_WINDOWS = {"last3": 3, "last5": 5}

_OUTPUT_COLUMNS = [
    "league_key", "season", "week", "defense_team", "position",
    "points_allowed_per_game_season_to_date", "points_allowed_per_game_last3",
    "points_allowed_per_game_last5", "games_observed",
    "league_avg_points_allowed", "vs_league_avg_ratio", "vs_league_avg_zscore", "rank",
]


def _points_allowed_by_game(stats: pd.DataFrame, league: pd.Series) -> pd.DataFrame:
    """One row per (season, week, defense_team, position): the total league points that position
    group scored against that defense in that week's game(s).

    `stats` is raw `weekly_stats` rows (one row per player per game) restricted to the columns
    `league_points` needs; each row is scored as its own single-game total before being summed
    across every player who shares a (season, week, opponent, position), which is what makes the
    result "points allowed in that one game", not a season aggregate.
    """
    scored = stats.copy()
    scored["points_allowed"] = league_points(scored, league)
    return (
        scored.groupby(["season", "week", "opponent_team", "position"], as_index=False)
        ["points_allowed"].sum()
        .rename(columns={"opponent_team": "defense_team"})
    )


def _walk_forward(by_game: pd.DataFrame) -> pd.DataFrame:
    """Attach as-of-week figures to each (defense_team, position)'s per-game series.

    The week-N row uses only games strictly before week N in the same season — `shift(1)` moves
    the current game out of its own window before `expanding`/`rolling` sees it, so nothing here
    can leak into a backtest that reads this table. `season` is part of the group key, so a
    defense/position's figures reset at a new season instead of carrying the prior year's games
    into its week 1.
    """
    out = by_game.sort_values(["defense_team", "position", "season", "week"]).reset_index(drop=True)
    points = out.groupby(["defense_team", "position", "season"])["points_allowed"]
    out["games_observed"] = points.transform(lambda p: p.shift(1).expanding().count())
    out["points_allowed_per_game_season_to_date"] = points.transform(
        lambda p: p.shift(1).expanding().mean()
    )
    for name, window in _RECENCY_WINDOWS.items():
        out[f"points_allowed_per_game_{name}"] = points.transform(
            lambda p, window=window: p.shift(1).rolling(window).mean()
        )
    return out


def _relative_to_league(df: pd.DataFrame) -> pd.DataFrame:
    """Rank each defense against every other defense facing that position the same week, on the
    same as-of-week season-to-date figure carried on the row itself — the comparison point has to
    be built from the same strictly-prior-weeks information, or the "league average" would leak
    future weeks into an early-season row on its own.
    """
    out = df.copy()
    grouped = out.groupby(["league_key", "season", "week", "position"])[
        "points_allowed_per_game_season_to_date"
    ]
    out["league_avg_points_allowed"] = grouped.transform("mean")
    league_std = grouped.transform("std")
    out["vs_league_avg_ratio"] = (
        out["points_allowed_per_game_season_to_date"] / out["league_avg_points_allowed"]
    )
    out["vs_league_avg_zscore"] = (
        out["points_allowed_per_game_season_to_date"] - out["league_avg_points_allowed"]
    ) / league_std
    out["rank"] = grouped.transform(lambda s: s.rank(ascending=False, method="min"))
    return out


def build_defense_vs_position() -> None:
    con = duckdb.connect(str(WAREHOUSE_PATH))
    stats = con.sql(f"""
        SELECT season, week, opponent_team, position, {", ".join(STAT_COLUMNS)}
        FROM weekly_stats
        WHERE season_type = 'REG' AND position IN {_SKILL_POSITIONS}
    """).df()
    leagues = con.sql("SELECT * FROM league_settings").df()

    frames = []
    for _, league in leagues.iterrows():
        walked = _walk_forward(_points_allowed_by_game(stats, league))
        walked["league_key"] = league["league_key"]
        frames.append(walked)

    result = _relative_to_league(pd.concat(frames, ignore_index=True))[_OUTPUT_COLUMNS]

    con.execute("CREATE OR REPLACE TABLE defense_vs_position AS SELECT * FROM result")
    (count,) = con.execute("SELECT COUNT(*) FROM defense_vs_position").fetchone()
    con.close()

    console.table("defense_vs_position", count)


if __name__ == "__main__":
    build_defense_vs_position()
