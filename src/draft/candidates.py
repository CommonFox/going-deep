"""Subtract the players already taken from the board and rank what is left.

The second half of the live draft assistant's single seam, and pure in the same way the first
half is: no network, no warehouse connection, no printing, and nothing handed in is modified.
`picks.ingest_picks` turns a payload into who is gone; this turns who is gone into who to take.

## It is a filter and a sort, and that is the entire point

Every number here was computed by the warehouse rebuild days before the draft. Nothing is
recalculated on the night, and `points_over_replacement` in particular comes back exactly as
the board priced it.

That rule is worth stating because the tempting alternative is a *good* idea, not an obvious
mistake. Replacement level genuinely moves during a draft — thirty backs go, so the best back
still available is a worse player than he was at pick one — and the tool knows exactly who has
gone, so recomputing against the survivors looks like free accuracy.

It is not, for two reasons:

- Replacement level means *freely available*, which is a season-long fact. By kickoff roughly
  210 players are rostered in a 14-team league, and that end state is knowable before a single
  pick is made — it is what the board is already computed against. The 47 players gone at pick
  47 are a transient state that matches nothing. Recomputing would swap an accurate number for
  a temporarily wrong one.
- A number computed once, in memory, during a live draft cannot be checked against anything.
  The board's value came from a pipeline that can be re-run and read; and when the tool and the
  printed board disagree by seven points at pick eleven, the drafter has no way to tell which
  is right and stops trusting both.

The real version of that instinct — what a player is worth *to a roster whose slots are partly
filled*, where a third back is worth less than a first — is a different question, is materially
more machinery, and is deliberately out of scope for this feature.

## Ranking, for now

By points over replacement alone, highest first. Cost of waiting replaces this rule later and
changes only the ordering: what comes back, and the promise that its values are the board's own,
are fixed here.

Ties break by name. The spec left them undecided, and left to itself a sort would order equal
values however the board happened to arrive, which is not stable across a rebuild. Alphabetical
is arbitrary but fixed, and because the values are on screen a reader can see that a tie is what
it is rather than mistaking it for a judgement.
"""

import pandas as pd

# What a candidate is, as far as every surface above here is concerned. The board carries thirty
# or so columns; a candidate is the four that identify and price a player, and no more. The
# `player_id` is not for display — it is how the survival probabilities join on when cost of
# waiting arrives, and how anything downstream refers to a player without going through a name.
CANDIDATE_COLUMNS = ["player_id", "player_name", "position", "team", "points_over_replacement"]


def rank_candidates(
    board: pd.DataFrame, taken: set[str], position: str | None = None
) -> pd.DataFrame:
    """The players still available, best first.

    `taken` is the board's own player IDs, exactly as `ingest_picks` returns them — not the
    picks payload, which has already been read once and should not be interpreted twice.
    `position` narrows to one position when asked, and is matched exactly against the board's
    own values rather than being normalized here, so that the pure function has no opinion a
    caller has to know about.

    Returns one row per available player with `CANDIDATE_COLUMNS`, in ranked order, always with
    those columns even when nobody is left.
    """
    available = board.loc[~board["player_id"].isin(taken)]
    if position is not None:
        available = available.loc[available["position"] == position]

    # `.loc` with a column list already copies, so the board itself is never touched by the
    # sort; being explicit costs nothing and makes that guarantee readable rather than inferred.
    return (
        available[CANDIDATE_COLUMNS]
        .copy()
        .sort_values(
            ["points_over_replacement", "player_name"], ascending=[False, True]
        )
        .reset_index(drop=True)
    )
