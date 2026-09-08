"""ESPN's counterpart to `test_sleeper_ids.py`: the same nine cases, retargeted at `espn_id`.

Every fixture reproduces something measured in the real warehouse, mirroring the Sleeper suite:

- `ids.espn_id` is a DOUBLE, exactly like `ids.sleeper_id`, so the same float-scraping applies.
- ESPN has no `LA`/`LAR`-style disagreement with `normalize_team` today, but the defense route is
  exercised the same way regardless, since a future team rename could reintroduce one.
- ESPN's own defense rows come from `espn_players` (`defaultPositionId == 16`), keyed on `id` and
  named `fullName` (e.g. "Eagles D/ST"), not on a bare team abbreviation the way Sleeper's are.
"""

import pandas as pd
import pytest

from src.gold.espn_ids import report_unmapped, resolve_espn_ids, unmapped_draftable


def board(*rows: dict) -> pd.DataFrame:
    """A draft board with only the columns resolution reads, defaulted to a draftable player."""
    defaults = {
        "player_id": "00-0000001",
        "player_name": "A Player",
        "position": "RB",
        "position_rank": 1,
        "starters_at_position": 14,
        "consensus_adp": None,
    }
    return pd.DataFrame([{**defaults, **row} for row in rows])


def crosswalk(**gsis_to_espn) -> pd.DataFrame:
    """nflverse's `ids`, cut to the two columns that matter, with its float `espn_id`."""
    return pd.DataFrame(
        [{"gsis_id": gsis, "espn_id": espn} for gsis, espn in gsis_to_espn.items()],
        columns=["gsis_id", "espn_id"],
    )


def defenses(**team_to_espn_id) -> pd.DataFrame:
    """ESPN's own DST rows: `espn_players` filtered to defenses, named by team full name."""
    return pd.DataFrame(
        [{"id": espn_id, "fullName": f"{team} D/ST"} for team, espn_id in team_to_espn_id.items()],
        columns=["id", "fullName"],
    )


NO_DEFENSES = defenses()
NO_OVERRIDES: dict[str, tuple[str, str]] = {}


# 1. Every running back, receiver and tight end on the board resolves to an ESPN ID.
def test_skill_positions_resolve_through_the_crosswalk():
    resolved = resolve_espn_ids(
        board(
            {"player_id": "00-0000001", "player_name": "A Back", "position": "RB"},
            {"player_id": "00-0000002", "player_name": "A Receiver", "position": "WR"},
            {"player_id": "00-0000003", "player_name": "An End", "position": "TE"},
        ),
        crosswalk(**{"00-0000001": 4034.0, "00-0000002": 5849.0, "00-0000003": 7564.0}),
        NO_DEFENSES,
        NO_OVERRIDES,
    )

    assert resolved["espn_id"].tolist() == ["4034", "5849", "7564"]


# 2. Every quarterback within the draftable range resolves to an ESPN ID.
def test_draftable_quarterback_resolves_and_a_deeper_one_is_neither_resolved_nor_reported():
    resolved = resolve_espn_ids(
        board(
            {
                "player_id": "00-0000010",
                "player_name": "A Starter",
                "position": "QB",
                "position_rank": 12,
                "starters_at_position": 28,
            },
            {
                "player_id": "00-0000011",
                "player_name": "A Third Stringer",
                "position": "QB",
                "position_rank": 57,
                "starters_at_position": 28,
            },
        ),
        crosswalk(**{"00-0000010": 4034.0}),
        NO_DEFENSES,
        NO_OVERRIDES,
    )

    by_name = resolved.set_index("player_name")["espn_id"]
    assert by_name["A Starter"] == "4034"
    assert by_name["A Third Stringer"] is None

    assert unmapped_draftable(resolved)["player_name"].tolist() == []


# 3. Every team defense resolves via its team abbreviation, read off ESPN's own D/ST name.
def test_defenses_resolve_on_their_team_abbreviation():
    resolved = resolve_espn_ids(
        board({"player_id": "PHI", "player_name": "PHI", "position": "DST"}),
        crosswalk(),
        defenses(Eagles="16001"),
        NO_OVERRIDES,
    )

    assert resolved["espn_id"].tolist() == ["16001"]


# 4. A board team abbreviation that differs from ESPN's own nickname still matches — the board
#    says `LA` for the Rams, following nfl_data_py, and ESPN's D/ST name says "Rams".
def test_a_defense_whose_abbreviation_espn_spells_differently_still_matches():
    resolved = resolve_espn_ids(
        board({"player_id": "LA", "player_name": "LA", "position": "DST"}),
        crosswalk(),
        defenses(Rams="16002"),
        NO_OVERRIDES,
    )

    assert resolved["espn_id"].tolist() == ["16002"]


# 5. Resolved IDs are strings with no floating-point residue.
def test_ids_come_back_as_strings_without_the_crosswalks_float():
    resolved = resolve_espn_ids(
        board({"player_id": "00-0000001"}),
        crosswalk(**{"00-0000001": 4034.0}),
        NO_DEFENSES,
        NO_OVERRIDES,
    )

    (espn_id,) = resolved["espn_id"]
    assert espn_id == "4034"
    assert isinstance(espn_id, str)


# 6. No two board rows resolve to the same ESPN ID.
def test_two_board_rows_resolving_to_one_id_fails_loudly():
    with pytest.raises(ValueError) as error:
        resolve_espn_ids(
            board(
                {"player_id": "00-0000001", "player_name": "A Back"},
                {"player_id": "00-0000002", "player_name": "Another Back"},
            ),
            crosswalk(**{"00-0000001": 4034.0, "00-0000002": 4034.0}),
            NO_DEFENSES,
            NO_OVERRIDES,
        )

    assert "4034" in str(error.value)
    assert "A Back" in str(error.value)
    assert "Another Back" in str(error.value)


# 7. A hand-written override takes precedence over the crosswalk for the same player.
def test_an_override_beats_the_crosswalk():
    resolved = resolve_espn_ids(
        board({"player_id": "00-0000001", "player_name": "A Kicker", "position": "K"}),
        crosswalk(**{"00-0000001": 4034.0}),
        NO_DEFENSES,
        {"00-0000001": ("13833", "A Kicker")},
    )

    assert resolved["espn_id"].tolist() == ["13833"]


# 8. An override naming a player absent from the board fails the build loudly.
def test_an_override_for_a_player_who_is_not_on_the_board_fails_loudly():
    with pytest.raises(ValueError) as error:
        resolve_espn_ids(
            board({"player_id": "00-0000001", "player_name": "A Kicker"}),
            crosswalk(**{"00-0000001": 4034.0}),
            NO_DEFENSES,
            {"00-0000099": ("13833", "A Departed Kicker")},
        )

    assert "A Departed Kicker" in str(error.value)


# 9. The build reports every draftable player left unmapped, naming each one.
def test_the_report_names_every_draftable_player_left_unmapped(capsys):
    resolved = resolve_espn_ids(
        board(
            {
                "player_id": "00-0000020",
                "player_name": "A Priced Kicker",
                "position": "K",
                "position_rank": 21,
                "consensus_adp": 162.4,
            },
            {
                "player_id": "00-0000021",
                "player_name": "A Top Kicker",
                "position": "K",
                "position_rank": 5,
            },
            {
                "player_id": "00-0000022",
                "player_name": "A Camp Body",
                "position": "K",
                "position_rank": 34,
            },
        ),
        crosswalk(),
        NO_DEFENSES,
        NO_OVERRIDES,
    )

    report_unmapped(resolved, "espn")
    printed = capsys.readouterr().out

    assert "A Priced Kicker" in printed
    assert "A Top Kicker" in printed
    assert "A Camp Body" not in printed
