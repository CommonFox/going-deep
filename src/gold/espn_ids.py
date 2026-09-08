"""Resolve every draft board row to the identifier a live ESPN draft pick will arrive carrying.

The ESPN counterpart to `sleeper_ids.py` — same problem, same three-route resolution, same
"never guess, report the gap" instinct. See that module's docstring for the reasoning in full;
this one only calls out where ESPN's shape actually differs.

## The route in

nflverse's `ids` crosswalk carries `espn_id` alongside `sleeper_id`, covering the same skill
positions completely. Unlike Sleeper's own player export, ESPN's `espn_players` table (already
loaded by `src.silver.espn`) carries no `gsis_id` to trap the unwary with — there is no shortcut
route to fall into here, only the crosswalk.

## Defenses are named, not keyed on a team abbreviation

Sleeper's own DEF rows key a defense on its team abbreviation directly. ESPN's `espn_players`
rows for a defense (`defaultPositionId == 16`) carry a numeric `id` and a `fullName` like
"Eagles D/ST" — there is no bare abbreviation anywhere in the row. So the team is read out of the
name via `normalize_team`, which tolerates a full name the same way it tolerates a non-canonical
abbreviation.

## Identifiers are strings, deliberately

`ids.espn_id` is stored as a DOUBLE, same as `ids.sleeper_id`, so `4034.0` becomes `"4034"` the
same way. See `sleeper_ids._as_sleeper_id`'s docstring for the exact rule; this module applies it
identically.
"""

import pandas as pd

from src import console
from src.silver.teams import normalize_team

# gsis_id -> (espn_id, player_name), consulted before the crosswalk. Empty until a build reports a
# gap the crosswalk hasn't caught up with — see `sleeper_ids.SLEEPER_ID_OVERRIDES` for the pattern
# an entry here should follow.
ESPN_ID_OVERRIDES: dict[str, tuple[str, str]] = {}


def _as_espn_id(value) -> str | None:
    """One identifier as a string, with the crosswalk's float representation removed."""
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
    """gsis_id -> ESPN ID, for the crosswalk rows that carry one."""
    ids = {}
    for gsis_id, espn_id in zip(crosswalk["gsis_id"], crosswalk["espn_id"]):
        resolved = _as_espn_id(espn_id)
        if resolved is not None and gsis_id not in ids:
            ids[gsis_id] = resolved
    return ids


def _defense_ids(defenses: pd.DataFrame) -> dict[str, str]:
    """Canonical team abbreviation -> the identifier ESPN uses for that defense.

    Keyed on the normalized abbreviation read out of ESPN's own D/ST name, so a board saying `LA`
    finds the row ESPN files under "Rams D/ST".
    """
    return {
        normalize_team(str(name)): _as_espn_id(espn_id)
        for espn_id, name in zip(defenses["id"], defenses["fullName"])
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
            "ESPN_ID_OVERRIDES names players who are not on the board: "
            + ", ".join(missing)
            + ". Remove the entry, or find out why the player left the board."
        )


def _check_for_collisions(board: pd.DataFrame) -> None:
    """Fail loudly if two board rows claim one identifier."""
    mapped = board[board["espn_id"].notna()]
    collisions = mapped[mapped.duplicated("espn_id", keep=False)]
    if collisions.empty:
        return

    described = [
        f"{espn_id} is claimed by " + " and ".join(rows["player_name"])
        for espn_id, rows in collisions.groupby("espn_id")
    ]
    raise ValueError("ESPN IDs must identify one board row each, but " + "; ".join(described))


def resolve_espn_ids(
    board: pd.DataFrame,
    crosswalk: pd.DataFrame,
    defenses: pd.DataFrame,
    overrides: dict[str, tuple[str, str]] = ESPN_ID_OVERRIDES,
) -> pd.DataFrame:
    """The board with an `espn_id` column, resolved override-first and returned as strings.

    One league's board at a time — see `resolve_sleeper_ids`'s docstring for why.
    """
    _check_overrides(board, overrides)

    from_crosswalk = _crosswalk_ids(crosswalk)
    from_defenses = _defense_ids(defenses)

    def resolve(player_id: str, position: str) -> str | None:
        if player_id in overrides:
            return _as_espn_id(overrides[player_id][0])
        if position == "DST":
            return from_defenses.get(normalize_team(str(player_id)))
        return from_crosswalk.get(player_id)

    resolved = board.assign(
        espn_id=[
            resolve(player_id, position)
            for player_id, position in zip(board["player_id"], board["position"])
        ]
    )
    _check_for_collisions(resolved)
    return resolved


def _draftable(board: pd.DataFrame) -> pd.Series:
    """Whether each row is someone this league might actually draft — see `sleeper_ids`."""
    return board["consensus_adp"].notna() | (
        board["position_rank"] <= board["starters_at_position"]
    )


def unmapped_draftable(board: pd.DataFrame) -> pd.DataFrame:
    """The draftable rows with no ESPN ID — the gaps that would cost a pick."""
    return board[_draftable(board) & board["espn_id"].isna()]


def report_unmapped(board: pd.DataFrame, league_key: str) -> None:
    """Name every draftable player left unmapped, and count the rest."""
    gaps = unmapped_draftable(board)
    for _, row in gaps.iterrows():
        priced = "unpriced" if pd.isna(row["consensus_adp"]) else f"ADP {row['consensus_adp']:.1f}"
        console.note(
            f"{league_key}: no ESPN ID for {row['player_name']} "
            f"({row['position']}{int(row['position_rank'])}, {priced})"
        )

    below_cut = int((~_draftable(board) & board["espn_id"].isna()).sum())
    if below_cut:
        console.note(f"{league_key}: {below_cut} more unmapped below the draftable cut")
