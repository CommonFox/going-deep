"""Let the drafter say a player is gone, when Sleeper cannot say it for him.

The fallback for the night the network drops or the API stalls: a name typed at the running tool
takes that player off the board, and the board stays correct without a single successful poll.
Pure, like everything else in this package bar `live` — a string and two frames in, a decision out.

## This is the one place identity is resolved by name, and it is bounded on both sides

The rule everywhere else is identity by ID: picks carry Sleeper's, the board carries the
warehouse's, and the crosswalk between them was built days ago. A drafter typing at a pick clock
cannot supply either. So a name is resolved against the board exactly once, here, and what comes
out is a board row carrying its own IDs — nothing downstream ever sees the string.

Which makes this function's real job refusing. Marking the wrong player hides somebody who is
genuinely available, and that is invisible on screen: a board missing a player looks exactly like
a board where somebody took him. So an unknown name and an ambiguous one are both refused, named,
and never resolved to whichever row the board happened to list first.

## Every outcome carries a line for the screen

The drafter is typing at a status line that repaints every three seconds. A mark that quietly did
nothing would be indistinguishable from a mark that worked, so there is no silent branch here at
all — accepted, refused and redundant all come back with something to print.

## Matching, and why part of a name is enough

Nobody types `Amon-Ra St. Brown` correctly while a pick clock runs, so punctuation is dropped from
both sides and a substring of a name is accepted when exactly one player has it. `swift` is a
mark; `allen` is a refusal naming the three of them.

An exact match wins over a substring even when both are found, which is what keeps `Michael
Carter` typeable on a board that also carries `Michael Carter II` — treating that as ambiguous
would make the shorter name unmarkable for the whole draft.

## Why a mark is unioned into the picks payload rather than kept beside it

A mark becomes a pick entry with no pick number and no roster, and is appended to what the API
returned. `picks.ingest_picks` then does the rest: it deduplicates on the player, so the moment
Sleeper catches up there is one player rather than two, and it reads the draft's progress from
pick numbers, so a hand-mark never pushes the seat's next turn a pick further away. Both of those
defences were written for this, and this is the only thing that exercises them.

The API's entries are listed first deliberately. Deduplication keeps the first one seen, so a real
pick always wins over the hand-mark it catches up with — the roster fills from the manager who
actually drafted the player, at the number he actually went.

A mark therefore says a player is *gone*, never whose he is. There is no way to claim one for my
own roster, and that is the point: a mark is a statement about supply, made under exactly the
conditions where the drafter is least able to check it, and a mistyped one that filled my lineup
would be a wrong answer to the question the tool exists to answer.

## The one player who cannot be marked

A board row whose `sleeper_id` is missing is refused. Its mark could not be told apart from the
player's own pick when it arrived, so it would union with nothing, remove nobody, and report
itself as an unmatched pick — a mark that appears to work and does not. Issue #31's build-time
report exists to close exactly that gap days beforehand, with an identity override, and the
message says so rather than leaving the drafter to guess why.
"""

import re

import pandas as pd

# What a mark carries: the two identities, and the three fields that name a player on screen.
MARK_COLUMNS = ["player_id", "sleeper_id", "player_name", "position", "team"]

# Typed in front of a name to take a mark back. A leading dash rather than a word because it is
# the fewest keystrokes that cannot be the start of a player's name.
UNMARK = "-"

# How many players an ambiguous refusal names before it stops. Enough to choose from, few enough
# to read in one glance at a line that is about to be written over.
MAX_NAMED = 6


def _plain(name) -> str:
    """A name reduced to what somebody would actually type: lowercase, unpunctuated, single-spaced.

    Hyphens and slashes become spaces and everything else non-alphanumeric is dropped, so
    `Amon-Ra St. Brown` reduces to `amon ra st brown` and `D'Andre Swift` to `dandre swift`. Both
    sides of every comparison go through this, so the board's spelling of a name never has to be
    reproduced exactly.
    """
    text = re.sub(r"[-–/]", " ", str(name).lower())
    return " ".join(re.sub(r"[^\w ]", "", text).split())


def _rows(board: pd.DataFrame) -> list[dict]:
    """The board as plain rows, cut to what a mark carries. The frame itself is never touched."""
    return board[MARK_COLUMNS].to_dict("records")


def _matching(typed: str, rows: list[dict]) -> list[dict]:
    """Every row a typed name could mean — the exact ones if there are any, else the partial ones.

    Returning the exact matches alone when they exist is what stops a whole name being ambiguous
    with the longer names it is the start of.
    """
    exact = [row for row in rows if _plain(row["player_name"]) == typed]
    return exact or [row for row in rows if typed in _plain(row["player_name"])]


def _named(player: dict) -> str:
    """One player, as a refusal or a confirmation names him: enough to tell two Allens apart."""
    return f"{player['player_name']} ({player['position']}, {player['team']})"


def _listed(players: list[dict]) -> str:
    """Several players, named, cut off before the line becomes something nobody reads."""
    shown = ", ".join(_named(player) for player in players[:MAX_NAMED])
    if len(players) <= MAX_NAMED:
        return shown
    return f"{shown} and {len(players) - MAX_NAMED} more"


def _nothing(message: str) -> dict:
    """Nothing to do, and the reason, which always goes on the screen."""
    return {"action": "none", "player": None, "message": message}


def _mark(typed: str, board: pd.DataFrame, marked: dict) -> dict:
    """Resolve a typed name against the board, or refuse it saying why."""
    wanted = _plain(typed)
    if not wanted:
        return _nothing("type at least part of a player's name to mark him taken")

    found = _matching(wanted, _rows(board))
    if not found:
        return _nothing(f'no player on the board matches "{typed}" — nobody has been marked')
    if len(found) > 1:
        return _nothing(
            f'"{typed}" matches {len(found)} players: {_listed(found)} — type more of the name; '
            "nobody has been marked"
        )

    player = found[0]
    if player["player_id"] in marked:
        return _nothing(f"{player['player_name']} is already marked taken by hand")
    if pd.isna(player["sleeper_id"]) or not str(player["sleeper_id"]).strip():
        return _nothing(
            f"{player['player_name']} has no Sleeper ID on this board, so a hand-mark could not "
            "be told apart from his own pick — nobody has been marked. He needs an identity "
            "override and a rebuild, not a mark."
        )
    return {
        "action": "mark",
        "player": player,
        "message": f"marked taken by hand: {_named(player)}",
    }


def _unmark(typed: str, marked: dict) -> dict:
    """Resolve a typed name against what has been marked by hand, or refuse it saying why.

    Against the marked set rather than the board: the drafter is choosing among his own marks, and
    a player Sleeper reported is gone because he is gone. An unmark that appeared to work on him
    would be the same lie as a wrong mark, pointing the other way.
    """
    wanted = _plain(typed)
    if not wanted:
        return _nothing(f'type at least part of a name after "{UNMARK}" to take a mark back')

    found = _matching(wanted, list(marked.values()))
    if not found:
        return _nothing(f'nothing marked by hand matches "{typed}" — nothing has changed')
    if len(found) > 1:
        return _nothing(
            f'"{typed}" matches {len(found)} players marked by hand: {_listed(found)} — type more '
            "of the name; nothing has changed"
        )

    player = found[0]
    return {
        "action": "unmark",
        "player": player,
        "message": f"{player['player_name']} is no longer marked by hand",
    }


def read_mark(typed: str, board: pd.DataFrame, marked: dict) -> dict:
    """One line the drafter typed, read against the board and the marks already made.

    `marked` is the hand-marked players so far, keyed by the board's own `player_id`. Returns:

    - `action` — `"mark"` to add the player to that set, `"unmark"` to take him out of it, and
      `"none"` when there is nothing to do.
    - `player` — the board row the name resolved to, or None when it resolved to nothing.
    - `message` — always present, and always worth printing: a refusal that is not read is a mark
      the drafter believes he made.
    """
    text = typed.strip()
    if text.startswith(UNMARK):
        return _unmark(text[len(UNMARK):].strip(), marked)
    return _mark(text, board, marked)


def as_picks(marked) -> list[dict]:
    """The hand-marked players as picks payload entries, in the shape Sleeper's own arrive in.

    No pick number, because the drafter knows a player is gone and not when — which is also what
    keeps a mark from advancing the draft. No roster, because a mark never claims a player for
    anybody's lineup.
    """
    entries = []
    for player in marked:
        first, _, last = str(player["player_name"]).partition(" ")
        entries.append({
            "player_id": str(player["sleeper_id"]),
            "roster_id": None,
            "pick_no": None,
            "metadata": {
                "first_name": first, "last_name": last, "position": player["position"]
            },
        })
    return entries


def combine(picks: list[dict], marked) -> list[dict]:
    """The API's picks with the hand-marked players unioned in, the API's first.

    First because `ingest_picks` keeps the first entry it sees for a player: a real pick, carrying
    its number and its roster, always wins over the hand-mark it has caught up with.
    """
    return [*picks, *as_picks(marked)]
