"""Work out who will still be there at each of your picks, and what opening plan to spend them on.

Pure warehouse-to-warehouse Python/SQL — no fetch step, no network. Built from `draft_board` and
`league_settings`, both already built.

`draft_board` prices players in isolation: what each one is worth above a freely-available
replacement. That is only half of a draft decision. The other half is supply — a player worth 180
points is worth nothing to you if he is gone fourteen picks before your next turn, and the whole
skill of drafting from a fixed seat is knowing which of two players is likelier to survive the gap.

## The seat is the thing

A snake draft gives each slot a completely different shape. From the 1.01 in a 14-team league your
picks are 1, then 28 and 29 back to back, then 56 and 57, and so on: one pick at the very top and
then seven *pairs*. From the middle you get an even drip. Nobody drafting from the turn should be
reasoning about "my next pick" as a single event, because it isn't one — which is why every number
here is computed per (slot, pick) rather than per player.

## Where the probabilities come from

FFC publishes, for each player, the mean draft position across thousands of real drafts in this
exact format, plus the standard deviation and the earliest and latest he actually went. So `adp` is
a mean of realized draft positions and `adp_stdev` is their dispersion — which makes
`P(still available at pick k)` a straightforward tail probability rather than a modelling choice:
the player is gone by pick k exactly when his draft position lands before it.

A normal centred on ADP is used for that tail, clipped to the observed `[high, low]` range so the
model never claims a player might be available past the latest he has ever actually lasted. The
known distortion is skew: a player can only rise so far above his ADP but can fall a long way
below it (Ja'Marr Chase, ADP 7.9, has gone as early as 1 and as late as 26), so a symmetric
distribution slightly understates how often the good ones fall to you. Read a borderline
"he might be there" as a little more likely than the number says, not less.

These are **marginal** probabilities, one player at a time. They do not encode that exactly one
player can be taken with each pick, so they will not sum to anything meaningful across a position —
`P(Allen available) = 0.4` and `P(Maye available) = 0.6` does not mean you get one of them with
probability 1.0. Joint behaviour is what the draft simulation is for; these numbers are for
reading a board, where "how likely is this specific name to reach me" is the actual question.

## Pricing a plan rather than a player

The second table asks the question a board cannot: not "what is this player worth" but "what is
this *opening* worth from this seat". A plan constrains only the first five rounds — three backs
and two receivers, or two quarterbacks and three backs — and from round six on every team takes
the best player left. It is scored on the whole roster it ends up with, not on those five picks,
because five picks can't see what they cost: opening with two quarterbacks leaves a thin running
back room, and a five-pick total would never charge you for it.

Each simulated draft samples every player's draft position from his own ADP distribution and sorts
the board by the result, so the room behaves differently every time and a plan has to survive the
spread rather than one tidy ordering. Opponents draft that sampled board straight, with no added
roster-need logic — deliberately, because the 2QB board is already measured from thousands of real
superflex drafts, so the quarterback run is priced into it. Layering need on top would count the
same behaviour twice and make quarterbacks disappear even earlier than they really do.

The simulator itself is `draft_strategy`'s, reused rather than rebuilt: same snake, same roster
caps, same hindsight-optimal lineup scoring, same treatment of the superflex slot as a superset of
flex eligibility. The only differences are that the board is projected rather than historical, the
order is sampled rather than fixed, and the focal seat is a specific slot rather than every slot
averaged together.
"""

from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
from scipy import stats

from src import console
from src.gold.draft_strategy import (
    _POSITION_CODE,
    _POSITIONS,
    _actual_variant,
    _compositions,
    _draft,
    _lineup,
)

# Resolved from this file rather than the working directory, unlike the other gold modules'
# relative `Path("data/warehouse.duckdb")`. Those only ever run as `python -m src...` from the repo
# root, but `simulate_first_pick` is meant to be called from a notebook, whose working directory
# depends on where the kernel started — the same reasoning as src/query.py.
WAREHOUSE_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "warehouse.duckdb"

# Below this the number is noise dressed as precision — a player 60 picks past his ADP is "gone",
# and carrying four decimal places of a 0.001 tail probability for every player at every pick just
# makes the table big and the board harder to read.
_MINIMUM_PROBABILITY = 0.01

# Some players carry an ADP with no dispersion (a single-source price, or a board where everyone
# took them at the same spot). Treating that as certainty would make them either guaranteed present
# or guaranteed gone, so they get a floor wide enough to stay honest.
_MINIMUM_STDEV = 1.0

_OUTPUT_COLUMNS = [
    "league_key", "draft_slot", "round", "overall_pick", "player_id", "player_name", "position",
    "consensus_adp", "points_over_replacement", "p_available",
]

# Simulated drafts per (league, slot, plan). The spread across plans is a few points wide, so this
# has to be large enough that the standard error of a plan's mean is well under that gap.
_TRIALS = 300

# Fixed so a rebuild reproduces byte-for-byte, and shared across plans within a trial so every plan
# meets the identical room — the comparison between plans is then paired rather than just averaged.
_SEED = 20260903

_PLAN_COLUMNS = [
    "league_key", "draft_slot", "plan", "trials", "starter_points", "points_vs_field",
    "finish_rank", "win_rate", "top_third_rate",
]


def _snake_picks(team_count: int, rounds: int, draft_slot: int) -> pd.DataFrame:
    """Every overall pick number one seat owns, in a snake draft.

    Odd rounds run 1..N from the front, even rounds run back the other way, which is what turns
    the ends of the board into back-to-back pairs and the middle into an even drip.
    """
    picks = []
    for draft_round in range(1, rounds + 1):
        position_in_round = (
            draft_slot if draft_round % 2 == 1 else team_count - draft_slot + 1
        )
        picks.append(
            {
                "round": draft_round,
                "overall_pick": (draft_round - 1) * team_count + position_in_round,
            }
        )
    return pd.DataFrame(picks)


def _probability_available(board: pd.DataFrame, overall_pick: int) -> pd.Series:
    """P(this player's draft position lands at or after `overall_pick`).

    The half-pick offset is a continuity correction: draft positions are whole numbers and the
    normal is continuous, so "at or after pick k" is the tail beyond k - 0.5.
    """
    spread = board["adp_stdev"].fillna(_MINIMUM_STDEV).clip(lower=_MINIMUM_STDEV)
    probability = stats.norm.sf(overall_pick - 0.5, loc=board["consensus_adp"], scale=spread)

    # Nobody has ever gone earlier than `adp_high` or later than `adp_low` across thousands of
    # drafts, so the model shouldn't invent either tail beyond what was actually observed.
    probability = pd.Series(probability, index=board.index)
    probability = probability.mask(board["adp_high"] >= overall_pick, 1.0)
    probability = probability.mask(board["adp_low"] < overall_pick, 0.0)
    return probability


def _availability_for_league(
    con: duckdb.DuckDBPyConnection, league: pd.Series, rounds: int
) -> pd.DataFrame:
    board = con.execute(
        """
        SELECT player_id, player_name, position, consensus_adp, adp_stdev, adp_high, adp_low,
               points_over_replacement
        FROM draft_board
        WHERE league_key = ? AND consensus_adp IS NOT NULL
        """,
        [league["league_key"]],
    ).df()

    team_count = int(league["team_count"])
    frames = []
    for draft_slot in range(1, team_count + 1):
        for _, pick in _snake_picks(team_count, rounds, draft_slot).iterrows():
            available = board.copy()
            available["p_available"] = _probability_available(board, pick["overall_pick"])
            available = available[available["p_available"] >= _MINIMUM_PROBABILITY]
            available["draft_slot"] = draft_slot
            available["round"] = pick["round"]
            available["overall_pick"] = pick["overall_pick"]
            frames.append(available)

    df = pd.concat(frames, ignore_index=True)
    df["league_key"] = league["league_key"]
    return df[_OUTPUT_COLUMNS]


def _simulation_board(con: duckdb.DuckDBPyConnection, league: pd.Series) -> pd.DataFrame:
    """The drafted universe: skill players carrying both a market price and a projection.

    Kickers and defenses are left out along with their roster spots, matching `draft_strategy` —
    no plan question turns on when you take a kicker, and including them would only add rounds in
    which every team does the same thing.
    """
    board = con.execute(
        f"""
        SELECT player_id, player_name, position, consensus_adp, adp_stdev, adp_high, adp_low,
               projected_points_adjusted
        FROM draft_board
        WHERE league_key = ? AND consensus_adp IS NOT NULL
          AND projected_points_adjusted IS NOT NULL
          AND position IN {_POSITIONS}
        """,
        [league["league_key"]],
    ).df()
    board["position_code"] = board["position"].map(_POSITION_CODE)
    return board


def _sampled_order(board: pd.DataFrame, rng: np.random.Generator) -> np.ndarray:
    """One plausible draft room: each player's position drawn from his own ADP distribution.

    Clipped to the earliest and latest he has actually gone, so a draw can't invent a room where
    the consensus 1.01 lasts to the third round.
    """
    spread = board["adp_stdev"].fillna(_MINIMUM_STDEV).clip(lower=_MINIMUM_STDEV).to_numpy()
    drawn = rng.normal(board["consensus_adp"].to_numpy(), spread)
    # A player priced by a source that publishes no range (FantasyPros' blended ADP) has no
    # observed bounds to clip to, so his draw is left unconstrained rather than pinned to NaN.
    earliest = board["adp_high"].fillna(-np.inf).to_numpy()
    latest = board["adp_low"].fillna(np.inf).to_numpy()
    return np.argsort(np.clip(drawn, earliest, latest), kind="stable")


def _simulate_slot(
    board: pd.DataFrame, league: pd.Series, draft_slot: int, trials: int
) -> pd.DataFrame:
    """Every opening plan, drafted `trials` times from one seat against a resampled room."""
    lineup = _lineup(league, _actual_variant(league))
    n_teams = int(league["team_count"])
    plans = _compositions()
    codes = board["position_code"].to_numpy()
    points = board["projected_points_adjusted"].to_numpy()

    rows = []
    for trial in range(trials):
        # Seeded on the seat and trial but not the plan, so within a trial every plan faces the
        # identical room and the comparison between them is paired.
        rng = np.random.default_rng((_SEED, draft_slot, trial))
        order = _sampled_order(board, rng)
        board_position, board_points = codes[order], points[order]
        position_index = [
            np.flatnonzero(board_position == code) for code in range(len(_POSITIONS))
        ]

        for label, kind, quota in plans:
            quotas = np.zeros((n_teams, len(_POSITIONS)), dtype=np.int16)
            if quota is not None:
                quotas[draft_slot - 1] = quota
            totals = _draft(
                board_position, board_points, position_index, lineup, n_teams, quotas, {}
            )
            mine = totals[draft_slot - 1]
            field = (sum(totals) - mine) / (n_teams - 1)
            rows.append(
                {
                    "draft_slot": draft_slot,
                    "plan": label if kind == "composition" else "best available",
                    "starter_points": mine,
                    "points_vs_field": mine - field,
                    "finish_rank": 1 + sum(1 for total in totals if total > mine),
                }
            )
    return pd.DataFrame(rows)


def simulate_first_pick(
    league_key: str,
    draft_slot: int,
    openings: dict[str, list[str]],
    trials: int = _TRIALS,
) -> pd.DataFrame:
    """Compare named first picks head to head, each followed by a stated opening plan.

    `openings` maps a player's name to the five-position sequence to draft after taking him, e.g.
    `{"Jahmyr Gibbs": ["RB", "QB", "QB", "RB", "TE"]}`. Every candidate is run through the *same*
    sampled draft rooms, so the comparison is paired and the difference between two candidates is
    measured room by room rather than by averaging two independent samples.

    Forcing the pick works by pulling the player to the front of that room's board. The focal team
    only ever picks first when it holds seat 1, so this is exact there and approximate elsewhere —
    from any other seat the player might not have lasted to the pick at all, which is what
    `draft_availability` is for.

    Returns one row per (trial, candidate), so the caller can pair, difference and test them.
    """
    con = duckdb.connect(str(WAREHOUSE_PATH), read_only=True)
    league = con.execute(
        "SELECT * FROM league_settings WHERE league_key = ?", [league_key]
    ).df().iloc[0]
    board = _simulation_board(con, league)
    con.close()

    lineup = _lineup(league, _actual_variant(league))
    n_teams = int(league["team_count"])
    codes = board["position_code"].to_numpy()
    points = board["projected_points_adjusted"].to_numpy()

    rows = []
    for trial in range(trials):
        rng = np.random.default_rng((_SEED, draft_slot, trial))
        order = _sampled_order(board, rng)

        for name, sequence in openings.items():
            matches = board.index[board["player_name"] == name]
            if not len(matches):
                raise KeyError(f"{name} is not on {league_key}'s board")
            forced = np.concatenate(([matches[0]], order[order != matches[0]]))
            board_position, board_points = codes[forced], points[forced]
            position_index = [
                np.flatnonzero(board_position == code) for code in range(len(_POSITIONS))
            ]
            totals = _draft(
                board_position,
                board_points,
                position_index,
                lineup,
                n_teams,
                np.zeros((n_teams, len(_POSITIONS)), dtype=np.int16),
                {draft_slot - 1: tuple(_POSITION_CODE[pos] for pos in sequence)},
            )
            mine = totals[draft_slot - 1]
            rows.append(
                {
                    "trial": trial,
                    "player_name": name,
                    "plan": "".join(sequence),
                    "starter_points": mine,
                    "points_vs_field": mine - (sum(totals) - mine) / (n_teams - 1),
                    "finish_rank": 1 + sum(1 for total in totals if total > mine),
                }
            )
    return pd.DataFrame(rows)


def _summarize_plans(results: pd.DataFrame, n_teams: int) -> pd.DataFrame:
    """One row per (seat, plan): how it did across every room it was drafted into."""
    grouped = results.groupby(["draft_slot", "plan"], as_index=False).agg(
        trials=("starter_points", "size"),
        starter_points=("starter_points", "mean"),
        points_vs_field=("points_vs_field", "mean"),
        finish_rank=("finish_rank", "mean"),
        win_rate=("finish_rank", lambda ranks: (ranks == 1).mean()),
        top_third_rate=("finish_rank", lambda ranks: (ranks <= n_teams / 3).mean()),
    )
    return grouped


def build_draft_plans() -> None:
    con = duckdb.connect(str(WAREHOUSE_PATH))
    leagues = con.execute("SELECT * FROM league_settings").df()

    frames = []
    for _, league in leagues.iterrows():
        board = _simulation_board(con, league)
        n_teams = int(league["team_count"])
        results = pd.concat(
            [_simulate_slot(board, league, slot, _TRIALS) for slot in range(1, n_teams + 1)],
            ignore_index=True,
        )
        summary = _summarize_plans(results, n_teams)
        summary["league_key"] = league["league_key"]
        frames.append(summary)

    plans = pd.concat(frames, ignore_index=True)[_PLAN_COLUMNS]
    con.execute("CREATE OR REPLACE TABLE draft_plans AS SELECT * FROM plans")
    (count,) = con.execute("SELECT COUNT(*) FROM draft_plans").fetchone()
    con.close()

    console.table("draft_plans", count)


def build_draft_availability() -> None:
    con = duckdb.connect(str(WAREHOUSE_PATH))
    leagues = con.execute("SELECT * FROM league_settings").df()

    frames = []
    for _, league in leagues.iterrows():
        # One round per roster spot: the draft fills the whole roster, bench included.
        rounds = int(
            league[["qb_slots", "rb_slots", "wr_slots", "te_slots", "flex_slots",
                    "superflex_slots", "k_slots", "p_slots", "dst_slots", "bench_slots"]].sum()
        )
        frames.append(_availability_for_league(con, league, rounds))

    availability = pd.concat(frames, ignore_index=True)
    con.execute("CREATE OR REPLACE TABLE draft_availability AS SELECT * FROM availability")
    (count,) = con.execute("SELECT COUNT(*) FROM draft_availability").fetchone()
    con.close()

    console.table("draft_availability", count)


if __name__ == "__main__":
    build_draft_availability()
    build_draft_plans()
