"""Narrow the board to one position, on a line typed at the running tool.

Pure, like everything in this package bar `live`: a string and a frame in, a decision out. Nothing
here ranks, reprices or renders — it only decides what the drafter asked to look at.

## Why this is typed rather than passed at startup

The ranking has taken a `position` argument since cost of waiting was written, and narrowing to it
changes no number: cost of waiting is measured against the whole board's remaining players, so a
filtered screen shows the same quarterbacks at the same prices as the unfiltered one. The argument
was reachable from nothing.

A command-line flag would not have fixed that. "Who is the best quarterback left" is a question
asked *at* a pick, about a board that is four picks older than the one the tool started on, and the
loop runs until Ctrl-C precisely because a session that ends at pick eleven is a session nobody
restarts in time. A filter that could only be set by restarting would be a filter nobody used.

## Precedence: a position first, a name second

The filter shares the one input channel with hand-marks, so a typed line has to be read as one or
the other. It is read as a position first, and the asymmetry is what makes that safe:

- A name cannot be read as a position. A position token is one of the handful the board itself
  carries, matched whole — `bijan` is not `RB` under any spelling.
- A position *can* be read as a name, which is what would happen without this rule: `k` is a
  substring of `Kyle Pitts`. It costs nothing, because a one- or two-letter mark matches half the
  board and `marks` already refuses an ambiguous one. The drafter loses a refusal he did not want.

Only positions the board actually carries are recognised, so the set is a fact about the frame
rather than a list typed here that a rebuild could make wrong.

## Every outcome carries a line for the screen

Same rule `marks` is written to, for a sharper reason. A hand-mark that silently failed leaves a
player on the board who is gone; a filter that silently *worked* leaves a drafter reading three
quarterbacks as the whole of what is left. Both are the screen quietly meaning something other
than what it appears to mean, so there is no silent branch here: narrowing, clearing, and the two
ways of asking for what is already on screen all come back with something to print.
"""

import pandas as pd

# Typed to put the whole board back. A word rather than a punctuation mark because `-` is already
# spent on taking a hand-mark back, and the two undo different things.
ALL = "all"


def _positions(board: pd.DataFrame) -> dict[str, str]:
    """What the drafter could type, to the board's own spelling of it.

    Read off the frame rather than listed here: the board decides what positions exist, and a list
    written in this module would be a second opinion that a rebuild could make wrong.
    """
    return {str(position).strip().lower(): position for position in board["position"].unique()}


def _nothing(message: str) -> dict:
    """Nothing to do, and the reason, which always goes on the screen."""
    return {"action": "none", "position": None, "message": message}


def read_position(typed: str, board: pd.DataFrame, position: str | None = None) -> dict | None:
    """One line the drafter typed, read as a position filter — or None if it is not one.

    `position` is what the board is narrowed to already, so that asking for what is on screen can
    say so rather than redrawing in silence. Returns None when the line is not a filter command at
    all, which is the caller's signal to read it as a name instead; otherwise:

    - `action` — `"show"` to narrow to `position`, `"clear"` to put the whole board back, and
      `"none"` when there is nothing to do.
    - `position` — the board's own spelling of the position to show, or None for the whole board.
    - `message` — always present, and always worth printing.
    """
    wanted = typed.strip().lower()
    if not wanted:
        return None

    if wanted == ALL:
        if position is None:
            return _nothing("the whole board is already showing")
        return {
            "action": "clear",
            "position": None,
            "message": "showing the whole board again",
        }

    found = _positions(board).get(wanted)
    if found is None:
        return None
    if found == position:
        return _nothing(f"{found} is already the only position showing")
    return {
        "action": "show",
        "position": found,
        "message": f"showing {found} only — type \"{ALL}\" for the whole board",
    }
