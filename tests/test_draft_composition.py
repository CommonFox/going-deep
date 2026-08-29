"""Spec cases 29-35 from issue #38: which roster shapes the opening is still on track for.

These assert what comes *out* of `composition_guidance` for a given plan frame and a given set of
picks already spent — never how it gets there. Cost of waiting looks one pick ahead, one player at
a time, and so cannot see the roster being assembled; this is the signal that can, and it is read
from the plan table at run time rather than encoded as a rule.

## Why the fixture scores openings on their quarterback count alone

The finding this feature exists to carry *reverses between the two leagues this repo serves*:
opening with two quarterbacks is worth roughly +22 points against the field in the superflex
league and roughly -41 in the one-quarterback league. So the thing worth testing is not that the
guidance says "take two quarterbacks" — it is that the guidance says whatever the frame it was
handed says, and the opposite when handed the opposite frame.

Every fixture opening therefore scores purely on how many quarterbacks it takes, with the two
score maps below being mirror images. That makes every expected band mean workable on paper: a
band's score is the mean of its members' quarterback-level scores, and the quarterback bands are
the only ones made up of a single level, which is what makes one of them the best band in either
direction.

The ten openings are chosen so that no band other than a quarterback band is made up entirely of
openings from one quarterback level. Without that, a receiver band that happened to contain only
two-quarterback openings would tie with the quarterback band it is really just restating, and the
"best band" would be an artifact of the fixture rather than of the arithmetic.

## Vocabulary

*Plan*, *composition* and *band*, as the glossary has them: a plan is a claim about the opening, a
composition is that plan stated as counts, and a band is every composition sharing one position's
count. The guidance never names a composition — the plan table's recorded dispersion puts the
leading compositions inside each other's noise at most seats, and only the coarse band structure
underneath them is real enough to show.
"""

import pandas as pd

from src.draft.composition import composition_guidance

SEAT = 3
ANOTHER_SEAT = 7

# The order the plan table writes a composition in, which is the order these labels are built in.
POSITIONS = ("QB", "RB", "WR", "TE")

# Ten five-pick openings, as (QB, RB, WR, TE) counts. Three at each quarterback level bar one, and
# arranged so that every running back, receiver and tight end count is shared across levels — see
# the module docstring on why that matters.
OPENINGS = [
    (2, 1, 1, 1), (2, 2, 1, 0), (2, 1, 2, 0),
    (1, 2, 1, 1), (1, 1, 2, 1), (1, 1, 1, 2), (1, 2, 2, 0),
    (0, 2, 2, 1), (0, 1, 2, 2), (0, 2, 1, 2),
]

# Openings three picks long rather than five, for the case that the length is read from the frame
# rather than assumed. Same rule: the score is the quarterback count and nothing else.
SHORT_OPENINGS = [(2, 1, 0, 0), (1, 1, 1, 0), (1, 0, 1, 1), (0, 2, 1, 0), (0, 1, 1, 1)]

# What an opening is worth against the field, by how many quarterbacks it takes. The first is this
# repo's superflex league, in shape if not in exact numbers; the second is the one-quarterback
# league, where the same opening is the worst one available.
QB_FIRST = {0: -42.0, 1: 0.0, 2: 38.0}
QB_LAST = {0: 38.0, 1: 0.0, 2: -42.0}

# Simulated drafts behind every plan, as the plan table records it: the same for each, which is
# what makes a band mean a plain average of its members.
TRIALS = 300


def label(counts: tuple[int, ...]) -> str:
    """A composition written the way the plan table writes it — `2QB1RB1WR1TE`."""
    return "".join(f"{n}{position}" for n, position in zip(counts, POSITIONS) if n)


def plan_frame(
    scores: dict[int, float] = QB_FIRST,
    openings: list[tuple[int, ...]] = OPENINGS,
    seat: int = SEAT,
    rows: tuple[dict, ...] = (),
) -> pd.DataFrame:
    """`draft_plans` as the warehouse hands it over, already filtered to one league.

    One row per (seat, plan), carrying only the columns the guidance reads. `rows` appends
    anything else the real table holds — the pure-ADP control, or another seat's plans.
    """
    planned = [
        {
            "draft_slot": seat,
            "plan": label(counts),
            "trials": TRIALS,
            "points_vs_field": scores[counts[0]],
            # Win rate moves with the score rather than being invented separately, so a frame
            # cannot say one thing on points and the opposite on wins.
            "win_rate": round(0.25 + scores[counts[0]] / 400, 3),
        }
        for counts in openings
    ]
    return pd.DataFrame(planned + list(rows))


def picks(*positions: str) -> dict:
    """What ingestion hands over, with the only part the guidance reads: my picks so far."""
    return {
        "mine": [
            {
                "player_id": f"00-000000{number}",
                "player_name": f"Player {number}",
                "position": position,
            }
            for number, position in enumerate(positions, start=1)
        ]
    }


def league(seat: int = SEAT) -> dict:
    """The league shape `resolve_seat` reads out of the draft record."""
    return {"seat": seat, "roster_id": 6, "team_count": 14, "rounds": 15, "slots": {}}


def band(guidance: dict, position: str) -> pd.DataFrame:
    """One position's bands, lowest count first."""
    bands = guidance["bands"]
    return bands.loc[bands["position"] == position].sort_values("count")


# 29. Guidance is derived from the supplied frame, not from constants: a frame in which
#     zero-quarterback openings score best yields guidance toward zero quarterbacks.
def test_a_frame_that_likes_no_quarterbacks_gives_guidance_toward_none():
    guidance = composition_guidance(plan_frame(QB_LAST), picks(), league())

    assert guidance["best"]["position"] == "QB"
    assert guidance["best"]["count"] == 0


# 30. The same code path, given a plan frame whose best band is the opposite of the superflex
#     league's, produces the opposite guidance — the case that proves no finding is encoded here.
def test_the_same_call_on_the_opposite_frame_gives_the_opposite_band():
    toward_none = composition_guidance(plan_frame(QB_LAST), picks(), league())
    toward_two = composition_guidance(plan_frame(QB_FIRST), picks(), league())

    assert toward_none["best"]["position"] == "QB"
    assert toward_two["best"]["position"] == "QB"
    assert (toward_none["best"]["count"], toward_two["best"]["count"]) == (0, 2)


# 31. Guidance is expressed as a position-count band and never names a single composition as
#     optimal — the plan table has no dispersion, so the leaders cannot be told apart from noise.
def test_guidance_names_a_band_and_never_a_composition():
    frame = plan_frame()
    guidance = composition_guidance(frame, picks(), league())

    assert set(guidance["best"]) >= {"position", "count"}

    printed = str(guidance) + guidance["bands"].to_string()
    for plan in frame["plan"]:
        assert plan not in printed


# 31 (continued). Every open band is reported, so the shapes that score badly are as visible as
#     the one that scores well — being warned away is half of what the signal is for.
def test_every_open_band_is_reported_with_what_it_scored():
    guidance = composition_guidance(plan_frame(), picks(), league())

    quarterbacks = band(guidance, "QB")
    assert list(quarterbacks["count"]) == [0, 1, 2]
    assert list(quarterbacks["points_vs_field"]) == [-42.0, 0.0, 38.0]


# 32. Guidance is absent, and marked absent, once the opening rounds the plan table covers have
#     passed. The table says nothing about round six, so neither does this.
def test_guidance_is_withdrawn_once_the_opening_is_over():
    guidance = composition_guidance(
        plan_frame(), picks("QB", "QB", "RB", "WR", "TE"), league()
    )

    assert guidance["withdrawn"] is True
    assert guidance["best"] is None
    assert guidance["bands"].empty
    assert guidance["reason"]


# 32 (continued). How long the opening is comes from the frame too, not from a number typed here.
def test_the_length_of_the_opening_is_read_from_the_frame():
    frame = plan_frame(openings=SHORT_OPENINGS)

    assert composition_guidance(frame, picks("QB", "RB"), league())["withdrawn"] is False
    assert composition_guidance(frame, picks("QB", "RB", "WR"), league())["withdrawn"] is True


# 34. A roster consistent with no plan in the frame reports no open plans and does not raise.
def test_an_opening_no_plan_covers_reports_nothing_open():
    guidance = composition_guidance(plan_frame(), picks("QB", "QB", "QB"), league())

    assert guidance["open_plans"] == 0
    assert guidance["best"] is None
    assert guidance["bands"].empty
    assert guidance["withdrawn"] is False
    assert guidance["reason"]


# 34 (continued). A position the plan table has never heard of is the same kind of nothing, and
#     is reported rather than dropped so the screen can say why it has stopped talking.
def test_a_position_outside_the_plans_closes_every_one_of_them():
    guidance = composition_guidance(plan_frame(), picks("QB", "K"), league())

    assert guidance["open_plans"] == 0
    assert guidance["taken"]["K"] == 1


# 35. A candidate whose position would close the best-scoring open band is reported as doing so.
def test_the_positions_that_would_close_the_best_band_are_named():
    guidance = composition_guidance(
        plan_frame(), picks("QB", "RB", "WR", "TE"), league()
    )

    assert (guidance["best"]["position"], guidance["best"]["count"]) == ("QB", 2)
    assert guidance["keeps_best"] == ["QB"]
    assert guidance["closes_best"] == ["RB", "WR", "TE"]


# The seat filter, from the ticket's first acceptance criterion: the bands are this seat's. A
# snake gives every slot a different draft, and the plan table is keyed on the seat for that
# reason — reading another seat's rows would be guidance for somebody else's draft.
def test_only_the_drafters_own_seat_is_read():
    frame = pd.concat(
        [plan_frame(QB_FIRST, seat=SEAT), plan_frame(QB_LAST, seat=ANOTHER_SEAT)],
        ignore_index=True,
    )

    assert composition_guidance(frame, picks(), league(SEAT))["best"]["count"] == 2
    assert composition_guidance(frame, picks(), league(ANOTHER_SEAT))["best"]["count"] == 0


def test_a_seat_the_table_does_not_cover_withdraws_rather_than_guessing():
    guidance = composition_guidance(plan_frame(seat=ANOTHER_SEAT), picks(), league(SEAT))

    assert guidance["withdrawn"] is True
    assert guidance["bands"].empty
    assert guidance["reason"]


# The plan table carries the pure-ADP control beside the compositions, under a label that is not
# a composition at all. It is not an opening anybody can still be on track for, and it must not
# become a band — least of all the best one.
def test_a_row_that_is_not_a_composition_is_left_out():
    frame = plan_frame(
        rows=(
            {
                "draft_slot": SEAT,
                "plan": "best available",
                "trials": TRIALS,
                "points_vs_field": 500.0,
                "win_rate": 0.9,
            },
        )
    )
    guidance = composition_guidance(frame, picks(), league())

    assert guidance["best"]["position"] == "QB"
    assert guidance["best"]["count"] == 2
    assert guidance["open_plans"] == len(OPENINGS)
