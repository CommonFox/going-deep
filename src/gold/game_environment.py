"""Describe the game each team is playing: implied scoring environment, gamescript lean, and
kickoff conditions — the weekly context layer (#108) wants this sitting next to a projection.

Pure warehouse-to-warehouse Python/SQL — no fetch step, no network. Built entirely from
`schedules` (loaded by nfl_data.py), which already carries every betting-line and weather column
needed; this table adds no new source, only reshapes and derives.

One row per (season, week, team) rather than per game, so a player row joins onto it directly
without the consumer working out which side of `schedules` he is on.

## `spread_line`'s sign convention, checked rather than assumed

nflverse's docs say `spread_line` is home-relative but not which sign favours the home team, and
getting it backwards produces numbers that look entirely reasonable and are exactly wrong. Checked
against `home_moneyline`, an independent number from the same books (2020-2025 regular season,
`corr(spread_line, home_moneyline) = -0.95`): a deeply negative moneyline (a big home favourite)
pairs with a large *positive* `spread_line`. So **positive `spread_line` means the home team is
favoured** by that many points; negative means the home team is the underdog.

Hand-verified against three 2024 games, two with the away team favoured:

    game                       spread_line   actual score
    BAL (home) vs CLE (away)    +19.5        BAL 35 - CLE 10   (home favourite, blowout home win)
    NYG (home) vs BAL (away)    -16.5        NYG 14 - BAL 35   (away favourite, blowout away win)
    LV  (home) vs KC  (away)     -9.0        LV  20 - KC  27   (away favourite, away win by 7)

`spread_line` itself is left on the table exactly as `schedules` carries it — home-relative — so it
stays traceable back to the source. `implied_margin` below is the derived, team-perspective number
(the home row gets `spread_line` as-is; the away row gets its negation).

## Coverage: lines arrive as the week approaches, not at schedule release

`spread_line`/`total_line` are null for a season/week that betting markets haven't priced yet
(checked live against 2026: week 3 partially populated, week 4 onward entirely null), while
`gameday`/`gametime` are populated the moment the schedule is released. Every column derived from
the line (`implied_team_total`, `implied_margin`, `is_favorite`, `gamescript_lean`) stays null
rather than being coerced to zero for those rows — a team with no line yet is not the same as a
team priced as a pick'em, and collapsing the two would read as "even game" instead of "no signal
yet". `temp`/`wind` are null on the same principle: a dome has no wind reading, which is a fact
about the stadium, not a missing measurement — `punt_environment.py` documents the identical case.

Every game type in `schedules` is included, not just `REG` — the coverage this ticket was written
against (285 games in 2024 and 2025) is `REG` plus three rounds of playoffs, and there's no reason
a playoff-eligible fantasy week should have no game_environment row.

## `gamescript_lean` is a named bucket of the spread, not a model

"A team expected to trail throws more" is the intuition; this only names where `implied_margin`
sits, the way a broadcast graphic would. Whether the bucket actually predicts anything week to week
was #114's job, not this table's — this stays a documented transformation of the spread. #133
answers the predictive question below; nothing here changed as a result.

## Verdict (#133): display-only

Run through `weekly_backtest.score_signal` against 2015-2025 (`notebooks/game_environment.ipynb`),
holding fixed a player's own walk-forward season-to-date and last-3 PPG, for the four measurable
positions (QB/RB/WR/TE — `league_points` has no kicking coefficients, so K is not computable here
any more than it is in `defense_vs_position.py`). Sample sizes run from n = 3,904 (QB, wind, a dome
game has no wind reading) to n = 22,800 (WR, every full-coverage signal), clustered by 173-181
distinct season-weeks depending on the signal's own null rate.

**Implied margin / `gamescript_lean`** carries the strongest and most robust signal: RB (incremental
rho 0.032, n = 14,346 / 181 weeks against season-to-date; 0.024 against last-3, both p < 0.01) and TE
(0.021, n = 11,236 / 181 weeks; 0.023 against last-3, both p < 0.04) hold up against both baselines;
QB and WR are flat and insignificant on both. That confirms only *half* the folk "RB on favorites, WR
on underdogs" model — the RB-favorite half holds, the WR-underdog half does not (WR incremental rho
-0.006, p = 0.33). Bucketing to `gamescript_lean` instead of the continuous `implied_margin` loses
essentially nothing (RB 0.035, TE 0.018) except a little of TE's significance (p = 0.066 vs. 0.028)
— the named bucket is a fine proxy for the number it's built from.

**`implied_team_total`** and **`temp`** are both weaker and baseline-fragile, not real findings.
`implied_team_total`: RB (0.024) and WR (-0.014, the *opposite* sign from what "more offense helps
everyone" would predict) both clear p < 0.05 against season-to-date PPG, but neither survives against
last-3 PPG (RB p = 0.15, WR p = 0.37). `temp`: QB looks promising at first against season-to-date
(p = 0.08) but flips to significant on last-3 in one league (ESPN p = 0.043) and not the other
(Sleeper p = 0.082); RB/TE/WR show nothing on either baseline. Both read as unconfirmed rather than
real — a finding that depends on which walk-forward baseline holds it fixed, or on which league's
scoring is used, is exactly the kind of thing `draft_strategy.py`'s slope check and
`player_archetypes.py`'s gate exist to catch.

**`wind`** matters for QB (-0.057 / -0.057, both baselines, both p < 0.007) and WR (-0.022 / -0.025,
both baselines, both p < 0.02) — RB and TE show no effect either way. `is_sheltered` (a closed roof
or dome, which blocks wind and precipitation the same way) finds the mirror image for WR: +0.021
against season-to-date (p = 0.002), +0.027 against last-3 (p = 0.0002), both significant; QB carries
the same sign but doesn't clear significance on either baseline (p = 0.12 / 0.06). Together these
confirm the "wind hurts deep passing" half of the common claim (QB carries the largest confirmed
effect of any signal measured here); the kicker half is uncheckable in this warehouse for the
K-scoring reason above. Contrast `punt_environment.py`'s "doesn't order at all" weather finding —
that was about punting specifically, not about every weather claim this warehouse could test.

Every number in this verdict that held up against both walk-forward baselines — `implied_margin`'s
RB/TE effect, `wind`'s QB/WR effect, `is_sheltered`'s WR effect — is confirmed to within 0.002 of
Spearman rho re-running the identical measurement on the ESPN league's own scoring; the unconfirmed
signals (`implied_team_total`, `temp`) aren't cross-league-checked, since a signal that already
failed the single-warehouse robustness check doesn't need a second league to also fail to confirm it.
Join coverage is 97.8% (1,383 of 61,977 skill-position player-weeks excluded): `weekly_stats.team`
back-labels three relocated franchises (Raiders, Chargers, Rams) to
their current city for every season, while `schedules`/this table use the abbreviation actually in
use at the time (OAK/SD/STL, 2015-2016) — a small, understood gap, not corrected for here.

This stays *display*, not *weighted*, for the same reason `defense_vs_position.py`'s #132 verdict
does: the promotion bar is beating the vendor's own weekly projection, and that question is
unanswerable right now — zero player-weeks in this warehouse have both a completed game's actual
points and a Sleeper weekly projection (`weekly_stats` has no 2026 rows yet; #117). Re-run once that
archive accumulates.
"""

from pathlib import Path

import duckdb

from src import console

WAREHOUSE_PATH = Path("data/warehouse.duckdb")

# implied_margin thresholds, in points, naming where a team's expected outcome sits. Symmetric and
# round rather than fit to anything.
_BIG_LEAD_POINTS = 10
_LEAD_POINTS = 3

_BUILD_SQL = f"""
CREATE OR REPLACE TABLE game_environment AS
WITH team_games AS (
    SELECT
        game_id, season, week, gameday, gametime,
        home_team AS team, away_team AS opponent, TRUE AS is_home,
        spread_line, total_line, spread_line AS implied_margin,
        roof, temp, wind, div_game, home_rest AS rest_days
    FROM schedules
    UNION ALL
    SELECT
        game_id, season, week, gameday, gametime,
        away_team AS team, home_team AS opponent, FALSE AS is_home,
        spread_line, total_line, -spread_line AS implied_margin,
        roof, temp, wind, div_game, away_rest AS rest_days
    FROM schedules
)
SELECT
    season,
    week,
    team,
    opponent,
    game_id,
    CAST(gameday || ' ' || gametime AS TIMESTAMP) AS kickoff,
    is_home,
    total_line,
    spread_line,
    implied_margin,
    implied_margin > 0 AS is_favorite,
    total_line / 2 + implied_margin / 2 AS implied_team_total,
    CASE
        WHEN implied_margin IS NULL THEN NULL
        WHEN implied_margin >= {_BIG_LEAD_POINTS} THEN 'big_favorite'
        WHEN implied_margin >= {_LEAD_POINTS} THEN 'favorite'
        WHEN implied_margin > -{_LEAD_POINTS} THEN 'pick_em'
        WHEN implied_margin > -{_BIG_LEAD_POINTS} THEN 'underdog'
        ELSE 'big_underdog'
    END AS gamescript_lean,
    roof,
    temp,
    wind,
    rest_days,
    CAST(div_game AS BOOLEAN) AS div_game
FROM team_games
ORDER BY season, week, team
"""


def _build_sql() -> str:
    return _BUILD_SQL


def build_game_environment() -> None:
    con = duckdb.connect(str(WAREHOUSE_PATH))
    con.execute(_build_sql())
    (count,) = con.execute("SELECT COUNT(*) FROM game_environment").fetchone()
    console.table("game_environment", count)
    con.close()


if __name__ == "__main__":
    build_game_environment()
