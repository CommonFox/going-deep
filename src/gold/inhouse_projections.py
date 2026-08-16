"""Build an in-house PPG projection: a shift-based model trained on this warehouse's own data.

Unlike the other `src/gold` modules, this one isn't pure SQL — it's Python/pandas/scikit-learn
over the warehouse (still no fetch step, no network). It's the "home-grown predictive model" idea
from project notes: prior-year weighted baseline + team context -> next-year PPG, meant to become
a fifth voice in `consensus.py`'s aggregation alongside the external sites.

Features as of `target_season - 1` (the most recent season actually played):
- `player_weighted_baselines.weighted_ppg_ppr` / `weighted_games_per_season` / `seasons_used` —
  the player's own recency-weighted history and durability, plus that table's volume/role/
  efficiency component block and its touchdown-luck-neutral `weighted_td_regressed_ppg`.
- `offensive_line_grades.ol_grade` and `skill_position_grades.grade` (own position) — a LEFT JOIN,
  since QB never matches `skill_position_grades` (it only grades WR/TE/RB);
  HistGradientBoostingRegressor takes the resulting NaN natively, no imputation needed.

Plus a role block as of the start of `target_season` itself — `target_is_starter` and
`changed_team`, from that season's week 1 depth chart. A depth chart published before a snap is
played says nothing about the label, so this isn't leakage, and it's what lets the model tell an
incumbent from a career backup whose per-game history looks identical. The grades above are still
*measured* on `target_season - 1` (the season being predicted hasn't happened) but are attached to
the team the player is joining, so an offseason move changes his supporting cast rather than
carrying his old team's line into the projection.

Plus a career block that is instead as of `target_season` itself — `seasons_of_experience`,
`age_at_season`, `draft_pick`. Age and years-in-league for the season being
predicted are fixed and knowable before it starts, so using them isn't leakage the way a
performance stat would be. Without these the model had no way to represent a player's place on
the experience curve at all: its whole view of a high-scoring second-year player was last year's
rate plus team context, so it could only regress them toward the mean of everyone who scored
similarly, never toward what young ascending players specifically tend to do next.

Label: actual PPG in `target_season`, only for players who played >= the same games-played floor
`player_baselines.py` uses (imported, not redefined) — an actual season cut short by injury is as
untrustworthy a training target as a short season is a baseline input.

A second, much smaller model covers everyone the arm above structurally cannot see: players with no
`player_weighted_baselines` row for the season, because they have never put together a season of
`_MIN_GAMES_PLAYED` games. Every feature above derives from that row, so without one there is
nothing to predict from, and those players were simply absent from this table — 150 of them that
ESPN projected and this warehouse didn't. Two groups land there and they are the same modelling
problem: rookies with no NFL history at all, and fringe veterans who have played but never enough
in one season to earn a baseline. It reads what a human reads for an unproven player instead —
draft capital, the landing spot's line and skill-corps grades, whether he has already won a week 1
job, and an unweighted career rate that is NULL for a true rookie and so tells the model which of
the two it's looking at.

Its cohort is every such player reaching that season's week 1 depth chart, which makes its
availability label uncensored: that population is closed, so no weekly_stats row means he didn't
play, and 0 is honest rather than missing. Both arms write to this one table so that `_role_games`
normalises over one combined population and consumers don't need to know there are two models
behind the column.

Draft capital comes from `rosters.draft_number`, not `players.draft_pick`: the latter is the more
natural home for it but is published on a long enough lag that it carried no 2026 draft class at
all during the 2026 preseason, which would have left the arm blind in exactly the season it's for.

Every stored prediction is produced without the model having seen its own label:
- each labeled `target_season` from the first fold onward is predicted by a model trained only on
  the labeled seasons *before* it — a walk-forward backtest, one fold per season (see
  `_walk_forward`). The per-fold, per-position error is written to `inhouse_backtest`.
- the live (unlabeled) `target_season` — the one that actually feeds `consensus.py` — is predicted
  by a model refit on *all* labeled seasons combined, since there's no reason to hold data back
  from the projection that actually matters once the honest backtest above has already run.

`predicted_ppg_ppr` and `expected_games` are predicted separately, by models with different feature
sets, because "how good per game" and "how many games will they play" are different questions —
prior-year scoring rate says nothing about availability, and the role signals that do carry it (see
`_GAMES_FEATURE_COLUMNS`) say little about scoring.

Two season-total columns come out of those two halves, because this model and the external
projection sites decompose a season differently:

- `projected_points` = `predicted_ppg_ppr * expected_games`. The honest expectation: points per
  game he plays, times the games he's expected to play. This is the number to rank a real roster
  on, and it is *not* what the sites publish.
- `projected_points_full` = `predicted_ppg_ppr * role_games`, the column `consensus.py` blends.

The distinction matters because the sites are internally inconsistent in a specific way, and
matching them requires reproducing that inconsistency rather than being more correct than it. They
do not discount a starter for injury risk — CBS projects 17.0 games for every starter it covers,
and the others land within a percent of the same assumption — but they *do* discount a backup for
role, expressed through the per-game term instead of through games: CBS has Jake Browning at 0.9
points per game across a full 17. Their product is what `projected_points_full` has to be
comparable to, so it keeps the role discount and drops the injury one. `_role_games` does that by
measuring a player's expected games against a starter's at the same position and season.

A floor and a ceiling come out alongside the expectation, from the same features and estimator refit
under the pinball loss: `ppg_p10` / `ppg_p90`, and the season totals `projected_points_floor` /
`projected_points_ceiling` on the same games basis as `projected_points`. `upside` is the gap
between the ceiling and the expectation — the "might he boom" score, which is not recoverable from
`projected_points`, since two players can share a projection and not share a distribution.

Both are reported as measured rather than assumed:
- Calibration is checked out-of-sample on every walk-forward fold (`_print_quantile_report`). The
  ceiling is honest — 89.9% of actual veteran seasons land under `ppg_p90` against a 90% target,
  with no crossed intervals. The floor is not: 15.4% land under `ppg_p10` against a 10% target, so
  it sits too high and should be read as optimistic. That is the scoring label's games floor showing
  through — a season cut short is excluded from training, so nothing ever teaches the model how far
  down a bad one goes, and the same censoring that makes the label trustworthy for a per-game rate
  makes the low tail specifically unreliable.
- `upside` does carry information beyond the mean projection, and not much. Splitting each quintile
  of `predicted_ppg_ppr` at its median upside, the high-upside half beats its own projection by more
  than 3 PPG 15.5% of the time against 11.9% for the low-upside half (+3.5pp, z=2.51, p=0.012, over
  2,416 walk-forward player-seasons), with the lift positive in all five quintiles. It holds to a
  4-PPG threshold and dies by 5 (p=0.11), so it is a genuine tilt in the odds, not a boom detector.

Both arms' availability labels are anchored on the roster rather than the box score, because a
player who was in the league all season and never took a snap has no `weekly_stats` row at all and
would otherwise read as unobserved rather than as the zero he is — roughly 90-110 players a season
on the veteran side alone. Without those zeros the model cannot answer below about 4 games, and
career backups keep season totals they will never earn. Note the scoring walk-forward can't see any
of this: its games floor excludes exactly the players it affects, so the availability metrics are
reported on their own population.
"""

from pathlib import Path

import duckdb
import pandas as pd
from scipy.stats import spearmanr
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.inspection import permutation_importance
from sklearn.metrics import mean_absolute_error, r2_score

from src.gold.player_baselines import (
    _COMPONENT_COLUMNS,
    _MIN_GAMES_PLAYED,
    _SKILL_POSITIONS,
)
from src.silver.teams import normalize_team

WAREHOUSE_PATH = Path("data/warehouse.duckdb")

# _COMPONENT_COLUMNS is imported rather than restated so that a column added to
# player_baselines.py reaches the model automatically instead of being built and then silently
# ignored. It's the volume/role/efficiency block: how the player's prior scoring was actually
# earned, which is the part that carries across a change of team or role, where a bare
# points-per-game average carries nothing.
_BASE_FEATURES = [
    "weighted_ppg_ppr", "weighted_games_per_season", "seasons_used", "ol_grade", "skill_grade",
    "seasons_of_experience", "age_at_season", "draft_pick",
    "target_is_starter", "changed_team",
] + _COMPONENT_COLUMNS
_FEATURE_COLUMNS = _BASE_FEATURES + [f"position_{p}" for p in _SKILL_POSITIONS]

# Availability is its own question, so it gets its own model rather than sharing the PPG feature
# set. Prior-year scoring rate says nothing about whether a player will be on the field, and the
# role block (how many weeks he appeared on a depth chart, how often as a starter, and whether he
# finished there) is the part that distinguishes "wasn't the starter yet" from "kept getting hurt"
# — two stories a games-played average alone tells identically.
_GAMES_FEATURE_COLUMNS = [
    "weighted_games_per_season", "seasons_used",
    "seasons_of_experience", "age_at_season", "draft_pick",
    "weeks_on_depth_chart", "starter_share", "end_starter",
    # Availability's own version of the same blind spot the PPG model had: a player who won't be
    # on the field at all is a different proposition from one who was hurt, and only the target
    # season's depth chart separates them before the season starts.
    "target_is_starter",
] + [f"position_{p}" for p in _SKILL_POSITIONS]

# Conservative for a dataset this small (a few hundred rows per cohort): shallow trees, a decent
# per-leaf sample floor, and a gentle learning rate all guard against overfitting what little data
# there is.
_MODEL_PARAMS = dict(max_depth=3, min_samples_leaf=15, learning_rate=0.1, random_state=0)

# The same estimator refit under the pinball loss, which asks it for a conditional quantile rather
# than a conditional mean. Two players can share a projected PPG and be completely different draft
# propositions — the season-long RB2 who will finish within a point of his number either way, and
# the backup one injury away from a starter's workload — and a point estimate cannot tell them
# apart. p90 is the ceiling that "which players might boom" is actually asking about; p10 is the
# floor, which is the same question asked of a roster you already own.
#
# Deliberately additional to `predicted_ppg_ppr` rather than replacing it: the median is not the
# mean, and consensus.py blends this model against external sites that publish means.
_QUANTILE_LOW = 0.10
_QUANTILE_HIGH = 0.90

# offensive_line_grades needs target_season - 1 >= 2018 (PFR advanced stats' own floor) before it
# has any non-null values at all. A walk-forward fold whose entire training slice sits below that
# would be handed a feature column that's 100% missing rather than just sparse, which breaks
# HistGradientBoostingRegressor's binning outright — so folds start at the first labeled season
# *after* this one, which is the first whose training slice is guaranteed to contain a season with
# real team-context values. Per-row (rather than per-column) missingness inside a fold is fine:
# it's exactly what skill_grade already looks like for every QB row.
_MIN_TRAIN_SEASON_WITH_TEAM_CONTEXT = 2019

# A fold/position slice below this many rows gets no metrics row: R2 and Spearman over a handful of
# players are noise reported to three decimals, which is worse than reporting nothing.
_MIN_METRIC_ROWS = 20

# Season length for the `projected_points_full` multiplier, read from the schedule rather than
# hardcoded to 17 — the warehouse still holds 16-game seasons (<= 2020), and the number moving
# again is a live possibility. The live target_season has no schedule published yet, so it falls
# back to the most recent season that does.
_SEASON_GAMES_SQL = """
SELECT season, CAST(ROUND(COUNT(*) * 2.0 / COUNT(DISTINCT home_team)) AS BIGINT) AS games
FROM schedules
WHERE game_type = 'REG'
GROUP BY season
"""

# A second arm for players the veteran model structurally cannot see: those with no
# player_weighted_baselines row for the season being projected, because they've never put together
# a season of `_MIN_GAMES_PLAYED` games. Everything the veteran arm reads is derived from that row,
# so without one there's nothing to predict from and the player was simply absent from this table.
#
# Two groups land here, and they're the same modelling problem. Rookies, who have no NFL history at
# all (~55-95 a season), and fringe veterans who have played but never enough in one season to earn
# a baseline (~35-55 a season). Covering only the first left the second uncovered — 38 of the 39
# skill players ESPN projected and this warehouse still didn't. Pooling them also roughly doubles
# the training data, which the rookie half benefits from too.
#
# What replaces the missing history is what a human reads for an unproven player anyway: where he
# went in the draft, what he walked into, whether he's already won a job, and — for the ones who
# have played at all — their unweighted career rate, which is NULL for a true rookie and so tells
# the model which of the two groups it's looking at. Kept deliberately small: this trains on
# hundreds of rows where the veteran arm has thousands, and a wide feature set would just memorise.
_NO_BASELINE_FEATURES = [
    "draft_number", "age_at_season", "seasons_of_experience", "career_ppg", "career_games",
    "ol_grade", "skill_grade", "target_is_starter",
]
_NO_BASELINE_FEATURE_COLUMNS = (
    _NO_BASELINE_FEATURES + [f"position_{p}" for p in _SKILL_POSITIONS]
)

# Draft capital, experience and a won job are close to the whole story on whether an unproven
# player sees the field; nothing about the landing spot's blocking tells you much about his own
# availability.
_NO_BASELINE_GAMES_FEATURE_COLUMNS = [
    "draft_number", "age_at_season", "seasons_of_experience", "career_games", "target_is_starter",
] + [f"position_{p}" for p in _SKILL_POSITIONS]

_COMPONENT_SELECTS = ",\n".join(f"    b.{column}" for column in _COMPONENT_COLUMNS)

# The cohort is every player reaching that season's week 1 depth chart who has no
# player_weighted_baselines row for it. Roster membership alone would be the wrong test: it isn't
# comparable across eras (the current feed lists a preseason 90-man roster where the older ones
# list far fewer), and a player who never reaches a depth chart is not one any draft board needs a
# number for.
#
# draft_number comes from rosters rather than players.draft_pick, which is the more natural home
# for it but is published on a long enough lag that it had no 2026 class at all while the roster
# feed already carried the full board. NULL means undrafted, which is signal, not absence.
_NO_BASELINE_FEATURE_SQL = f"""
WITH roster_facts AS (
    SELECT
        player_id,
        season,
        MIN(draft_number) AS draft_number,
        MIN(years_exp) AS seasons_of_experience,
        ANY_VALUE(player_name) AS player_name,
        MIN(position) AS position
    FROM rosters
    WHERE position IN {_SKILL_POSITIONS}
    GROUP BY player_id, season
),
-- Every prior season's games and points, with no games-played floor — the floor is precisely why
-- these players have no baseline, so applying it again would zero out the only history they have.
-- Unweighted, unlike player_weighted_baselines: there's too little of it for recency weighting to
-- say anything a plain career rate doesn't.
career_to_date AS (
    SELECT
        w.player_id,
        s.season,
        SUM(w.fantasy_points_ppr) / COUNT(*) AS career_ppg,
        COUNT(*) AS career_games
    FROM weekly_stats w
    JOIN (SELECT DISTINCT season FROM roster_facts) s ON w.season < s.season
    WHERE w.season_type = 'REG'
        AND w.fantasy_points_ppr IS NOT NULL
        AND w.position IN {_SKILL_POSITIONS}
    GROUP BY w.player_id, s.season
),
week_one_role AS (
    SELECT
        season,
        gsis_id,
        MIN(team) AS team,
        MAX(CASE WHEN is_starter THEN 1 ELSE 0 END) AS is_starter
    FROM player_depth_chart
    WHERE week = 1
    GROUP BY season, gsis_id
),
actual_scoring AS (
    SELECT
        player_id,
        season AS target_season,
        SUM(fantasy_points_ppr) / COUNT(*) AS actual_ppg_ppr
    FROM weekly_stats
    WHERE season_type = 'REG'
        AND fantasy_points_ppr IS NOT NULL
        AND position IN {_SKILL_POSITIONS}
    GROUP BY player_id, season
    HAVING COUNT(*) >= {_MIN_GAMES_PLAYED}
),
actual_availability AS (
    SELECT player_id, season AS target_season, COUNT(*) AS actual_games
    FROM weekly_stats
    WHERE season_type = 'REG'
        AND fantasy_points_ppr IS NOT NULL
        AND position IN {_SKILL_POSITIONS}
    GROUP BY player_id, season
)
SELECT
    r.season AS target_season,
    r.player_id,
    r.player_name,
    r.position,
    r.draft_number,
    r.seasons_of_experience,
    c.career_ppg,
    c.career_games,
    w.team AS context_team,
    w.is_starter AS target_is_starter,
    r.season - YEAR(TRY_CAST(pl.birth_date AS DATE)) AS age_at_season,
    ol.ol_grade,
    sk.grade AS skill_grade,
    -- Benchmark only, never a feature, for the same reason as the veteran arm.
    adp.consensus_adp,
    o.actual_ppg_ppr,
    -- Unlike the veteran arm, this label is genuinely uncensored. The cohort is a closed
    -- population — every first-season player on the week 1 chart — so a rookie with no
    -- weekly_stats row didn't play, rather than being unobserved, and 0 is the honest label. That
    -- is exactly the bias the veteran games model still carries and this one doesn't.
    --
    -- Guarded on the season having actually been played: without it the live season's rookies,
    -- who have no weekly_stats rows because the season hasn't started, would every one of them be
    -- labelled a genuine 0 and train the games model to predict that nobody plays.
    CASE
        WHEN r.season <= (SELECT MAX(season) FROM weekly_stats)
        THEN COALESCE(av.actual_games, 0)
    END AS actual_games
FROM roster_facts r
JOIN week_one_role w ON w.gsis_id = r.player_id AND w.season = r.season
-- The defining condition: no usable prior-season baseline, so the veteran arm has nothing to read.
-- This is what keeps the two arms disjoint — every player gets a number from exactly one of them.
LEFT JOIN player_weighted_baselines b
    ON b.player_id = r.player_id AND b.target_season = r.season
LEFT JOIN career_to_date c ON c.player_id = r.player_id AND c.season = r.season
LEFT JOIN players pl ON pl.gsis_id = r.player_id
LEFT JOIN offensive_line_grades ol ON ol.team = w.team AND ol.season = r.season - 1
LEFT JOIN skill_position_grades sk
    ON sk.team = w.team AND sk.season = r.season - 1 AND sk.position = r.position
LEFT JOIN adp_consensus adp ON adp.gsis_id = r.player_id AND adp.season = r.season
LEFT JOIN actual_scoring o ON o.player_id = r.player_id AND o.target_season = r.season
LEFT JOIN actual_availability av
    ON av.player_id = r.player_id AND av.target_season = r.season
WHERE b.player_id IS NULL
-- Same reproducibility guard as the veteran query: a stable total order over the frame, so
-- histogram binning can't shift between runs on identical data.
ORDER BY r.season, r.player_id
"""

# player_team resolves each player's most-played team in a season (mirrors the
# team_by_player_season pattern in skill_position_grades.py, over weekly_stats instead of
# ngs_data), so team context can be joined in as of target_season - 1.
_FEATURE_SQL = f"""
WITH team_by_player_season AS (
    SELECT
        season, player_id, normalize_team(team) AS team,
        -- COUNT(*) alone isn't a total order: a player traded at the midpoint has an equal game
        -- count on both teams, and DuckDB then resolves the tie differently from run to run,
        -- silently reassigning their team (and with it the ol_grade/skill_grade joined below).
        -- MAX(week) breaks ties toward the team they finished the season on, which is also the
        -- better signal for the following season this row is used to predict; team is a final
        -- alphabetical backstop so the ordering is total.
        ROW_NUMBER() OVER (
            PARTITION BY season, player_id
            ORDER BY COUNT(*) DESC, MAX(week) DESC, normalize_team(team)
        ) AS rn
    FROM weekly_stats
    WHERE season_type = 'REG' AND team IS NOT NULL
    GROUP BY season, player_id, normalize_team(team)
),
player_team AS (
    SELECT season, player_id, team FROM team_by_player_season WHERE rn = 1
),
-- The scoring label keeps the games-played floor: a season cut short by injury is as untrustworthy
-- a training target for a per-game rate as a short season is a baseline input.
actual_scoring AS (
    SELECT
        player_id,
        season AS target_season,
        SUM(fantasy_points_ppr) / COUNT(*) AS actual_ppg_ppr
    FROM weekly_stats
    WHERE season_type = 'REG'
        AND fantasy_points_ppr IS NOT NULL
        AND position IN {_SKILL_POSITIONS}
    GROUP BY player_id, season
    HAVING COUNT(*) >= {_MIN_GAMES_PLAYED}
),
-- The availability label deliberately does *not*, because the floor censors exactly the outcome
-- the games model most needs to be able to predict, and it's anchored on the roster rather than
-- the box score for the same reason. Counting only weekly_stats rows means a player who was in the
-- league all year and never took a snap has no row at all, so he reads as unobserved rather than
-- as the zero he actually is — roughly 90-110 players a season, on top of the 75-100 who play 1-5
-- games. Left to itself the model then can't answer below about 4 games, and career backups keep
-- season totals they'll never earn.
--
-- Anchoring on rosters supplies those zeros: roster membership is the statement "in the league
-- this season", and the appearance count is then a genuine 0 rather than a missing row. Practice
-- squad and cut spells are deliberately kept — a player on the practice squad really does play
-- zero games, which is the case being modelled. FULL OUTER so a weekly_stats row with no matching
-- roster row (nflverse has a handful) keeps its label instead of silently vanishing.
roster_seasons AS (
    SELECT DISTINCT player_id, season
    FROM rosters
    WHERE position IN {_SKILL_POSITIONS}
        -- Without this the live season, whose rosters are published but whose games haven't been
        -- played, would label every player a real 0.
        AND season <= (SELECT MAX(season) FROM weekly_stats)
),
appearances AS (
    SELECT player_id, season, COUNT(*) AS games
    FROM weekly_stats
    WHERE season_type = 'REG'
        AND fantasy_points_ppr IS NOT NULL
        AND position IN {_SKILL_POSITIONS}
    GROUP BY player_id, season
),
actual_availability AS (
    SELECT
        COALESCE(rs.player_id, a.player_id) AS player_id,
        COALESCE(rs.season, a.season) AS target_season,
        COALESCE(a.games, 0) AS actual_games
    FROM roster_seasons rs
    FULL OUTER JOIN appearances a ON a.player_id = rs.player_id AND a.season = rs.season
),
-- Role as of target_season - 1, which is what separates "wasn't the starter yet" from "kept
-- getting hurt" — two very different stories that a games-played average alone tells identically.
-- end_starter deliberately reads the *last* week the player appears rather than the season as a
-- whole: a rookie who takes the job over in week 4 finishes as the starter, and that's the better
-- statement about next season than a share diluted by the weeks he spent behind someone else.
prior_role AS (
    SELECT
        season,
        gsis_id,
        COUNT(*) AS weeks_on_depth_chart,
        AVG(CASE WHEN is_starter THEN 1.0 ELSE 0.0 END) AS starter_share,
        CAST(ARG_MAX(is_starter, week) AS BIGINT) AS end_starter
    FROM player_depth_chart
    GROUP BY season, gsis_id
),
-- Role as of the start of target_season itself, not the season before it — the one thing here
-- that describes the season being predicted rather than the last one played. It isn't leakage:
-- a week 1 depth chart is published before a snap of the season is played, so it says nothing
-- about the label. It's also the single largest thing the model was missing. Without it a backup
-- quarterback who last started ten games two years ago looks identical to the incumbent, which is
-- why the model was projecting career backups as starters.
--
-- MIN(team)/MAX(is_starter) rather than ANY_VALUE: a player holds one week 1 depth chart slot in
-- practice, but the aggregate has to be a total order regardless, or DuckDB's parallel scan
-- resolves ties differently run to run and the projections stop being reproducible.
target_role AS (
    SELECT
        season,
        gsis_id,
        MIN(team) AS team,
        MAX(CASE WHEN is_starter THEN 1 ELSE 0 END) AS is_starter
    FROM player_depth_chart
    WHERE week = 1
    GROUP BY season, gsis_id
)
SELECT
    b.target_season,
    b.player_id,
    b.player_name,
    b.position,
    b.seasons_used,
    b.weighted_ppg_ppr,
    b.weighted_games_per_season,
{_COMPONENT_SELECTS},
    pt.team,
    -- The team whose supporting cast this player will actually be in: the target season's depth
    -- chart where it knows, last season's box scores otherwise. This is what makes an offseason
    -- move visible at all — a receiver who signs elsewhere in March used to carry his old team's
    -- line and skill-corps grades straight into the projection.
    COALESCE(tr.team, pt.team) AS context_team,
    -- Absent from the depth chart is folded into "not a starter" rather than left NULL on purpose.
    -- Being *listed at all* isn't comparable across eras — the legacy feed lists a post-cuts
    -- 53-man roster while the current snapshot feed lists a preseason 90-man one, so coverage of
    -- the baseline cohort runs 43% in 2019 against 68% in 2026 — but the starter call itself is
    -- stable at 22-25% in every season. Collapsing the unstable distinction keeps the feature
    -- meaning one fixed thing at training time and at prediction time.
    COALESCE(tr.is_starter, 0) AS target_is_starter,
    -- Left NULL when either side is unknown: a move that can't be observed is genuinely unknown,
    -- unlike the starter call above, where absence carries a real meaning worth encoding.
    CASE
        WHEN tr.team IS NOT NULL AND pt.team IS NOT NULL
        THEN CAST(tr.team != pt.team AS BIGINT)
    END AS changed_team,
    ol.ol_grade,
    sk.grade AS skill_grade,
    -- Unlike every feature above, these are as of target_season itself rather than
    -- target_season - 1. That isn't leakage: how old a player will be and how many years they'll
    -- have been in the league are both fixed facts about the season being predicted, known before
    -- a snap of it is played, which is exactly not true of performance or team-context stats.
    -- 0 = rookie year, 1 = entering year two, and so on.
    b.target_season - pl.rookie_season AS seasons_of_experience,
    -- birth_date arrives as an ISO string, and TRY_CAST leaves an unparseable one as NULL rather
    -- than failing the whole build over one bad row.
    b.target_season - YEAR(TRY_CAST(pl.birth_date AS DATE)) AS age_at_season,
    -- A static career fact, so no as-of question arises. NULL means undrafted, which is signal
    -- rather than absence — HistGradientBoostingRegressor consumes the NaN natively, the same way
    -- it already does for skill_grade on every QB row. Coverage is 56-81% across rookie eras with
    -- no gradient, so a null doesn't stand in for "old season". draft_round is deliberately left
    -- out: pick number already encodes it, and carrying both scored marginally worse in the
    -- walk-forward backtest while adding no permutation importance.
    pl.draft_pick,
    pr.weeks_on_depth_chart,
    pr.starter_share,
    pr.end_starter,
    -- Benchmark only, deliberately absent from _FEATURE_COLUMNS: what the preseason draft market
    -- thought of this player, so the walk-forward can answer "does the model rank better than ADP
    -- already does" instead of only "better than doing nothing". It must never become a feature —
    -- breakout_candidates.py's entire premise is that this model is ADP-blind, and a model that
    -- had seen ADP could not meaningfully disagree with it.
    adp.consensus_adp,
    o.actual_ppg_ppr,
    av.actual_games
FROM player_weighted_baselines b
LEFT JOIN player_team pt ON pt.player_id = b.player_id AND pt.season = b.target_season - 1
LEFT JOIN target_role tr ON tr.gsis_id = b.player_id AND tr.season = b.target_season
-- Grades are still measured on target_season - 1 (the season being predicted hasn't been played,
-- so it has no team context of its own) but are now attached to the team the player is joining
-- rather than the one he left. "How good was my new supporting cast last year" is the question
-- worth asking; "how good was the one I no longer play for" is not. Both joins have to sit after
-- pt and tr, since they read the COALESCE across them.
LEFT JOIN offensive_line_grades ol
    ON ol.team = COALESCE(tr.team, pt.team) AND ol.season = b.target_season - 1
LEFT JOIN skill_position_grades sk
    ON sk.team = COALESCE(tr.team, pt.team)
    AND sk.season = b.target_season - 1
    AND sk.position = b.position
-- players.years_of_experience is deliberately unused: it's one static value per player that
-- doesn't equal seasons-since-rookie (Brady reads 23 against a 2000 rookie season and a final
-- 2022 season), so applying it to a historical row would leak how the career eventually turned
-- out. rookie_season and birth_date are immutable, so deriving from them is safe at any season.
LEFT JOIN players pl ON pl.gsis_id = b.player_id
LEFT JOIN prior_role pr ON pr.gsis_id = b.player_id AND pr.season = b.target_season - 1
-- As of target_season itself, unlike every other prior-year join here: preseason ADP is published
-- before the season starts, so lining it up with the season it was drafted for is what makes it a
-- fair benchmark rather than a lagged one.
LEFT JOIN adp_consensus adp ON adp.gsis_id = b.player_id AND adp.season = b.target_season
LEFT JOIN actual_scoring o ON o.player_id = b.player_id AND o.target_season = b.target_season
LEFT JOIN actual_availability av
    ON av.player_id = b.player_id AND av.target_season = b.target_season
-- Not cosmetic: DuckDB's parallel joins return these rows in a different order from run to run,
-- and HistGradientBoostingRegressor sums gradients per histogram bin in row order. Float addition
-- isn't associative, so a reordered frame shifts split gains just enough to flip tree splits,
-- which cascades — back-to-back rebuilds on identical data moved projections by up to 20 points.
-- (target_season, player_id) is unique here, so this is a total order and makes rebuilds
-- reproducible.
ORDER BY b.target_season, b.player_id
"""


def _add_position_dummies(df: pd.DataFrame) -> pd.DataFrame:
    dummies = pd.get_dummies(df["position"])
    for position in _SKILL_POSITIONS:
        if position not in dummies.columns:
            dummies[position] = 0
    dummies = dummies[list(_SKILL_POSITIONS)].add_prefix("position_").astype(int)
    return pd.concat([df, dummies], axis=1)


def _fit(train: pd.DataFrame) -> HistGradientBoostingRegressor:
    model = HistGradientBoostingRegressor(**_MODEL_PARAMS)
    model.fit(train[_FEATURE_COLUMNS], train["actual_ppg_ppr"])
    return model


def _fit_games(train: pd.DataFrame) -> HistGradientBoostingRegressor:
    model = HistGradientBoostingRegressor(**_MODEL_PARAMS)
    model.fit(train[_GAMES_FEATURE_COLUMNS], train["actual_games"])
    return model


def _fit_generic(
    train: pd.DataFrame, feature_columns: list[str], label: str
) -> HistGradientBoostingRegressor:
    """Same estimator and settings, with the feature set and label passed in — the unproven arm
    uses a different set of both, but no reason to use different hyperparameters."""
    model = HistGradientBoostingRegressor(**_MODEL_PARAMS)
    model.fit(train[feature_columns], train[label])
    return model


def _add_quantiles(
    frame: pd.DataFrame, train: pd.DataFrame, feature_columns: list[str]
) -> pd.DataFrame:
    """Attach a floor and a ceiling PPG to `frame`, from models fit on `train` under the pinball
    loss. Same features and hyperparameters as the mean model — the only thing changing is which
    part of the conditional distribution is being asked for."""
    for column, quantile in (("ppg_p10", _QUANTILE_LOW), ("ppg_p90", _QUANTILE_HIGH)):
        model = HistGradientBoostingRegressor(
            loss="quantile", quantile=quantile, **_MODEL_PARAMS
        )
        model.fit(train[feature_columns], train["actual_ppg_ppr"])
        frame[column] = model.predict(frame[feature_columns])
    return frame


def _metrics(fold: pd.DataFrame, target_season: int, position: str) -> dict:
    """Score one fold (or one position within it) against every benchmark worth beating.

    Three comparisons, because "the model has some skill" and "the model is worth running" are
    different claims:
    - `naive_*` — carrying last season's recency-weighted rate forward unchanged, i.e. what
      `player_weighted_baselines` alone would say. Losing to this means the model is subtracting.
    - `adp_spearman` — the preseason draft market's own ordering. This is the parity benchmark:
      ADP is thousands of drafters pricing in offseason information (job changes, scheme, camp
      reports) that nothing in this warehouse observes.
    - `model_spearman_vs_adp` — the model re-scored on *only* the ADP-covered players, so the two
      Spearmans above sit on the same population. Scored over the full pool the model would get
      credit for correctly ranking undrafted players ADP never had an opinion on.
    """
    actual, predicted = fold["actual_ppg_ppr"], fold["predicted_ppg_ppr"]
    drafted = fold[fold["consensus_adp"].notna()]
    has_adp = len(drafted) >= _MIN_METRIC_ROWS
    return {
        "target_season": target_season,
        "position": position,
        "n": len(fold),
        "mae": mean_absolute_error(actual, predicted),
        "r2": r2_score(actual, predicted),
        "spearman": spearmanr(predicted, actual).statistic,
        "naive_mae": mean_absolute_error(actual, fold["weighted_ppg_ppr"]),
        "naive_spearman": spearmanr(fold["weighted_ppg_ppr"], actual).statistic,
        # Negated so a better draft pick sorts the same direction as more points, making this
        # Spearman directly comparable to the two above rather than its mirror image.
        "adp_spearman": (
            spearmanr(-drafted["consensus_adp"], drafted["actual_ppg_ppr"]).statistic
            if has_adp else float("nan")
        ),
        "model_spearman_vs_adp": (
            spearmanr(drafted["predicted_ppg_ppr"], drafted["actual_ppg_ppr"]).statistic
            if has_adp else float("nan")
        ),
        "adp_n": len(drafted),
    }


def _games_metrics(games_fold: pd.DataFrame) -> dict:
    """Score the availability half on its own population — everyone who appeared, not just the
    players who cleared the scoring label's 6-game floor.

    Scoring it on the 6+ games population instead would grade the model on a subgroup it is
    deliberately no longer specialised to: once it can predict backups at all, its predictions for
    everyone shift down, which reads as a regression when measured only on players who by
    construction played a lot. The naive comparison moves to the same wider population with it, so
    the two stay like for like.
    """
    return {
        "games_n": len(games_fold),
        "games_mae": mean_absolute_error(games_fold["actual_games"],
                                         games_fold["expected_games"]),
        "games_naive_mae": mean_absolute_error(games_fold["actual_games"],
                                               games_fold["weighted_games_per_season"]),
    }


def _no_baseline_metrics(fold: pd.DataFrame, target_season: int) -> dict:
    """Score one unproven-player fold.

    The naive benchmark can't be "carry last season forward" the way it is for veterans — there is
    no qualifying last season, which is what put these players here. It's the position's mean PPG
    over the training seasons instead: the honest "no model, just know what players at this
    position usually do" baseline. ADP is the same parity benchmark as everywhere else, and matters
    more here than anywhere, since draft capital is most of what the market is going on too.
    """
    actual, predicted = fold["actual_ppg_ppr"], fold["predicted_ppg_ppr"]
    drafted = fold[fold["consensus_adp"].notna()]
    has_adp = len(drafted) >= _MIN_METRIC_ROWS
    return {
        "target_season": target_season,
        "position": "NO_BASELINE",
        "n": len(fold),
        "mae": mean_absolute_error(actual, predicted),
        "r2": r2_score(actual, predicted),
        "spearman": spearmanr(predicted, actual).statistic,
        "naive_mae": mean_absolute_error(actual, fold["position_mean_ppg"]),
        "naive_spearman": spearmanr(fold["position_mean_ppg"], actual).statistic,
        "adp_spearman": (
            spearmanr(-drafted["consensus_adp"], drafted["actual_ppg_ppr"]).statistic
            if has_adp else float("nan")
        ),
        "model_spearman_vs_adp": (
            spearmanr(drafted["predicted_ppg_ppr"], drafted["actual_ppg_ppr"]).statistic
            if has_adp else float("nan")
        ),
        "adp_n": len(drafted),
    }


def _walk_forward_no_baseline(
    labeled: pd.DataFrame, cohort: pd.DataFrame, fold_seasons: list[int]
) -> tuple[list[pd.DataFrame], list[pd.DataFrame], pd.DataFrame]:
    """Walk-forward for the unproven arm, same shape as the veteran one.

    The games half trains on the whole cohort rather than only the scoring-labelled part, because
    here the whole cohort *is* the label: a player who never appeared is a real 0, not a missing
    row.
    """
    predictions, games_predictions, metrics = [], [], []
    for season in fold_seasons:
        train = labeled[labeled["target_season"] < season]
        train_cohort = cohort[cohort["target_season"] < season]
        if train.empty or train_cohort.empty:
            continue
        games_model = _fit_generic(train_cohort, _NO_BASELINE_GAMES_FEATURE_COLUMNS, "actual_games")
        scoring_model = _fit_generic(train, _NO_BASELINE_FEATURE_COLUMNS, "actual_ppg_ppr")

        fold = labeled[labeled["target_season"] == season].copy()
        if not fold.empty:
            fold["predicted_ppg_ppr"] = scoring_model.predict(fold[_NO_BASELINE_FEATURE_COLUMNS])
            fold["expected_games"] = games_model.predict(fold[_NO_BASELINE_GAMES_FEATURE_COLUMNS])
            fold = _add_quantiles(fold, train, _NO_BASELINE_FEATURE_COLUMNS)
            # Position means come from the training slice only — a benchmark computed on the fold
            # would have seen the answers the model is being graded against.
            position_means = train.groupby("position")["actual_ppg_ppr"].mean()
            fold["position_mean_ppg"] = fold["position"].map(position_means).fillna(
                train["actual_ppg_ppr"].mean()
            )
            predictions.append(fold)
            if len(fold) >= _MIN_METRIC_ROWS:
                metrics.append(_no_baseline_metrics(fold, season))

        games_fold = cohort[cohort["target_season"] == season].copy()
        games_fold["expected_games"] = games_model.predict(
            games_fold[_NO_BASELINE_GAMES_FEATURE_COLUMNS]
        )
        games_predictions.append(games_fold)
    return predictions, games_predictions, pd.DataFrame(metrics)


def _role_games(frame: pd.DataFrame) -> pd.Series:
    """How many games this player would play at full health, given the role he's expected to hold.

    `expected_games` mixes two very different reasons a player misses time — he gets hurt, or he
    was never going to be on the field — and the external sources treat those differently. None of
    them discounts a starter for injury risk (CBS projects 17.0 games for every starter it covers),
    but all of them do discount a backup for role: CBS has Jake Browning at 0.9 points per game
    across a full 17, which is a role judgment expressed through the per-game term rather than
    through games. Their *product* is what `projected_points_full` has to be comparable to.

    Dividing a player's expected games by what a starter at his position and season is expected to
    get strips out the league-wide injury discount, which is roughly common to everyone, and leaves
    the part that is specifically about role. A week 1 starter lands at ~1.0 and so keeps the full
    season; a backup at half a starter's expected games keeps half of it. Capped at 1.0, since
    "healthier than a typical starter" is not a thing this should extrapolate into extra games.
    """
    starters = frame[frame["target_is_starter"] == 1]
    reference = starters.groupby(["target_season", "position"])["expected_games"].mean()
    key = pd.MultiIndex.from_frame(frame[["target_season", "position"]])
    reference_games = pd.Series(reference.reindex(key).to_numpy(), index=frame.index)
    # A season/position with no listed starter at all leaves the ratio undefined; falling back to
    # 1.0 keeps that player on the full season rather than silently zeroing his projection.
    ratio = (frame["expected_games"] / reference_games).clip(upper=1.0).fillna(1.0)
    return frame["season_games"] * ratio


def _walk_forward(
    labeled: pd.DataFrame, labeled_games: pd.DataFrame, fold_seasons: list[int]
) -> tuple[list[pd.DataFrame], pd.DataFrame]:
    """Predict each labeled season from a model trained only on the seasons before it.

    Replaces what used to be a single train-2019/holdout-2020 split (n=331). One split can't
    distinguish a real improvement from fold noise, which makes it useless as the accept/reject
    instrument for any change to the feature set — hence one fold per labeled season, each training
    on everything that came before it, exactly mirroring how the live model is fit.
    """
    predictions, games_predictions, metrics = [], [], []
    for season in fold_seasons:
        train = labeled[labeled["target_season"] < season]
        # The games half trains on its own, wider slice — every player who appeared at all, not
        # just those clearing the scoring label's games floor — and is scored on that same wider
        # population below.
        train_games = labeled_games[labeled_games["target_season"] < season]
        games_model = _fit_games(train_games)

        fold = labeled[labeled["target_season"] == season].copy()
        fold["predicted_ppg_ppr"] = _fit(train).predict(fold[_FEATURE_COLUMNS])
        fold["expected_games"] = games_model.predict(fold[_GAMES_FEATURE_COLUMNS])
        fold = _add_quantiles(fold, train, _FEATURE_COLUMNS)
        predictions.append(fold)

        games_fold = labeled_games[labeled_games["target_season"] == season].copy()
        games_fold["expected_games"] = games_model.predict(games_fold[_GAMES_FEATURE_COLUMNS])
        games_predictions.append(games_fold)

        metrics.append(_metrics(fold, season, "ALL") | _games_metrics(games_fold))
        for position, group in fold.groupby("position"):
            if len(group) >= _MIN_METRIC_ROWS:
                metrics.append(_metrics(group, season, position))
    return predictions, games_predictions, pd.DataFrame(metrics)


def _print_no_baseline_report(
    metrics: pd.DataFrame, folds: list[pd.DataFrame], games_folds: list[pd.DataFrame]
) -> None:
    print("\nUnproven arm walk-forward (no qualifying prior season to work from):")
    print(f"  {'season':>6} {'n':>5} {'MAE':>6} {'R2':>6} {'rho':>6} | "
          f"{'naive MAE':>9} {'naive rho':>9} | {'ADP rho':>7} {'model rho':>9} {'(n)':>6}")
    for row in metrics.itertuples():
        print(f"  {row.target_season:>6} {row.n:>5} {row.mae:>6.2f} {row.r2:>6.3f} "
              f"{row.spearman:>6.3f} | {row.naive_mae:>9.2f} {row.naive_spearman:>9.3f} | "
              f"{row.adp_spearman:>7.3f} {row.model_spearman_vs_adp:>9.3f} {row.adp_n:>6}")

    pooled = pd.concat(folds, ignore_index=True)
    row = _no_baseline_metrics(pooled, 0)
    print(f"  {'pooled':>6} {row['n']:>5} {row['mae']:>6.2f} {row['r2']:>6.3f} "
          f"{row['spearman']:>6.3f} | {row['naive_mae']:>9.2f} {row['naive_spearman']:>9.3f} | "
          f"{row['adp_spearman']:>7.3f} {row['model_spearman_vs_adp']:>9.3f} {row['adp_n']:>6}")

    games = _games_metrics_no_baseline(pd.concat(games_folds, ignore_index=True))
    print(f"  Games played, pooled over the whole cohort including true zeros "
          f"(n={games['games_n']}): MAE={games['games_mae']:.2f} vs {games['games_naive_mae']:.2f} "
          f"for always guessing the cohort mean")
    _print_quantile_report(pooled, "unproven arm")


def _games_metrics_no_baseline(games_fold: pd.DataFrame) -> dict:
    """Unproven-player availability error, against always guessing the cohort's mean games played.

    The veteran baseline (`weighted_games_per_season`) doesn't exist here for the same reason the
    scoring one doesn't — there is no qualifying prior season to average.
    """
    return {
        "games_n": len(games_fold),
        "games_mae": mean_absolute_error(games_fold["actual_games"],
                                         games_fold["expected_games"]),
        "games_naive_mae": mean_absolute_error(
            games_fold["actual_games"],
            pd.Series(games_fold["actual_games"].mean(), index=games_fold.index),
        ),
    }


def _print_backtest_report(
    metrics: pd.DataFrame, folds: list[pd.DataFrame], games_folds: list[pd.DataFrame]
) -> None:
    overall = metrics[metrics["position"] == "ALL"]
    print("\nWalk-forward backtest — each season predicted by a model trained only on prior ones:")
    print(f"  {'season':>6} {'n':>5} {'MAE':>6} {'R2':>6} {'rho':>6} | "
          f"{'naive MAE':>9} {'naive rho':>9} | {'ADP rho':>7} {'model rho':>9} {'(n)':>6}")
    for row in overall.itertuples():
        print(f"  {row.target_season:>6} {row.n:>5} {row.mae:>6.2f} {row.r2:>6.3f} "
              f"{row.spearman:>6.3f} | {row.naive_mae:>9.2f} {row.naive_spearman:>9.3f} | "
              f"{row.adp_spearman:>7.3f} {row.model_spearman_vs_adp:>9.3f} {row.adp_n:>6}")

    # Pooled over every fold rather than averaged over per-fold numbers, so a season with more
    # qualifying players counts for more and small positions (TE, QB) don't get a whole fold's
    # worth of weight from 25 rows.
    pooled = pd.concat(folds, ignore_index=True)
    print("\n  Pooled across all folds, by position:")
    for position in ("QB", "RB", "WR", "TE"):
        group = pooled[pooled["position"] == position]
        if len(group) < _MIN_METRIC_ROWS:
            continue
        row = _metrics(group, 0, position)
        print(f"  {position:>6} {row['n']:>5} {row['mae']:>6.2f} {row['r2']:>6.3f} "
              f"{row['spearman']:>6.3f} | {row['naive_mae']:>9.2f} {row['naive_spearman']:>9.3f} | "
              f"{row['adp_spearman']:>7.3f} {row['model_spearman_vs_adp']:>9.3f} "
              f"{row['adp_n']:>6}")

    games = _games_metrics(pd.concat(games_folds, ignore_index=True))
    print(f"\n  Games played, pooled over every player who appeared (n={games['games_n']}): "
          f"MAE={games['games_mae']:.2f} vs {games['games_naive_mae']:.2f} for the weighted "
          f"average it replaces")
    _print_quantile_report(pooled, "veteran arm")


def _print_quantile_report(pooled: pd.DataFrame, label: str) -> None:
    """Are the floor and ceiling the quantiles they claim to be?

    An interval is only worth reading if it covers what it says it covers, and a quantile model can
    be badly calibrated while still scoring well on rank metrics. Empirical coverage is the direct
    test: the share of actual seasons landing under `ppg_p90` should be about 90%, and under
    `ppg_p10` about 10%. Reported out-of-sample, pooled over every walk-forward fold.
    """
    if "ppg_p90" not in pooled.columns or pooled["ppg_p90"].isna().all():
        return
    below_high = (pooled["actual_ppg_ppr"] <= pooled["ppg_p90"]).mean()
    below_low = (pooled["actual_ppg_ppr"] <= pooled["ppg_p10"]).mean()
    inverted = (pooled["ppg_p90"] < pooled["ppg_p10"]).sum()
    print(f"\n  Quantile calibration, {label} (n={len(pooled)}): "
          f"{100 * below_low:.1f}% of actual seasons under ppg_p10 (target "
          f"{100 * _QUANTILE_LOW:.0f}%), {100 * below_high:.1f}% under ppg_p90 (target "
          f"{100 * _QUANTILE_HIGH:.0f}%); {inverted} crossed intervals")


def build_inhouse_projections() -> None:
    con = duckdb.connect(str(WAREHOUSE_PATH))
    con.create_function("normalize_team", normalize_team, ["VARCHAR"], "VARCHAR")
    df = con.execute(_FEATURE_SQL).df()
    unproven = con.execute(_NO_BASELINE_FEATURE_SQL).df()
    season_games = dict(con.execute(_SEASON_GAMES_SQL).fetchall())
    con.close()

    df = _add_position_dummies(df)
    # The live target_season's schedule isn't published yet (and nflverse won't carry it until
    # well after these projections are wanted), so it inherits the most recent season that is —
    # the same assumption every external site is making when it projects 17 games.
    latest_scheduled = season_games[max(season_games)]
    for season in df["target_season"].unique():
        season_games.setdefault(int(season), latest_scheduled)

    labeled = df[df["actual_ppg_ppr"].notna()].sort_values("target_season")
    labeled_games = df[df["actual_games"].notna()].sort_values("target_season")
    labeled_seasons = sorted(labeled["target_season"].unique())
    fold_seasons = [s for s in labeled_seasons if s > _MIN_TRAIN_SEASON_WITH_TEAM_CONTEXT]
    live_season = df["target_season"].max()

    output_frames = []
    metrics = pd.DataFrame()

    if fold_seasons:
        output_frames, games_frames, metrics = _walk_forward(
            labeled, labeled_games, fold_seasons
        )
        _print_backtest_report(metrics, output_frames, games_frames)

        # Out-of-sample (on the fold, not its training slice) so a feature the model overfit to
        # doesn't look important just because it memorized training noise. Answers "do the
        # team-context signals (ol_grade/skill_grade) actually carry weight" rather than assuming
        # they do because they're in the feature list. Run on the last fold: it has the most
        # training data behind it, so it's the fold closest to the live model actually shipped.
        last_fold = output_frames[-1]
        last_model = _fit(labeled[labeled["target_season"] < fold_seasons[-1]])
        importances = permutation_importance(
            last_model, last_fold[_FEATURE_COLUMNS], last_fold["actual_ppg_ppr"],
            n_repeats=20, random_state=0,
        )
        ranked_importances = sorted(
            zip(_FEATURE_COLUMNS, importances.importances_mean), key=lambda x: -x[1]
        )
        importance_str = ", ".join(f"{name}={score:.3f}" for name, score in ranked_importances)
        print(f"\nPermutation feature importance on the {fold_seasons[-1]} fold "
              f"(mean R2 drop when shuffled): {importance_str}")
    else:
        print("No labeled season has a trainable prior slice yet — skipping the backtest.")

    # Restrict live output to players with some evidence they're actually in the league: either
    # they're on the target season's week 1 depth chart, or they played the season before. Without
    # that, a player with no data more recent than 2+ years back (e.g. a retiree like Tom Brady,
    # whose last game was 2022) still gets extrapolated forward from their old numbers.
    #
    # The depth chart half is what the previous prior-season-only test was missing: it admits ~40
    # players returning from a lost season, whose presence on a current depth chart is direct
    # evidence of exactly the "are they still active" question that used to have no answer here.
    # It deliberately does not go the other way and *drop* players the chart hasn't listed — 61 of
    # them played last season, and they include genuine free agents still likely to sign.
    #
    # Backtest folds are untouched: a missing team there is just a smaller feature signal, not a
    # "don't show this" judgment call the way it is for the live cohort feeding consensus.py.
    live = df[(df["target_season"] == live_season) & df["context_team"].notna()].copy()
    if not labeled.empty:
        final_model = _fit(labeled)
        live["predicted_ppg_ppr"] = final_model.predict(live[_FEATURE_COLUMNS])
        live["expected_games"] = _fit_games(labeled_games).predict(live[_GAMES_FEATURE_COLUMNS])
        live = _add_quantiles(live, labeled, _FEATURE_COLUMNS)
    else:
        live["predicted_ppg_ppr"] = float("nan")
        live["expected_games"] = float("nan")
        live["ppg_p10"] = float("nan")
        live["ppg_p90"] = float("nan")
        print("No labeled seasons available yet — live cohort predictions left null.")
    output_frames.append(live)

    # The rookie arm, backtested and predicted the same way, then concatenated into the same table.
    # One table rather than two so that role_games below is normalised over one combined
    # population, and so every consumer (consensus.py, breakout_candidates.py) picks rookies up
    # without needing to know there are two models behind the column.
    unproven = _add_position_dummies(unproven)
    unproven_labeled = unproven[unproven["actual_ppg_ppr"].notna()].sort_values("target_season")
    unproven_cohort = unproven[unproven["actual_games"].notna()].sort_values("target_season")
    unproven_folds, unproven_games_folds, unproven_metrics = _walk_forward_no_baseline(
        unproven_labeled, unproven_cohort, fold_seasons
    )
    if not unproven_metrics.empty:
        _print_no_baseline_report(unproven_metrics, unproven_folds, unproven_games_folds)
        metrics = pd.concat([metrics, unproven_metrics], ignore_index=True)
    output_frames.extend(unproven_folds)

    live_unproven = unproven[unproven["target_season"] == live_season].copy()
    if not unproven_labeled.empty and not live_unproven.empty:
        live_unproven["predicted_ppg_ppr"] = _fit_generic(
            unproven_labeled, _NO_BASELINE_FEATURE_COLUMNS, "actual_ppg_ppr"
        ).predict(live_unproven[_NO_BASELINE_FEATURE_COLUMNS])
        live_unproven["expected_games"] = _fit_generic(
            unproven_cohort, _NO_BASELINE_GAMES_FEATURE_COLUMNS, "actual_games"
        ).predict(live_unproven[_NO_BASELINE_GAMES_FEATURE_COLUMNS])
        live_unproven = _add_quantiles(
            live_unproven, unproven_labeled, _NO_BASELINE_FEATURE_COLUMNS
        )
        output_frames.append(live_unproven)
        print(f"\nUnproven arm: {len(live_unproven)} players with no qualifying prior season "
              f"projected for {live_season}, trained on {len(unproven_labeled)} labelled seasons.")

    result = pd.concat(output_frames, ignore_index=True)
    result["season_games"] = result["target_season"].map(season_games)
    result["role_games"] = _role_games(result)
    result["projected_points"] = result["predicted_ppg_ppr"] * result["expected_games"]
    result["projected_points_full"] = result["predicted_ppg_ppr"] * result["role_games"]
    # Season totals on the same games basis as `projected_points`, so floor/expectation/ceiling are
    # three readings of one number rather than three different questions.
    result["projected_points_floor"] = result["ppg_p10"] * result["expected_games"]
    result["projected_points_ceiling"] = result["ppg_p90"] * result["expected_games"]
    # How much room there is above the expectation, which is the actual "might he boom" score — and
    # not recoverable from projected_points, since two players can share it and not share this.
    result["upside"] = result["projected_points_ceiling"] - result["projected_points"]
    result = result[[
        "player_id", "player_name", "position", "target_season",
        "predicted_ppg_ppr", "ppg_p10", "ppg_p90", "expected_games", "season_games", "role_games",
        "projected_points", "projected_points_full",
        "projected_points_floor", "projected_points_ceiling", "upside",
    ]]

    con = duckdb.connect(str(WAREHOUSE_PATH))
    con.execute("CREATE OR REPLACE TABLE inhouse_projections AS SELECT * FROM result")
    (count,) = con.execute("SELECT COUNT(*) FROM inhouse_projections").fetchone()
    con.execute("CREATE OR REPLACE TABLE inhouse_backtest AS SELECT * FROM metrics")
    (metric_count,) = con.execute("SELECT COUNT(*) FROM inhouse_backtest").fetchone()
    con.close()

    print(f"\nBuilt {count} rows into {WAREHOUSE_PATH} (table: inhouse_projections)")
    print(f"Built {metric_count} rows into {WAREHOUSE_PATH} (table: inhouse_backtest)")


if __name__ == "__main__":
    build_inhouse_projections()
