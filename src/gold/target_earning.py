"""Project WR target share from what causes it, not from what it was — issue #67.

Pure warehouse-to-warehouse Python/SQL — no fetch step, no network. Built from `weekly_stats`,
`ngs_data` and `player_depth_chart` (all loaded by nfl_data.py), following the `src/gold/` fetch-free
pattern.

## Why not just carry the raw share forward

A widely-shared draft heuristic (9+ targets/game, 16 games played -> 88% chance of a top-12 finish)
replicates against `weekly_stats` but is near-tautological: 9 targets/game over a full season is
already ~150 targets, which *is* a WR1 workload, so the stat mostly restates "a player who already
had WR1 volume kept it." It also barely survives being pointed forward — only 36-45% of qualifying
seasons repeat, against a 26% baseline for any 100+ target WR. This table replaces "carry the raw
number forward" with a model of what produces next year's share: how much of the passing offense a
player commanded last year (`target_share_prior`, `air_yards_share_prior`), and whether this year's
depth chart still gives him the role to sustain it (`depth_role`).

## The blend: target share first, air yards share second

`target_share_prior` and `air_yards_share_prior` are both shares of the same passing offense, so
averaging them is a fair comparison. They are not weighted evenly, though: checked against next
season's actual target share (WR seasons with >=6 games both sides, 2016-2025, n=942),
`target_share_prior` alone correlates at Spearman 0.756; blending in `air_yards_share_prior` at any
weight *reduces* that correlation (0.719 at 0.7/0.3, 0.653 at an even split) because a receiver's
share of the *ball* is a more direct measurement of what is being projected than his share of the
*air*, which also reflects a passing game's depth of target and can move independently of who ends
up getting thrown to. `air_yards_share_prior` is kept as a secondary term anyway, at a fixed 0.7/0.3
weight, because it is the one component here that reflects *quarterback trust* rather than usage
that has already happened — the ticket's stated reason for wanting a caused-by model rather than a
repeated one — and it is the only fallback a player has when `target_share_prior` itself is missing
(a rookie season, or a season below the games floor). Either input can stand in alone when the other
is missing; the blend only requires both when both exist.

## The role ceiling: this year's depth chart, not last year's

A blended share computed purely from history says nothing about whether a player still has the job.
`depth_role` (this year's week-1 `is_starter`, the one column `player_depth_chart` defines the same
way across its 2015-2024 legacy format and its 2025+ snapshot format — see that module's docstring)
gates the blend: a non-starter's projected share is capped at `_BACKUP_SHARE_CEILING`, whatever his
history says, and a starter's is never capped. The ceiling is not an arbitrary trim — checked against
every WR-season where the following year's week-1 depth chart lists a *non-starter* (n=584), the
90th percentile of what those players actually carried forward as their prior-season target share is
0.147 (median 0.073, vs. a starter cohort median of 0.197); 0.15 sits just above that, wide enough
to not clip an ordinary rotational share but firm enough to correct a demoted player's stale
starter-level number. Missing `depth_role` (no week-1 row for the player at all) is treated as a
non-starter for the same reason a punt on `is_starter` would be dishonest: an unconfirmed role is not
evidence of a starting job.

## What this table does *not* claim

Checked against `draft_value.surplus_centered` (WR, `sleeper` league, seasons with a completed
draft-value row) rather than raw finish, per the ticket's own framing — `projected_target_share`
under every blend weight tried here correlates with `surplus_centered` at Spearman magnitude under
0.03. That is not a bug in the blend: `surplus_centered` measures beating *draft price*, and ADP
already prices in a well-known player's expected role, so a role/share signal on its own explains
little of who outperforms their cost — the same "raw volume components added little on their own"
finding the backtest behind this ticket already reported. `projected_target_share` is a description
of expected opportunity, not a value signal; a future model would need to combine it with price
(ADP) to say anything about surplus.

## Scope

WR only, matching the ticket. `team_pass_volume` (team pass attempts, from `weekly_stats` QB
`attempts`) is carried alongside the blend as team-offense context rather than folded into the share
math: a team's total pass volume does not change a receiver's *share* of it by construction, so
bending the blend around it would be modeling a variable that is already normalized out. It is keyed
to the *prior* season for the player's depth-chart team this year, since the season being projected
has no team-level attempts yet.
"""

from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

from src import console

WAREHOUSE_PATH = Path("data/warehouse.duckdb")

# A season-level share needs more than a cameo game behind it to be trusted — the same floor
# player_baselines.py uses for its own per-game share averages.
_MIN_GAMES_FOR_SHARE = 6

# target_share_prior's weight in the blend; air_yards_share_prior gets the rest. See the module
# docstring for the correlation check this is grounded in.
_TARGET_SHARE_WEIGHT = 0.7

# A non-starter's projected share is capped here regardless of history. Set just above the 90th
# percentile of what actual non-starters carried forward as their prior-season target share — see
# the module docstring for the numbers.
_BACKUP_SHARE_CEILING = 0.15

_PRIOR_SHARES_SQL = f"""
SELECT player_id, season, AVG(target_share) AS target_share_prior
FROM weekly_stats
WHERE position = 'WR' AND season_type = 'REG' AND target_share IS NOT NULL
GROUP BY player_id, season
HAVING COUNT(*) >= {_MIN_GAMES_FOR_SHARE}
"""

# nflverse reports this as a 0-100 percentage; divided here so it sits on the same 0-1 scale as
# target_share before the two are ever blended. Same games floor as target_share_prior: without it,
# an injury-shortened prior season's noisy few-game average becomes the *entire* blend input the
# moment target_share_prior is floored out from under it — caught concretely on a 2025 Malik Nabers
# stint of 4 games, whose small-sample air-yards share (0.63) would otherwise have been carried
# forward whole as his 2026 projection.
_AIR_YARDS_SHARE_SQL = f"""
SELECT
    player_gsis_id AS player_id,
    season,
    AVG(percent_share_of_intended_air_yards) / 100 AS air_yards_share_prior
FROM ngs_data
WHERE stat_type = 'receiving' AND season_type = 'REG' AND player_position = 'WR'
GROUP BY player_gsis_id, season
HAVING COUNT(*) >= {_MIN_GAMES_FOR_SHARE}
"""

_TEAM_PASS_VOLUME_SQL = """
SELECT team, season, SUM(attempts) AS team_pass_volume
FROM weekly_stats
WHERE position = 'QB' AND season_type = 'REG' AND attempts IS NOT NULL
GROUP BY team, season
"""

_WEEK1_DEPTH_ROLE_SQL = """
SELECT gsis_id AS player_id, player_name, season AS target_season, team, is_starter AS depth_role
FROM player_depth_chart
WHERE week = 1 AND position = 'WR'
"""

_SCORING_SQL = """
SELECT player_id, season, surplus_centered
FROM draft_value
WHERE position = 'WR' AND league_key = 'sleeper' AND surplus_centered IS NOT NULL
"""

_OUTPUT_COLUMNS = [
    "player_id", "player_name", "season", "team", "target_share_prior", "air_yards_share_prior",
    "depth_role", "team_pass_volume", "projected_target_share",
]


def _blend_prior_shares(
    target_share_prior: pd.Series, air_yards_share_prior: pd.Series
) -> pd.Series:
    """Weighted average of the two prior-season shares, falling back to whichever one exists when
    only one does — see the module docstring for why target share carries more weight and why
    averaging the two is a fair comparison in the first place."""
    has_target = target_share_prior.notna()
    has_air = air_yards_share_prior.notna()

    blended = pd.Series(np.nan, index=target_share_prior.index, dtype=float)
    both = has_target & has_air
    blended[both] = (
        _TARGET_SHARE_WEIGHT * target_share_prior[both]
        + (1 - _TARGET_SHARE_WEIGHT) * air_yards_share_prior[both]
    )
    blended[has_target & ~has_air] = target_share_prior[has_target & ~has_air]
    blended[~has_target & has_air] = air_yards_share_prior[~has_target & has_air]
    return blended


def _apply_role_ceiling(blended_share: pd.Series, depth_role: pd.Series) -> pd.Series:
    """Cap a non-starter's blended share at `_BACKUP_SHARE_CEILING`; a confirmed starter's is never
    capped. A missing `depth_role` (no week-1 row at all) is treated as a non-starter rather than
    left uncapped, since an unconfirmed role is not evidence of a starting job."""
    confirmed_starter = depth_role.fillna(False).astype(bool)
    return blended_share.where(confirmed_starter, blended_share.clip(upper=_BACKUP_SHARE_CEILING))


def project_target_share(frame: pd.DataFrame) -> pd.Series:
    """`frame` needs `target_share_prior`, `air_yards_share_prior` and `depth_role` columns. Blends
    the two prior shares, then gates the result by this year's depth-chart role."""
    blended = _blend_prior_shares(frame["target_share_prior"], frame["air_yards_share_prior"])
    return _apply_role_ceiling(blended, frame["depth_role"])


@console.analysis
def _report(con: duckdb.DuckDBPyConnection, result: pd.DataFrame) -> None:
    scored = result.merge(con.execute(_SCORING_SQL).df(), on=["player_id", "season"], how="inner")

    print(f"\nprojected_target_share coverage: {result['projected_target_share'].notna().sum()} "
          f"of {len(result)} rows ({result['depth_role'].mean() * 100:.1f}% confirmed starters)")

    if len(scored) < 30:
        return
    # Per the module docstring: this is a deliberately weak result, checked against the ticket's
    # own acceptance criterion rather than against raw target share.
    rho = scored["projected_target_share"].corr(scored["surplus_centered"], method="spearman")
    print(f"Spearman(projected_target_share, draft_value.surplus_centered) = {rho:.3f} "
          f"(n={len(scored)}, WR/sleeper) -- a description of opportunity, not of value; see the "
          f"module docstring")


def build_target_earning() -> None:
    con = duckdb.connect(str(WAREHOUSE_PATH))
    prior_shares = con.execute(_PRIOR_SHARES_SQL).df()
    air_yards_share = con.execute(_AIR_YARDS_SHARE_SQL).df()
    team_pass_volume = con.execute(_TEAM_PASS_VOLUME_SQL).df()
    depth_role = con.execute(_WEEK1_DEPTH_ROLE_SQL).df()

    prior_shares = prior_shares.rename(columns={"season": "target_season"})
    prior_shares["target_season"] += 1
    air_yards_share = air_yards_share.rename(columns={"season": "target_season"})
    air_yards_share["target_season"] += 1

    result = depth_role.merge(
        prior_shares[["player_id", "target_season", "target_share_prior"]],
        on=["player_id", "target_season"], how="left",
    ).merge(
        air_yards_share[["player_id", "target_season", "air_yards_share_prior"]],
        on=["player_id", "target_season"], how="left",
    )

    # team_pass_volume is keyed by the prior season for the player's depth-chart team this year —
    # the season being projected has no team-level attempts yet.
    result["prior_season"] = result["target_season"] - 1
    result = result.merge(
        team_pass_volume.rename(columns={"season": "prior_season"}),
        on=["team", "prior_season"], how="left",
    )

    result["projected_target_share"] = project_target_share(result)
    result = result.rename(columns={"target_season": "season"})[_OUTPUT_COLUMNS]

    _report(con, result)
    con.close()

    con = duckdb.connect(str(WAREHOUSE_PATH))
    con.execute("CREATE OR REPLACE TABLE target_earning AS SELECT * FROM result")
    (count,) = con.execute("SELECT COUNT(*) FROM target_earning").fetchone()
    con.close()

    console.table("target_earning", count)


if __name__ == "__main__":
    build_target_earning()
