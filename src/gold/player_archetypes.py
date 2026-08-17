"""Classify each player-season into a career-stage archetype, and price what the archetype is worth.

Pure warehouse-to-warehouse Python/SQL — no fetch step, no network. Built from `boom_bust` (for the
outcome label and the elite-finish history), `draft_value` (for the ADP-adjusted edge),
`adp_consensus` (to carry the live season, which has no outcome yet), `players` (birth date and
rookie season) and `league_settings`.

The idea comes from a draft-strategy segment that sorts running backs into three groups — a
"breakout candidate" in year 3 or younger with no RB1 finish yet, a "trusty veteran" in year 7+ or
aged 27+, and an "RB in his prime" under 27 and/or already holding an RB1 finish — then reads off
how often each group returned on its ADP, got hurt, boomed or busted, and assigns each a flat number
of points to draft by.

## The taxonomy

The source's three groups overlap (a 29-year-old with an RB1 finish is both "prime" and "trusty
veteran"; a 24-year-old in year 2 is both "breakout candidate" and "prime"), so they cannot be used
to partition a board. The two questions actually being asked are independent, so they are crossed
here into a grid instead:

- *Has he ever been elite?* — `prior_elite_finishes > 0`, counted from `boom_bust.is_elite_finish`,
  which is a top-`team_count` positional finish in any earlier season. This is the source's "already
  has an RB1 finish", derived per league rather than hardcoded to 12, so a 10-team league's bar is a
  top-10 finish.
- *Where is he on the age curve?* — `_VETERAN_AGE` / `_VETERAN_EXPERIENCE`, the source's own 27-and-
  27+/year-7+ pair.

That yields four cells, plus `Unproven Youth` split off the unproven-and-young cell for the players
still inside the source's year-3 window — the "breakout candidate" proper, kept separate because a
year-2 player with no elite finish is a genuinely different proposition from a year-5 one:

- `Unproven Youth` — `seasons_of_experience <= _BREAKOUT_EXPERIENCE`, never elite.
- `Unproven Prime` — past that window, still young by both bars, never elite.
- `Proven Prime` — young by both bars, already elite. The source's headline archetype.
- `Unproven Veteran` — old by either bar, never elite. The career backup / journeyman.
- `Proven Veteran` — old by either bar, already elite. The source's "trusty veteran".

`_VETERAN_AGE` is held at the source's 27 for every position rather than tuned per position. A scan
of every cut from 25 to 31 (see the module's own report) shows proven RBs, WRs and QBs separating at
essentially any threshold in that range — the young-minus-old gap in ADP-adjusted surplus runs +7 to
+20 points for RB at every cut, with no clean knee — while TEs never separate at all. Fitting a
per-position threshold to that would be reading noise; the position-specific information is carried
by scoring every cell per position instead, which is where it belongs.

## The rate chart is mostly a restatement of ADP

`archetype_outcomes` reproduces the source's chart directly: the share of each archetype landing in
each of `boom_bust`'s outcome buckets, plus `elite_finish_rate`. Those rates replicate the source's
finding cleanly — proven prime RBs finish elite 48.6% of the time against 8.1% for unproven youth.

They are also, on their own, close to meaningless as draft advice, and this is the one correction
that matters most here. Proven prime RBs carry a mean ADP of 30; unproven youth carry 170. A group
drafted in the third round finishing top-12 six times more often than a group drafted in the
fourteenth is not an edge, it is the draft market working. Scoring archetypes off those rates — the
source's flat +5 / +3 / -3 — pays for information already in the price.

`mean_surplus` is therefore the column the score is built from: the mean of
`draft_value.surplus_centered`, which is realised value minus what that player's *ADP slot*
historically returned, centred within its league-season. It answers the question the rate chart
cannot — at the same cost, does this archetype return more? — and `draft_value` already carries the
walk-forward isotonic ADP curve that makes the subtraction honest.

## What survives the correction, and what does not

Measured on one league (the two leagues re-measure the same player-seasons, so pooling them would
halve the standard errors without adding a single independent observation):

- **`RB` / `Proven Prime`: +17.8 points of surplus, t=2.63, positive in 6 of 8 seasons.** The
  source's headline archetype is real and survives the price adjustment. It also holds inside every
  ADP band it appears in (+18.8 in picks 1-24, +9.3 in 25-60, +13.0 in 61-120), so it is not an
  artifact of the curve's shape at one end.
- **`WR` / `Proven Veteran`: -6.3, t=-2.01, and `WR` / `Unproven Veteran`: -3.9, t=-3.30.** Aging
  receivers are systematically overpriced. This is the source's negative "trusty veteran" score —
  and it is a receiver effect, not the running back effect the source assigns it to. `RB` /
  `Proven Veteran` sits at +2.3 (t=0.56), indistinguishable from zero.
- **Everything else is noise.** No other cell clears |t| >= 2. Scored naively, `QB` / `Proven Prime`
  would carry +10.0 off 46 rows and then *invert* out of sample, realising -9.9 and -16.8 in the two
  leagues' held-out folds. That cell is exactly what the reliability gate below exists to suppress.

A general "rank every player by his archetype's historical surplus" score does not work: walk-forward
over 2,667 player-seasons it lands at Spearman -0.031 (p=0.11) against realised surplus, with
non-monotonic quintiles, and no alternative definition rescues it (empirical-Bayes shrinkage,
beat-price rate, and a shrunk rate all land between -0.09 and -0.02). The taxonomy is not a ranking
system. Its value is concentrated in a few cells, which is why `archetype_edge` is gated to zero
rather than reported for all twenty.

## The gate, and the one thing it cannot do

`is_reliable` requires `_MIN_CELL_ROWS` rows and `|t| >= _MIN_ABS_T`. `archetype_edge` is that cell's
`mean_surplus` when it passes and 0.0 when it does not, so a player in an unproven cell is scored
"no information", never a made-up number.

Applied walk-forward — each season's edge fit only on the seasons before it, mirroring
`draft_value`'s curve — the gate passes only the negative cells, and they hold up: players it marks
down realise -1.46 surplus against +0.41 for ungated players, and finish elite 3.8% of the time
against 16%. Ungated, the same test separates nothing at all (+0.22 points, p=0.85).

It never passes `RB` / `Proven Prime` in any historical fold. That cell needs the full eight seasons
to clear t=2; through 2020 it had neither the rows nor the significance, so a drafter running this
table in 2021 would not have been handed the edge. This is stated rather than smoothed over: the RB
prime effect is a strong full-sample finding that was not prospectively detectable at the sample
sizes available, and the live season's edge — fit on all played seasons, the same convention
`draft_value` uses for its live curve — is the first time it is being extended forward.
"""

from pathlib import Path

import duckdb
import pandas as pd

from src import console
from src.gold.points_over_replacement import _SKILL_POSITIONS

WAREHOUSE_PATH = Path("data/warehouse.duckdb")

# The source's own thresholds, kept rather than tuned — see the module docstring on why a
# per-position age knee would be fitting noise. 0 = rookie year, so `<= 2` is "year 3 or younger"
# and `>= 6` is "year 7+".
_BREAKOUT_EXPERIENCE = 2
_VETERAN_EXPERIENCE = 6
_VETERAN_AGE = 27

# A cell needs this many player-seasons and this much signal before its mean surplus is reported as
# an edge rather than zeroed. Both bars matter: `QB`/`Proven Prime` clears neither and would
# otherwise ship a +10.0 score that inverts out of sample.
_MIN_CELL_ROWS = 50
_MIN_ABS_T = 2.0

# Significance is measured on one league rather than the pool. Both leagues score the same
# player-seasons under different rulebooks, so pooling them doubles the row count without adding an
# independent observation, and would halve every standard error on the strength of that.
_SIGNIFICANCE_LEAGUE = "sleeper"

# The walk-forward edge needs enough seasons behind it to be a benchmark rather than an echo of one
# season's noise — the same bar and the same reason as draft_value's ADP curve.
_MIN_EDGE_SEASONS = 3

# boom_bust's buckets, in the order the source's chart reads them: the good outcomes, then the
# failure modes. Keyed by bucket, valued by the rate column it becomes.
_OUTCOME_BUCKETS = {
    "League Winner": "league_winner_rate",
    "Delivered": "delivered_rate",
    "Beat His Price": "beat_price_rate",
    "Met His Price": "met_price_rate",
    "Fine": "fine_rate",
    "Busted": "busted_rate",
    "Got Injured": "injured_rate",
    "Never Had The Job": "never_had_job_rate",
}

# The population is every player-season with a preseason ADP: "which players should I target" is a
# question about a draft board, and a player no source tracked as draftable was never on one.
# boom_bust's 'No Preseason ADP' bucket is therefore excluded rather than carried as a ninth rate.
_POPULATION_SQL = f"""
WITH played AS (
    SELECT
        league_key, season, player_id, player_name, position, consensus_adp,
        outcome_bucket, is_elite_finish, games_played
    FROM boom_bust
    WHERE position IN {_SKILL_POSITIONS} AND consensus_adp IS NOT NULL
),
-- The season being drafted has no boom_bust row at all (nothing has been played), so its board
-- comes from adp_consensus instead, crossed onto each league so the archetype grid and its edge are
-- keyed the same way for the live season as for every past one.
live AS (
    SELECT
        l.league_key,
        a.season,
        a.gsis_id AS player_id,
        a.player_name,
        a.position,
        a.consensus_adp,
        CAST(NULL AS VARCHAR) AS outcome_bucket,
        CAST(NULL AS BOOLEAN) AS is_elite_finish,
        CAST(NULL AS BIGINT) AS games_played
    FROM adp_consensus a
    CROSS JOIN league_settings l
    WHERE a.season > (SELECT MAX(season) FROM boom_bust)
        AND a.consensus_adp IS NOT NULL
        AND a.position IN {_SKILL_POSITIONS}
),
population AS (SELECT * FROM played UNION ALL SELECT * FROM live),
-- Elite finishes strictly before the season in question, so a player's own current season can
-- never count toward the history that classifies it. Per league, since what counts as elite is a
-- top-`team_count` finish and the two leagues run 12 and 10 teams.
career AS (
    SELECT
        p.league_key,
        p.season,
        p.player_id,
        COUNT(b.season) AS prior_seasons,
        SUM(CASE WHEN b.is_elite_finish THEN 1 ELSE 0 END) AS prior_elite_finishes,
        MAX(CASE WHEN b.season = p.season - 1 AND b.is_elite_finish THEN 1 ELSE 0 END)
            AS elite_last_season
    FROM population p
    LEFT JOIN boom_bust b
        ON b.player_id = p.player_id AND b.league_key = p.league_key AND b.season < p.season
    GROUP BY 1, 2, 3
)
SELECT
    p.league_key,
    p.season,
    p.player_id,
    p.player_name,
    p.position,
    p.consensus_adp,
    p.outcome_bucket,
    p.is_elite_finish,
    p.games_played,
    -- Both are fixed, knowable facts about the season before it starts, exactly like
    -- inhouse_projections' use of the same two columns: how old a player will be and how many years
    -- he will have been in the league are not outcomes. rookie_season and birth_date are immutable,
    -- unlike players.years_of_experience, which is a single career-end value and would leak.
    p.season - pl.rookie_season AS seasons_of_experience,
    p.season - YEAR(TRY_CAST(pl.birth_date AS DATE)) AS age_at_season,
    c.prior_seasons,
    c.prior_elite_finishes,
    c.elite_last_season,
    dv.surplus_centered,
    dv.projected_surplus,
    dv.expected_value,
    dv.actual_value
FROM population p
JOIN career c
    ON c.league_key = p.league_key AND c.season = p.season AND c.player_id = p.player_id
LEFT JOIN players pl ON pl.gsis_id = p.player_id
LEFT JOIN draft_value dv
    ON dv.league_key = p.league_key AND dv.season = p.season AND dv.player_id = p.player_id
ORDER BY p.league_key, p.season, p.player_id
"""


def _classify(df: pd.DataFrame) -> pd.Series:
    """Assign the five-cell grid, in the one order that makes it a partition.

    Tested first, `Unproven Youth` claims the year-1-to-3 players who have not been elite; the
    proven/veteran split then covers everyone else exactly once. A player missing a birth date or
    rookie season would fall through every comparison, so those are handled by the caller rather
    than silently landing in a cell they don't belong to.
    """
    proven = df["prior_elite_finishes"] > 0
    veteran = (df["age_at_season"] >= _VETERAN_AGE) | (
        df["seasons_of_experience"] >= _VETERAN_EXPERIENCE
    )
    breakout = (df["seasons_of_experience"] <= _BREAKOUT_EXPERIENCE) & ~proven

    archetype = pd.Series("Unproven Prime", index=df.index, dtype="object")
    archetype[proven & ~veteran] = "Proven Prime"
    archetype[proven & veteran] = "Proven Veteran"
    archetype[~proven & veteran] = "Unproven Veteran"
    archetype[breakout] = "Unproven Youth"
    archetype[df["age_at_season"].isna() | df["seasons_of_experience"].isna()] = None
    return archetype


def _cell_stats(group: pd.DataFrame) -> pd.Series:
    """One archetype cell's rate profile and its ADP-adjusted edge."""
    scored = group["surplus_centered"].dropna()
    per_season = group.dropna(subset=["surplus_centered"]).groupby("season")["surplus_centered"]
    season_means = per_season.mean()
    mean_surplus = scored.mean() if len(scored) else float("nan")
    standard_error = scored.sem() if len(scored) > 1 else float("nan")
    stats = {
        "n": len(group),
        "n_scored": len(scored),
        "mean_adp": group["consensus_adp"].mean(),
        "elite_finish_rate": group["is_elite_finish"].mean(),
        "mean_surplus": mean_surplus,
        "surplus_se": standard_error,
        "surplus_t": mean_surplus / standard_error if standard_error else float("nan"),
        "surplus_positive_rate": (scored > 0).mean() if len(scored) else float("nan"),
        "seasons_positive": int((season_means > 0).sum()),
        "seasons_measured": len(season_means),
    }
    outcomes = group["outcome_bucket"].value_counts(normalize=True)
    for bucket, column in _OUTCOME_BUCKETS.items():
        stats[column] = outcomes.get(bucket, 0.0) if len(outcomes) else float("nan")
    return pd.Series(stats)


def _build_outcomes(df: pd.DataFrame) -> pd.DataFrame:
    """The archetype grid: one row per (league_key, position, archetype).

    Rates are computed on that league's own rows, since the outcome buckets and the elite bar are
    both league-relative. `is_reliable` is deliberately *not*: it is decided once, on
    `_SIGNIFICANCE_LEAGUE`, and applied to both — a cell either carries a real effect or it does
    not, and letting a 10-team and a 12-team measurement of the same players disagree about that
    would just be sampling noise promoted to a flag.
    """
    played = df[df["outcome_bucket"].notna()]
    outcomes = (
        played.groupby(["league_key", "position", "archetype"])
        .apply(_cell_stats)
        .reset_index()
    )
    reference = outcomes[outcomes["league_key"] == _SIGNIFICANCE_LEAGUE].set_index(
        ["position", "archetype"]
    )
    reliable = (reference["n_scored"] >= _MIN_CELL_ROWS) & (
        reference["surplus_t"].abs() >= _MIN_ABS_T
    )
    key = pd.MultiIndex.from_frame(outcomes[["position", "archetype"]])
    outcomes["is_reliable"] = reliable.reindex(key).fillna(False).to_numpy()
    outcomes["archetype_edge"] = outcomes["mean_surplus"].where(outcomes["is_reliable"], 0.0)
    return outcomes


def _edge_lookup(train: pd.DataFrame) -> pd.Series:
    """Gated mean surplus per (position, archetype), fit on `train` alone.

    Returns 0.0 for a cell that fails the gate rather than dropping it, so a player in an unproven
    archetype is scored "no information" instead of inheriting a neighbouring cell's number.
    """
    reference = train[train["league_key"] == _SIGNIFICANCE_LEAGUE]
    grouped = reference.groupby(["position", "archetype"])["surplus_centered"]
    mean, count, standard_error = grouped.mean(), grouped.count(), grouped.sem()
    passes = (count >= _MIN_CELL_ROWS) & ((mean / standard_error).abs() >= _MIN_ABS_T)
    return mean.where(passes, 0.0)


def _add_walk_forward_edge(df: pd.DataFrame) -> pd.DataFrame:
    """Attach each row's `archetype_edge` without the fit ever having seen that row.

    Same convention as `draft_value`'s ADP curve: a played season is scored by an edge fit only on
    the seasons before it, and the live season — which has no realised side to fit on anyway — is
    scored by an edge fit on every played season. Seasons too early to have `_MIN_EDGE_SEASONS`
    behind them keep a NULL edge rather than one fit on a handful of rows.
    """
    scored = df[df["surplus_centered"].notna()]
    df["archetype_edge"] = float("nan")
    for season in sorted(df["season"].unique()):
        train = scored[scored["season"] < season]
        if train["season"].nunique() < _MIN_EDGE_SEASONS:
            continue
        fold = df["season"] == season
        key = pd.MultiIndex.from_frame(df.loc[fold, ["position", "archetype"]])
        df.loc[fold, "archetype_edge"] = (
            _edge_lookup(train).reindex(key).fillna(0.0).to_numpy()
        )
    return df


@console.analysis
def _print_rate_chart(outcomes: pd.DataFrame) -> None:
    """The source's own chart: what each archetype's seasons actually looked like."""
    chart = outcomes[outcomes["league_key"] == _SIGNIFICANCE_LEAGUE]
    print(f"\nOutcome rates by archetype ({_SIGNIFICANCE_LEAGUE}, drafted players only) — the "
          f"source's chart, reproduced:")
    header = f"  {'pos':>3} {'archetype':17s} {'n':>4} {'ADP':>5} {'elite%':>7}"
    header += "".join(f"{name.replace('_rate', '')[:9]:>10}" for name in _OUTCOME_BUCKETS.values())
    print(header)
    for position in _SKILL_POSITIONS:
        for row in chart[chart["position"] == position].itertuples():
            line = (f"  {row.position:>3} {row.archetype:17s} {int(row.n):>4} "
                    f"{row.mean_adp:>5.0f} {100 * row.elite_finish_rate:>7.1f}")
            line += "".join(
                f"{100 * getattr(row, column):>10.1f}" for column in _OUTCOME_BUCKETS.values()
            )
            print(line)


@console.analysis
def _print_edge_table(outcomes: pd.DataFrame) -> None:
    """The same grid after the price adjustment — which archetypes are actually worth targeting."""
    edges = outcomes[outcomes["league_key"] == _SIGNIFICANCE_LEAGUE]
    print(f"\nADP-adjusted edge ({_SIGNIFICANCE_LEAGUE}) — mean surplus over what that draft slot "
          f"historically returned:")
    print(f"  {'pos':>3} {'archetype':17s} {'n':>4} {'ADP':>5} {'edge':>7} {'t':>6} "
          f"{'beat%':>6} {'seasons+':>9}  verdict")
    for position in _SKILL_POSITIONS:
        for row in edges[edges["position"] == position].itertuples():
            verdict = "EDGE" if row.is_reliable else "not distinguishable from zero"
            if row.is_reliable and row.mean_surplus < 0:
                verdict = "AVOID"
            print(f"  {row.position:>3} {row.archetype:17s} {int(row.n_scored):>4} "
                  f"{row.mean_adp:>5.0f} {row.mean_surplus:>7.1f} {row.surplus_t:>6.2f} "
                  f"{100 * row.surplus_positive_rate:>6.1f} "
                  f"{f'{int(row.seasons_positive)}/{int(row.seasons_measured)}':>9}  {verdict}")


@console.analysis
def _print_walk_forward_report(df: pd.DataFrame) -> None:
    """Does the gated edge separate anything out of sample?

    The accept/reject test for the scoring half. Each season's edge was fit only on prior seasons,
    so this is the honest version of "would drafting by this table have helped".
    """
    tested = df[df["archetype_edge"].notna() & df["surplus_centered"].notna()]
    if tested.empty:
        return
    marked_down = tested[tested["archetype_edge"] < 0]
    marked_up = tested[tested["archetype_edge"] > 0]
    neutral = tested[tested["archetype_edge"] == 0]
    print(f"\nWalk-forward check — each season's edge fit only on the seasons before it "
          f"(n={len(tested)}, {tested['season'].min()}-{tested['season'].max()}):")
    for label, group in (("marked up  ", marked_up), ("marked down", marked_down),
                         ("no edge    ", neutral)):
        if group.empty:
            print(f"  {label}: no rows — the gate never passed a cell in this direction")
            continue
        print(f"  {label}: n={len(group):>5}  realised surplus "
              f"{group['surplus_centered'].mean():>+6.2f}  "
              f"elite finish {100 * group['is_elite_finish'].mean():>4.1f}%")


@console.analysis
def _print_live_board(df: pd.DataFrame, outcomes: pd.DataFrame) -> None:
    """The season being drafted, sorted by ADP — who each archetype actually is this year."""
    live_season = df["season"].max()
    live = df[
        (df["season"] == live_season) & (df["league_key"] == _SIGNIFICANCE_LEAGUE)
    ].nsmallest(25, "consensus_adp")
    if live.empty:
        return
    reliable = outcomes[
        outcomes["is_reliable"] & (outcomes["league_key"] == _SIGNIFICANCE_LEAGUE)
    ]
    print(f"\n{live_season} board, first 25 picks — archetype and its edge:")
    print(f"  {'ADP':>6} {'player':24s} {'pos':>3} {'age':>4} {'yr':>3} {'elite':>6} "
          f"{'archetype':17s} {'edge':>6}")
    for row in live.itertuples():
        edge = "" if not row.archetype_edge else f"{row.archetype_edge:>+6.1f}"
        print(f"  {row.consensus_adp:>6.1f} {row.player_name[:24]:24s} {row.position:>3} "
              f"{row.age_at_season:>4.0f} {row.seasons_of_experience:>3.0f} "
              f"{row.prior_elite_finishes:>6.0f} {row.archetype:17s} {edge:>6}")
    if not reliable.empty:
        names = ", ".join(
            f"{r.position}/{r.archetype} {r.mean_surplus:+.0f}" for r in reliable.itertuples()
        )
        print(f"\n  Cells carrying a reliable edge: {names}")


def build_player_archetypes() -> None:
    con = duckdb.connect(str(WAREHOUSE_PATH))
    df = con.execute(_POPULATION_SQL).df()
    con.close()

    df["archetype"] = _classify(df)
    unclassified = df["archetype"].isna().sum()
    df = df[df["archetype"].notna()].copy()

    outcomes = _build_outcomes(df)
    df = _add_walk_forward_edge(df)

    _print_rate_chart(outcomes)
    _print_edge_table(outcomes)
    _print_walk_forward_report(df)
    _print_live_board(df, outcomes)
    if unclassified:
        # A data-quality signal rather than model analysis, so this stays visible in a full build.
        console.note(f"{unclassified} player-seasons had no birth date or rookie season and are "
                     f"left out rather than guessed into a cell")

    result = df[[
        "league_key", "season", "player_id", "player_name", "position", "archetype",
        "age_at_season", "seasons_of_experience", "prior_seasons", "prior_elite_finishes",
        "elite_last_season", "consensus_adp", "outcome_bucket", "is_elite_finish",
        "games_played", "expected_value", "actual_value", "surplus_centered",
        "projected_surplus", "archetype_edge",
    ]]

    con = duckdb.connect(str(WAREHOUSE_PATH))
    con.execute("CREATE OR REPLACE TABLE player_archetypes AS SELECT * FROM result")
    (count,) = con.execute("SELECT COUNT(*) FROM player_archetypes").fetchone()
    con.execute("CREATE OR REPLACE TABLE archetype_outcomes AS SELECT * FROM outcomes")
    (outcome_count,) = con.execute("SELECT COUNT(*) FROM archetype_outcomes").fetchone()
    con.close()

    console.table("player_archetypes", count)
    console.table("archetype_outcomes", outcome_count)


if __name__ == "__main__":
    build_player_archetypes()
