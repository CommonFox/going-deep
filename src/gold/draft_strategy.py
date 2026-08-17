"""Test draft strategies by drafting them, thousands of times, against the real historical board.

Pure warehouse-to-warehouse Python/SQL — no fetch step, no network. Built from `adp_consensus`,
`points_over_replacement` and `league_settings`, all already loaded.

Every other model here prices a *player*. This one prices a *plan*: "three running backs and two
receivers in the first five rounds" is not a claim about Bijan Robinson, it is a claim about roster
construction, and the only way to settle it is to run the draft. So that is what this does — snake
drafts, one per (league, season, strategy, draft slot), off the ADP board that season actually had,
scored on what those players actually went on to do.

## How a simulated draft works

- **The board** is `adp_consensus` for that season: every QB/RB/WR/TE with a consensus ADP, sorted
  by it. Not a projection, not hindsight — the price the market actually charged that August.
- **The field** is `team_count` teams from `league_settings`, snaking through
  `skill starters + bench` rounds. Kickers, defenses and the ESPN league's punter are left out of
  the draft entirely and their roster spots removed, since no strategy question turns on them and
  `points_over_replacement` doesn't price them anyway.
- **A strategy** constrains only the first `_OPENING_ROUNDS` (5) picks. After that every team,
  focal included, takes the best player left by ADP subject to `_ROSTER_CAPS`.
- **Scoring** is the hindsight-optimal starting lineup from the roster the team ended up with,
  in that league's own scoring via `points_over_replacement.league_points`. Hindsight-optimal is a
  fiction — nobody sets a lineup knowing the season's totals — but it is the *same* fiction for
  every strategy, and the alternative (guessing at weekly lineup decisions) would add noise without
  changing any ranking.
- **A drafted player who never played is a zero**, on the same reasoning as `draft_value`: the
  expected return on a plan has to include the plan's bust rate, and dropping the busts would flatter
  whichever strategy drafts the most fragile players. The same career-appearances guard excludes ADP
  rows whose gsis_id never appears in a box score at all (an unresolved name, not a bust).

## Composition, not ordering

A five-round opening has 4^5 = 1024 orderings, most of them nonsense and nearly all of them
untestable at eleven seasons of data. The primary sweep therefore treats a strategy as a
**composition** — how many of each position in the first five rounds, 36 of them once openings with
three quarterbacks or three tight ends are dropped — and lets ADP resolve the order: at each of the
first five picks the focal team takes the best player available among the positions it still owes
itself. That is also what a human does. Nobody sitting on "3 RB, 2 WR" passes the WR1 at 1.03 to
force a running back; they take the best of what they still need.

Ordering is then asked separately, and only where it could matter: every distinct permutation of
3RB+2WR and 2RB+3WR is run as its own `ordering` strategy. That is the specific claim behind the
"full house" opening — not just three backs, but three backs *early*.

## Two fields, because the answer depends on who else is drafting

`field_model = 'adp'` puts every opponent on the pure ADP board, so the focal team is the only
deviator. This is the cleanest read of "does departing from the market's price sheet pay", and it is
the primary number.

It is also the *friendliest* one, because a lone deviator competes with nobody for the position it
is hoarding. `field_model = 'mixed'` gives each opponent its own random composition (seeded, so a
re-run reproduces byte-for-byte), three replicates per draft. Any edge that survives a field which
is also deviating is an edge in a real draft room; any edge that only exists against a passive field
is an artifact of being the only person at the table with a plan.

## The superflex counterfactual

Neither league is superflex. Both were checked against the platforms' own raw settings, not
inferred: ESPN's lineup slot 7 (`OP`, the superflex slot) is 0, and Sleeper's `roster_positions`
contains no `SUPER_FLEX`. `variant = 'actual'` is therefore what these leagues really are.

`variant = 'superflex'` runs the same sweep with one bench spot converted into a superflex slot —
roster size unchanged, so the draft is the same length — purely to answer "how much would that
change things". It is a counterfactual and it flatters early quarterbacks twice over, which is worth
being explicit about: the ADP board is still a 1QB board (FantasyPros and FFC price for the formats
their users play), so the focal team buys quarterbacks at 1QB prices for a superflex lineup. Real
superflex ADP has already repriced that. Read `variant = 'superflex'`, `field_model = 'mixed'` as
the *upper* bound on what an early quarterback is worth, and the gap between it and
`variant = 'actual'` as the thing the slot itself is doing.

## What the tables say, in short

Under both leagues' actual settings, a quarterback-free, receiver-leaning opening beats an
RB-heavy one, and the "full house" 3RB+2WR opening lands slightly *below* the pure-ADP control.
The effect is real but small — a few tens of points across a ~1,500-point season, on the order of
1-2% — which is itself the finding: opening composition is worth about one good waiver claim, and
strategies that hard-commit to one position (5RB, 5WR) are the only ones that genuinely hurt.
Everything the notebook needs to show that is in `draft_strategy_summary`; `draft_strategy_results`
keeps every individual simulated draft so any of it can be re-cut by season or draft slot.
"""

import itertools
from collections import Counter
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
from scipy import stats

from src import console
from src.gold.points_over_replacement import _FLEX_POSITIONS, _SKILL_POSITIONS

WAREHOUSE_PATH = Path("data/warehouse.duckdb")

# Position order is fixed here and used as an integer encoding throughout the simulator — the inner
# loop runs tens of millions of times and indexing a small array beats hashing a string.
_POSITIONS = list(_SKILL_POSITIONS)
_POSITION_CODE = {pos: code for code, pos in enumerate(_POSITIONS)}
_FLEX_CODES = tuple(_POSITION_CODE[pos] for pos in _FLEX_POSITIONS)
_QB = _POSITION_CODE["QB"]

# The window a "draft strategy" is actually a claim about. The full-house pitch is a claim about
# rounds 1-5; past that everyone is taking the best player left regardless of what they planned.
_OPENING_ROUNDS = 5

# Openings with three quarterbacks or three tight ends in five picks aren't strategies anyone is
# choosing between, and running them only widens the multiple-comparisons problem.
_MAX_OPENING = {"QB": 2, "RB": 5, "WR": 5, "TE": 2}

# How many of a position any team will roster across the whole draft. Only binding on QB and TE in
# practice — with 13-14 rounds nobody organically reaches seven backs — but without it a pure-ADP
# team will happily take a fourth quarterback in the 11th round and leave a starting slot empty.
_ROSTER_CAPS = {"QB": 2, "RB": 7, "WR": 7, "TE": 3}
_SUPERFLEX_QB_CAP = 4

# Replicates for the mixed field. Each one redraws every opponent's opening, so the spread across
# them is the noise floor for "how much does the rest of the room matter"; three is enough to keep
# that from dominating a strategy's mean without tripling the build time again.
_MIXED_REPLICATES = 3
_SEED = 20260816

_RESULT_COLUMNS = [
    "league_key", "variant", "field_model", "replicate", "season", "strategy", "strategy_kind",
    "draft_slot", "team_count", "starter_points", "field_points", "points_vs_field", "finish_rank",
]

_SUMMARY_COLUMNS = [
    "league_key", "variant", "field_model", "strategy", "strategy_kind", "n_drafts", "n_seasons",
    "starter_points", "points_vs_field", "finish_rank", "win_rate", "top_third_rate",
    "seasons_positive", "t_stat", "p_value",
]

# The board and its realised outcome, per league. Mirrors draft_value's `_ACTUAL_SQL`: a drafted
# player with no points_over_replacement row played no games and scored zero, but a gsis_id that
# never appears in any box score is an unresolved ADP name rather than a bust, and is dropped.
_BOARD_SQL = f"""
WITH career_appearances AS (
    SELECT player_id, COUNT(*) AS games
    FROM weekly_stats
    WHERE season_type = 'REG' AND fantasy_points_ppr IS NOT NULL
    GROUP BY player_id
),
adp AS (
    SELECT a.gsis_id AS player_id, a.season, a.position, a.player_name, a.consensus_adp
    FROM adp_consensus a
    JOIN career_appearances c ON c.player_id = a.gsis_id
    WHERE a.consensus_adp IS NOT NULL
        AND a.position IN {_SKILL_POSITIONS}
        AND a.season <= (SELECT MAX(season) FROM weekly_stats)
)
SELECT
    l.league_key,
    adp.season,
    adp.player_id,
    adp.player_name,
    adp.position,
    adp.consensus_adp,
    COALESCE(por.league_points, 0.0) AS league_points
FROM adp
CROSS JOIN (SELECT league_key FROM league_settings) l
LEFT JOIN points_over_replacement por
    ON por.player_id = adp.player_id AND por.season = adp.season AND por.league_key = l.league_key
ORDER BY l.league_key, adp.season, adp.consensus_adp
"""


def _lineup(league: pd.Series, variant: str) -> dict:
    """Starting slots, draft length and roster caps for one league under one variant.

    The superflex variant converts a *bench* spot into a superflex slot rather than adding one, so
    both variants draft the same number of rounds and the comparison isn't confounded by roster size.
    """
    slots = {pos: int(league[f"{pos.lower()}_slots"]) for pos in _POSITIONS}
    flex = int(league["flex_slots"])
    superflex = int(league["superflex_slots"])
    bench = int(league["bench_slots"])
    if variant == "superflex":
        superflex += 1
        bench -= 1

    caps = dict(_ROSTER_CAPS)
    if superflex:
        caps["QB"] = _SUPERFLEX_QB_CAP
    return {
        "slots": slots,
        "flex": flex,
        "superflex": superflex,
        # Skill starters plus bench. K/DST/P are not drafted here, so their slots are simply gone.
        "rounds": sum(slots.values()) + flex + superflex + bench,
        "caps": np.array([caps[pos] for pos in _POSITIONS], dtype=np.int16),
        "starters": np.array([slots[pos] for pos in _POSITIONS], dtype=np.int16),
    }


def _label(quota: np.ndarray) -> str:
    return "".join(f"{quota[code]}{pos}" for code, pos in enumerate(_POSITIONS) if quota[code])


def _compositions() -> list[tuple[str, str, np.ndarray | None]]:
    """(label, kind, quota) for the control, every realistic 5-round composition, and the orderings.

    `quota` is a length-4 count vector for a composition, `None` for the pure-ADP control. Orderings
    carry their sequence separately — see `_orderings`.
    """
    strategies: list[tuple[str, str, np.ndarray | None]] = [("ADP", "control", None)]
    for combo in itertools.combinations_with_replacement(_POSITIONS, _OPENING_ROUNDS):
        counts = Counter(combo)
        if any(counts[pos] > _MAX_OPENING[pos] for pos in _POSITIONS):
            continue
        quota = np.array([counts[pos] for pos in _POSITIONS], dtype=np.int16)
        strategies.append((_label(quota), "composition", quota))
    return strategies


def _orderings() -> list[tuple[str, tuple[int, ...]]]:
    """Every distinct permutation of the two headline openings, as position-code sequences.

    3RB+2WR is the "full house" claim and 2RB+3WR its mirror; running both means the ordering
    question is answered on a receiver-leaning opening too, rather than only where the video said
    to look.
    """
    sequences = []
    for combo in (("RB", "RB", "RB", "WR", "WR"), ("RB", "RB", "WR", "WR", "WR")):
        for permutation in sorted(set(itertools.permutations(combo))):
            sequences.append((
                "-".join(permutation),
                tuple(_POSITION_CODE[pos] for pos in permutation),
            ))
    return sequences


def _score_lineup(counts_pts: list[list[float]], lineup: dict) -> float:
    """Hindsight-optimal starting lineup total from one team's drafted roster.

    FLEX takes RB/WR/TE only; the superflex slot additionally accepts a quarterback. Filling the
    restrictive slot first and the permissive one from what's left is optimal here precisely because
    superflex eligibility is a superset of flex eligibility.
    """
    slots = lineup["slots"]
    total = 0.0
    for code, pos in enumerate(_POSITIONS):
        counts_pts[code].sort(reverse=True)
        total += sum(counts_pts[code][: slots[pos]])

    flex_pool = sorted(
        (points for code in _FLEX_CODES for points in counts_pts[code][slots[_POSITIONS[code]]:]),
        reverse=True,
    )
    total += sum(flex_pool[: lineup["flex"]])
    if lineup["superflex"]:
        leftover = flex_pool[lineup["flex"]:] + counts_pts[_QB][slots["QB"]:]
        leftover.sort(reverse=True)
        total += sum(leftover[: lineup["superflex"]])
    return total


def _draft(
    board_position: np.ndarray,
    board_points: np.ndarray,
    position_index: list[np.ndarray],
    lineup: dict,
    n_teams: int,
    quotas: np.ndarray,
    sequences: dict[int, tuple[int, ...]],
) -> list[float]:
    """Run one snake draft and return every team's starting-lineup total.

    `quotas` is (n_teams, 4) of remaining opening-round obligations — all zero for a team drafting
    pure ADP. `sequences` maps a team to a forced position order, which overrides its quota row.
    """
    taken = np.zeros(len(board_position), dtype=bool)
    pointer = [0] * len(_POSITIONS)
    counts = np.zeros((n_teams, len(_POSITIONS)), dtype=np.int16)
    rosters: list[list[list[float]]] = [
        [[] for _ in _POSITIONS] for _ in range(n_teams)
    ]
    caps, starters = lineup["caps"], lineup["starters"]
    rounds = lineup["rounds"]

    def best_available(code: int) -> int:
        """Index of the highest-ADP player left at a position, or -1. The pointer only ever moves
        forward, so the whole draft costs one pass over each position's board."""
        board = position_index[code]
        index = pointer[code]
        while index < len(board) and taken[board[index]]:
            index += 1
        pointer[code] = index
        return board[index] if index < len(board) else -1

    for rnd in range(rounds):
        order = range(n_teams) if rnd % 2 == 0 else range(n_teams - 1, -1, -1)
        for team in order:
            under_cap = counts[team] < caps
            # With as many picks left as starting slots still empty, stop taking best-available and
            # fill the holes — otherwise a team can finish the draft without a quarterback and score
            # a zero that has nothing to do with the strategy being tested.
            unfilled = np.maximum(starters - counts[team], 0)
            if rounds - rnd <= int(unfilled.sum()):
                candidates = np.flatnonzero(under_cap & (unfilled > 0))
            elif rnd < _OPENING_ROUNDS and team in sequences:
                forced = sequences[team][rnd]
                candidates = np.array([forced]) if under_cap[forced] else np.flatnonzero(under_cap)
            elif rnd < _OPENING_ROUNDS and quotas[team].any():
                candidates = np.flatnonzero(under_cap & (quotas[team] > 0))
                if not len(candidates):
                    candidates = np.flatnonzero(under_cap)
            else:
                candidates = np.flatnonzero(under_cap)

            pick, picked_code = -1, -1
            for code in candidates:
                index = best_available(int(code))
                if index >= 0 and (pick < 0 or index < pick):
                    pick, picked_code = index, int(code)
            if pick < 0:
                continue

            taken[pick] = True
            counts[team, picked_code] += 1
            if rnd < _OPENING_ROUNDS and quotas[team, picked_code] > 0:
                quotas[team, picked_code] -= 1
            rosters[team][picked_code].append(board_points[pick])

    return [_score_lineup(roster, lineup) for roster in rosters]


def _sweep(
    board: pd.DataFrame,
    league: pd.Series,
    variant: str,
    field_model: str,
    strategies: list[tuple[str, str, np.ndarray | None]],
    orderings: list[tuple[str, tuple[int, ...]]],
) -> pd.DataFrame:
    """Every (season x strategy x draft slot) draft for one league under one variant and field."""
    lineup = _lineup(league, variant)
    n_teams = int(league["team_count"])
    opening_pool = [q for _, kind, q in strategies if kind == "composition"]
    replicates = _MIXED_REPLICATES if field_model == "mixed" else 1

    rows = []
    for season, season_board in board.groupby("season"):
        board_position = season_board["position_code"].to_numpy()
        board_points = season_board["league_points"].to_numpy()
        position_index = [np.flatnonzero(board_position == code) for code in range(len(_POSITIONS))]

        for replicate in range(replicates):
            # Seeded on the season and replicate but not on the strategy, so every strategy meets
            # the identical field — the comparison between them is then paired, not just averaged.
            rng = np.random.default_rng((_SEED, int(season), replicate))
            field = (
                np.stack([opening_pool[i] for i in rng.integers(len(opening_pool), size=n_teams)])
                if field_model == "mixed"
                else np.zeros((n_teams, len(_POSITIONS)), dtype=np.int16)
            )

            runs = [(label, kind, quota, None) for label, kind, quota in strategies]
            if field_model == "adp":
                runs += [(label, "ordering", None, sequence) for label, sequence in orderings]

            for label, kind, quota, sequence in runs:
                for draft_slot in range(n_teams):
                    quotas = field.copy()
                    quotas[draft_slot] = (
                        np.zeros(len(_POSITIONS), dtype=np.int16) if quota is None else quota
                    )
                    scores = _draft(
                        board_position, board_points, position_index, lineup, n_teams,
                        quotas, {draft_slot: sequence} if sequence else {},
                    )
                    focal = scores[draft_slot]
                    field_mean = (sum(scores) - focal) / (n_teams - 1)
                    rows.append((
                        league["league_key"], variant, field_model, replicate, int(season),
                        label, kind, draft_slot + 1, n_teams, focal, field_mean,
                        focal - field_mean, 1 + sum(1 for s in scores if s > focal),
                    ))

    return pd.DataFrame(rows, columns=_RESULT_COLUMNS)


def _summarize(results: pd.DataFrame) -> pd.DataFrame:
    """Collapse the simulated drafts to one row per strategy, with a season-clustered t-test.

    The t-test runs on the eleven *per-season* means rather than the ~1,500 individual drafts,
    because drafts within a season share the same player outcomes: one running back busting moves
    every RB-heavy draft that year together. Treating those as independent would divide the standard
    error by roughly the number of draft slots and turn every strategy significant.
    """
    keys = ["league_key", "variant", "field_model", "strategy", "strategy_kind"]
    by_season = (
        results.groupby([*keys, "season"])["points_vs_field"].mean().reset_index()
    )
    # Top third rather than a fixed rank, so a 10-team and a 12-team league are on one scale.
    results = results.assign(
        top_third=results["finish_rank"] <= np.ceil(results["team_count"] / 3)
    )

    tests = []
    for key, group in by_season.groupby(keys):
        values = group["points_vs_field"].to_numpy()
        if len(values) > 1 and values.std() > 0:
            t_stat, p_value = stats.ttest_1samp(values, 0.0)
        else:
            t_stat, p_value = np.nan, np.nan
        tests.append((*key, len(values), int((values > 0).sum()), t_stat, p_value))
    tests = pd.DataFrame(tests, columns=[*keys, "n_seasons", "seasons_positive", "t_stat",
                                         "p_value"])

    summary = results.groupby(keys).agg(
        n_drafts=("starter_points", "size"),
        starter_points=("starter_points", "mean"),
        points_vs_field=("points_vs_field", "mean"),
        finish_rank=("finish_rank", "mean"),
        win_rate=("finish_rank", lambda ranks: float((ranks == 1).mean())),
        top_third_rate=("top_third", "mean"),
    ).reset_index()
    return summary.merge(tests, on=keys)[_SUMMARY_COLUMNS]


@console.analysis
def _report(summary: pd.DataFrame) -> None:
    for league_key in sorted(summary["league_key"].unique()):
        for variant in ("actual", "superflex"):
            block = summary[
                (summary["league_key"] == league_key)
                & (summary["variant"] == variant)
                & (summary["field_model"] == "adp")
                & (summary["strategy_kind"] != "ordering")
            ].sort_values("points_vs_field", ascending=False)
            if block.empty:
                continue
            print(f"\n{league_key} / {variant} — opening composition vs a pure-ADP field")
            print(f"  {'strategy':<14} {'vs field':>9} {'rank':>6} {'win%':>6} {'t':>6} "
                  f"{'seasons+':>9}")
            shown = pd.concat([block.head(6), block[block["strategy_kind"] == "control"],
                               block.tail(3)]).drop_duplicates(subset="strategy")
            for _, row in shown.iterrows():
                print(f"  {row['strategy']:<14} {row['points_vs_field']:>9.1f} "
                      f"{row['finish_rank']:>6.2f} {100 * row['win_rate']:>6.1f} "
                      f"{row['t_stat']:>6.2f} {row['seasons_positive']:>4}/{row['n_seasons']}")


def build_draft_strategy() -> None:
    con = duckdb.connect(str(WAREHOUSE_PATH))
    board = con.execute(_BOARD_SQL).df()
    leagues = con.execute("SELECT * FROM league_settings").df()
    con.close()

    board["position_code"] = board["position"].map(_POSITION_CODE).astype(np.int16)
    strategies = _compositions()
    orderings = _orderings()

    frames = [
        _sweep(board[board["league_key"] == league["league_key"]], league, variant, field_model,
               strategies, orderings)
        for _, league in leagues.iterrows()
        for variant in ("actual", "superflex")
        for field_model in ("adp", "mixed")
    ]
    results = pd.concat(frames, ignore_index=True)
    summary = _summarize(results)

    _report(summary)

    con = duckdb.connect(str(WAREHOUSE_PATH))
    con.execute("CREATE OR REPLACE TABLE draft_strategy_results AS SELECT * FROM results")
    con.execute("CREATE OR REPLACE TABLE draft_strategy_summary AS SELECT * FROM summary")
    con.close()

    console.table("draft_strategy_results", len(results))
    console.table("draft_strategy_summary", len(summary))


if __name__ == "__main__":
    build_draft_strategy()
