"""Spec cases 1-9 from the live draft assistant's identity-resolution ticket.

Every fixture here is hand-written and small enough to reason about by eye, which is the point:
these assert what comes *out* of resolution for a given board and crosswalk, never how the
resolution is done. The shapes are not invented, though — each one reproduces something measured
in the real warehouse:

- `4034.0` is what `ids.sleeper_id` actually holds, a DOUBLE, so every ID needs the float scraped
  off it before a pick can be matched by string equality.
- `LA` against Sleeper's `LAR` is the one genuine abbreviation disagreement between the board's
  nfl_data_py convention and Sleeper's, and it is a defense, so it has no crosswalk row to fall
  back on.
- The rookie kickers are on the board and in Sleeper's player list but carry a null `sleeper_id`
  in nflverse's crosswalk, which is the entire reason an override table exists.
"""

import pandas as pd
import pytest

from src.gold.sleeper_ids import (
    report_unmapped,
    resolve_sleeper_ids,
    unmapped_draftable,
)


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


def crosswalk(**gsis_to_sleeper) -> pd.DataFrame:
    """nflverse's `ids`, cut to the two columns that matter, with its float `sleeper_id`."""
    return pd.DataFrame(
        [{"gsis_id": gsis, "sleeper_id": sleeper} for gsis, sleeper in gsis_to_sleeper.items()],
        # Named explicitly so an empty crosswalk still has the columns a real query returns —
        # a bare DataFrame([]) has no columns at all, which is a shape the warehouse never hands
        # back and so not one worth making the code under test tolerate.
        columns=["gsis_id", "sleeper_id"],
    )


def defenses(*sleeper_ids: str) -> pd.DataFrame:
    """Sleeper's own DEF rows, which key a defense on its team abbreviation."""
    return pd.DataFrame({"player_id": list(sleeper_ids)})


NO_DEFENSES = defenses()
NO_OVERRIDES: dict[str, tuple[str, str]] = {}


# 1. Every running back, receiver and tight end on the board resolves to a Sleeper ID.
def test_skill_positions_resolve_through_the_crosswalk():
    resolved = resolve_sleeper_ids(
        board(
            {"player_id": "00-0000001", "player_name": "A Back", "position": "RB"},
            {"player_id": "00-0000002", "player_name": "A Receiver", "position": "WR"},
            {"player_id": "00-0000003", "player_name": "An End", "position": "TE"},
        ),
        crosswalk(**{"00-0000001": 4034.0, "00-0000002": 5849.0, "00-0000003": 7564.0}),
        NO_DEFENSES,
        NO_OVERRIDES,
    )

    assert resolved["sleeper_id"].tolist() == ["4034", "5849", "7564"]


# 2. Every quarterback within the draftable range resolves to a Sleeper ID.
def test_draftable_quarterback_resolves_and_a_deeper_one_is_neither_resolved_nor_reported():
    resolved = resolve_sleeper_ids(
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

    by_name = resolved.set_index("player_name")["sleeper_id"]
    assert by_name["A Starter"] == "4034"
    assert by_name["A Third Stringer"] is None

    # Outside the draftable cut on both arms — no ADP, ranked past the league's starter depth —
    # so his absence is a fact about a player nobody drafts, not a gap worth a warning.
    assert unmapped_draftable(resolved)["player_name"].tolist() == []


# 3. Every team defense resolves via its team abbreviation.
def test_defenses_resolve_on_their_team_abbreviation():
    resolved = resolve_sleeper_ids(
        board({"player_id": "PHI", "player_name": "PHI", "position": "DST"}),
        crosswalk(),
        defenses("PHI"),
        NO_OVERRIDES,
    )

    assert resolved["sleeper_id"].tolist() == ["PHI"]


# 4. A board team abbreviation that differs from Sleeper's normalizes correctly before matching.
def test_a_defense_whose_abbreviation_sleeper_spells_differently_still_matches():
    resolved = resolve_sleeper_ids(
        board({"player_id": "LA", "player_name": "LA", "position": "DST"}),
        crosswalk(),
        defenses("LAR"),
        NO_OVERRIDES,
    )

    # The board says LA and Sleeper says LAR for the same franchise. What comes back is the
    # identifier a pick will actually arrive carrying, which is Sleeper's.
    assert resolved["sleeper_id"].tolist() == ["LAR"]


# 5. Resolved IDs are strings with no floating-point residue.
def test_ids_come_back_as_strings_without_the_crosswalks_float():
    resolved = resolve_sleeper_ids(
        board({"player_id": "00-0000001"}),
        crosswalk(**{"00-0000001": 4034.0}),
        NO_DEFENSES,
        NO_OVERRIDES,
    )

    (sleeper_id,) = resolved["sleeper_id"]
    assert sleeper_id == "4034"
    assert isinstance(sleeper_id, str)


# 6. No two board rows resolve to the same Sleeper ID.
def test_two_board_rows_resolving_to_one_id_fails_loudly():
    with pytest.raises(ValueError) as error:
        resolve_sleeper_ids(
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
    resolved = resolve_sleeper_ids(
        board({"player_id": "00-0000001", "player_name": "A Kicker", "position": "K"}),
        crosswalk(**{"00-0000001": 4034.0}),
        NO_DEFENSES,
        {"00-0000001": ("13833", "A Kicker")},
    )

    assert resolved["sleeper_id"].tolist() == ["13833"]


# 8. An override naming a player absent from the board fails the build loudly.
def test_an_override_for_a_player_who_is_not_on_the_board_fails_loudly():
    with pytest.raises(ValueError) as error:
        resolve_sleeper_ids(
            board({"player_id": "00-0000001", "player_name": "A Kicker"}),
            crosswalk(**{"00-0000001": 4034.0}),
            NO_DEFENSES,
            {"00-0000099": ("13833", "A Departed Kicker")},
        )

    assert "A Departed Kicker" in str(error.value)


# 9. The build reports every draftable player left unmapped, naming each one.
def test_the_report_names_every_draftable_player_left_unmapped(capsys):
    resolved = resolve_sleeper_ids(
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

    report_unmapped(resolved, "sleeper")
    printed = capsys.readouterr().out

    # Priced by the market, so draftable however deep his position rank.
    assert "A Priced Kicker" in printed
    # No ADP at all, but inside the league's starter depth — the rookie case the cut exists for.
    assert "A Top Kicker" in printed
    # Ranked past anyone's bench, unpriced: counted, but not named at every build.
    assert "A Camp Body" not in printed
