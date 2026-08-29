"""Which draft gets archived, from the list a league hands back.

The archiving pair is I/O either side of one decision: of the drafts this league has on record,
which one is *this season's*. That decision is the only part worth a test, and it is worth one
because it is not the only place it is made — `src.draft.live` picks the draft it watches by the
same rule, and the two disagreeing would mean archiving a different draft than the one drafted
from. So the rule lives in one function and both callers read it.

The fixtures are Sleeper's own shape, which is where two of the cases come from: `season` arrives
as a **string** while the repo carries the season as an int, and `created` is milliseconds since
the epoch.
"""

import pytest

from src.silver.sleeper import select_draft


def draft(draft_id: str, season: str, created: int = 0) -> dict:
    """One entry from `GET /league/<id>/drafts`, cut down to what the choice reads."""
    return {
        "draft_id": draft_id,
        "season": season,
        "created": created,
        "type": "snake",
        "status": "complete",
    }


# 1. A league that has drafted for years hands back all of them; the season picks one out.
def test_the_draft_for_the_asked_for_season_is_the_one_chosen():
    drafts = [
        draft("2024-draft", "2024", created=1_690_000_000_000),
        draft("2025-draft", "2025", created=1_720_000_000_000),
        draft("2026-draft", "2026", created=1_785_963_881_642),
    ]
    assert select_draft(drafts, 2026)["draft_id"] == "2026-draft"
    assert select_draft(drafts, 2024)["draft_id"] == "2024-draft"


# 2. Sleeper spells the season as a string and every caller here holds an int.
def test_the_season_matches_across_sleepers_string_and_the_repos_int():
    drafts = [draft("2026-draft", "2026")]
    assert select_draft(drafts, 2026)["draft_id"] == "2026-draft"
    assert select_draft(drafts, "2026")["draft_id"] == "2026-draft"


# 3. A league that restarted or redrafted has two for one season; the newest is the real one.
def test_the_newest_draft_wins_when_a_season_has_more_than_one():
    drafts = [
        draft("abandoned", "2026", created=1_785_000_000_000),
        draft("the-real-one", "2026", created=1_785_963_881_642),
    ]
    assert select_draft(drafts, 2026)["draft_id"] == "the-real-one"
    # Order in the payload is not the rule — `created` is.
    assert select_draft(list(reversed(drafts)), 2026)["draft_id"] == "the-real-one"


# 4. Refuses rather than falling back on another year, which would archive the wrong picks.
def test_a_season_with_no_draft_raises_rather_than_choosing_another():
    drafts = [draft("2025-draft", "2025")]
    with pytest.raises(RuntimeError, match="2026"):
        select_draft(drafts, 2026)


# 5. A league that has never drafted.
def test_no_drafts_at_all_raises():
    with pytest.raises(RuntimeError, match="2026"):
        select_draft([], 2026)
