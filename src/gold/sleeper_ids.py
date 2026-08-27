"""Resolve every draft board row to the identifier a Sleeper pick will arrive carrying.

During a live draft the only thing that changes is who has been taken, and Sleeper reports that as
a player ID. Matching those picks by name is the failure mode this module exists to prevent: a
board that quietly shows a drafted player as still available costs a pick, which is worse than
having no tool at all. So identity is resolved here, days before the draft, by a build that says
out loud which players it could not map.

## The route in, and the one that looks right and isn't

Sleeper's own player export carries a `gsis_id` field, which appears to be exactly the join this
needs — the warehouse keys players on `gsis_id` throughout. It is a trap. That field is null for
most of Sleeper's player list, and joining the board through it matches **98 of 655 rows, 15%**.
Nothing about the failure is loud: the join succeeds, returns a board where six players in seven
have no ID, and the shortfall only turns up on draft night.

The mapping goes through nflverse's `ids` crosswalk instead, which covers the skill positions
completely — every running back, receiver and tight end on the board, and every quarterback anyone
would draft.

## Three routes, in precedence order

1. **Overrides.** A hand-maintained table, consulted first, for players the crosswalk has not
   caught up with. Every entry must name a player who is actually on the board; an override for
   someone who has since left it fails the build rather than sitting there mapping nothing.
2. **Defenses, by team abbreviation.** A team defense has no crosswalk row anywhere — it is not a
   player — so it resolves on its team instead. Both sides go through `normalize_team` first
   because the two conventions genuinely disagree: the board says `LA` for the Rams, following
   nfl_data_py, and Sleeper says `LAR`. What comes back is Sleeper's spelling, since that is what
   a pick will carry.
3. **The crosswalk**, for everyone else.

## Identifiers are strings, deliberately

`ids.sleeper_id` is stored as a DOUBLE, so the crosswalk hands back `4034.0` where Sleeper's API
sends `"4034"`. Compared as strings those are not equal, and the mismatch is invisible in any
printed frame. Every ID leaving this module has the float scraped off it and is a string, which is
also the only representation that can hold a defense's `LAR`.

## What "draftable" means

A player is draftable if the market has priced him (`consensus_adp`) **or** he ranks inside the
league's starter depth at his position (`position_rank <= starters_at_position`). The second arm
is not redundant: a promising rookie can have no ADP at all and still be the 5th kicker on a board
where 14 get rostered, and an ADP-only rule would drop him. Below that cut sit third-string
quarterbacks and camp-body kickers, who are counted rather than named, because a warning printed
every build about players nobody drafts is a warning that stops being read.
"""

import pandas as pd

from src import console
from src.silver.teams import normalize_team

# gsis_id -> (sleeper_id, player_name), consulted before the crosswalk.
#
# The name is carried here rather than looked up on the board because it is needed exactly when
# the board no longer has the player: an override that has gone stale fails the build, and
# "00-0041145 is not on the board" is not something anyone can act on at speed.
#
# Every entry below is the same story — a rookie who has a crosswalk row and a Sleeper row, but
# whose `ids.sleeper_id` nflverse has not filled in yet. Expect to prune these as the crosswalk
# catches up; the build will tell you when one no longer matches anybody.
SLEEPER_ID_OVERRIDES = {
    "00-0041145": ("13833", "Dominic Zvada"),
    "00-0040899": ("13545", "Trey Smack"),
    "00-0041182": ("13968", "Drew Stevens"),
    "00-0039229": ("11653", "Charlie Smyth"),
    "00-0040530": ("13066", "Ben Sauls"),
}


def _as_sleeper_id(value) -> str | None:
    """One identifier as a string, with the crosswalk's float representation removed.

    `4034.0` and `"4034.0"` both become `"4034"`; `"LAR"` is left exactly as it is. Only a value
    that is already numeric, or spelled as a number with nothing but zeros after the point, is
    touched — so an ID that merely looks numeric keeps whatever shape it arrived in.
    """
    if value is None or pd.isna(value):
        return None
    if isinstance(value, float):
        return str(int(value))
    text = str(value).strip()
    if not text:
        return None
    whole, separator, fraction = text.partition(".")
    if separator and whole.isdigit() and set(fraction) <= {"0"}:
        return whole
    return text


def _crosswalk_ids(crosswalk: pd.DataFrame) -> dict[str, str]:
    """gsis_id -> Sleeper ID, for the crosswalk rows that carry one.

    A handful of gsis_ids appear on more than one crosswalk row. Taking the first that carries a
    Sleeper ID is enough here — where two rows disagree the collision check downstream is what
    catches it, rather than this quietly picking a winner.
    """
    ids = {}
    for gsis_id, sleeper_id in zip(crosswalk["gsis_id"], crosswalk["sleeper_id"]):
        resolved = _as_sleeper_id(sleeper_id)
        if resolved is not None and gsis_id not in ids:
            ids[gsis_id] = resolved
    return ids


def _defense_ids(defenses: pd.DataFrame) -> dict[str, str]:
    """Canonical team abbreviation -> the identifier Sleeper uses for that defense.

    Keyed on the normalized abbreviation so a board saying `LA` finds the row Sleeper files under
    `LAR`, and valued at Sleeper's own spelling so what comes back matches a live pick.
    """
    return {
        normalize_team(str(player_id)): _as_sleeper_id(player_id)
        for player_id in defenses["player_id"]
    }


def _check_overrides(board: pd.DataFrame, overrides: dict[str, tuple[str, str]]) -> None:
    """Fail loudly on an override for a player the board no longer carries."""
    on_board = set(board["player_id"])
    missing = [
        f"{name} ({player_id})"
        for player_id, (_, name) in overrides.items()
        if player_id not in on_board
    ]
    if missing:
        raise ValueError(
            "SLEEPER_ID_OVERRIDES names players who are not on the board: "
            + ", ".join(missing)
            + ". Remove the entry, or find out why the player left the board."
        )


def _check_for_collisions(board: pd.DataFrame) -> None:
    """Fail loudly if two board rows claim one identifier.

    This is the failure the whole module is built to prevent. Two rows sharing an ID means a
    single pick would strike the wrong player off the board — or leave a drafted one on it — and
    the drafter would have no way to see that from the screen.
    """
    mapped = board[board["sleeper_id"].notna()]
    collisions = mapped[mapped.duplicated("sleeper_id", keep=False)]
    if collisions.empty:
        return

    described = [
        f"{sleeper_id} is claimed by " + " and ".join(rows["player_name"])
        for sleeper_id, rows in collisions.groupby("sleeper_id")
    ]
    raise ValueError("Sleeper IDs must identify one board row each, but " + "; ".join(described))


def resolve_sleeper_ids(
    board: pd.DataFrame,
    crosswalk: pd.DataFrame,
    defenses: pd.DataFrame,
    overrides: dict[str, tuple[str, str]] = SLEEPER_ID_OVERRIDES,
) -> pd.DataFrame:
    """The board with a `sleeper_id` column, resolved override-first and returned as strings.

    One league's board at a time: the same player holds a row per league, so a whole-board
    collision check would report every player as a collision with himself.
    """
    _check_overrides(board, overrides)

    from_crosswalk = _crosswalk_ids(crosswalk)
    from_defenses = _defense_ids(defenses)

    def resolve(player_id: str, position: str) -> str | None:
        if player_id in overrides:
            return _as_sleeper_id(overrides[player_id][0])
        if position == "DST":
            return from_defenses.get(normalize_team(str(player_id)))
        return from_crosswalk.get(player_id)

    resolved = board.assign(
        sleeper_id=[
            resolve(player_id, position)
            for player_id, position in zip(board["player_id"], board["position"])
        ]
    )
    _check_for_collisions(resolved)
    return resolved


def _draftable(board: pd.DataFrame) -> pd.Series:
    """Whether each row is someone this league might actually draft — see the module docstring."""
    return board["consensus_adp"].notna() | (
        board["position_rank"] <= board["starters_at_position"]
    )


def unmapped_draftable(board: pd.DataFrame) -> pd.DataFrame:
    """The draftable rows with no Sleeper ID — the gaps that would cost a pick."""
    return board[_draftable(board) & board["sleeper_id"].isna()]


def report_unmapped(board: pd.DataFrame, league_key: str) -> None:
    """Name every draftable player left unmapped, and count the rest.

    Deliberately not `@console.analysis`: this is the one thing the build knows that a drafter
    needs days of warning about, so it survives a quiet build like any other note.
    """
    gaps = unmapped_draftable(board)
    for _, row in gaps.iterrows():
        priced = "unpriced" if pd.isna(row["consensus_adp"]) else f"ADP {row['consensus_adp']:.1f}"
        console.note(
            f"{league_key}: no Sleeper ID for {row['player_name']} "
            f"({row['position']}{int(row['position_rank'])}, {priced})"
        )

    below_cut = int((~_draftable(board) & board["sleeper_id"].isna()).sum())
    if below_cut:
        console.note(f"{league_key}: {below_cut} more unmapped below the draftable cut")
