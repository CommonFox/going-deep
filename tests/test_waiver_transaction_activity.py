"""Transaction velocity and roster share on the waiver board — issue #174, under the waiver epic
(#110).

The one part of this table genuinely worth unit testing is the Sleeper unpivot: `adds`/`drops` land
in the warehouse as one column per player ever touched (`adds.11256`, `drops.PIT`, ...), flattened
by `_load_json_to_table`'s `json_normalize` rather than a fixed schema, so nothing here can
reference a real column name ahead of time. Every fixture is hand-built and small, in the same
spirit as `test_waiver_pairs.py`: no warehouse, no dynamic column list read from disk.
"""

import pandas as pd

from src.gold.waiver_transaction_activity import (
    sleeper_add_drop_column,
    sleeper_transaction_counts,
    unpivot_sleeper_transactions,
)


# 1. A column named `adds.<key>` is an add of the player named by that key.
def test_adds_column_parses_to_add_action_and_key():
    assert sleeper_add_drop_column("adds.11256") == ("add", "11256")


# 2. A column named `drops.<key>` is a drop of the player named by that key — including a team
#    abbreviation key, Sleeper's own defense ID (see draft_board.sleeper_id for DST).
def test_drops_column_parses_to_drop_action_and_key():
    assert sleeper_add_drop_column("drops.PIT") == ("drop", "PIT")


# 3. Every other column on the table — including the bare, un-flattened `adds`/`drops` scalar
#    columns json_normalize leaves behind when every row's dict was empty — is not an add/drop at
#    all, and parses to nothing.
def test_unrelated_column_parses_to_none():
    assert sleeper_add_drop_column("status") is None
    assert sleeper_add_drop_column("adds") is None
    assert sleeper_add_drop_column("drops") is None
    assert sleeper_add_drop_column("settings.waiver_bid") is None


# 4. Unpivoting attributes an add to the specific player named in its own dynamic column, not to
#    every row of the transaction — the ticket's own acceptance bar. A second player's untouched
#    column on the same row contributes nothing.
def test_unpivot_attributes_add_to_the_named_player_only():
    transactions = pd.DataFrame([
        {"transaction_id": "t1", "created": 1000, "adds.11256": 5.0, "adds.9999": None},
    ])

    touches = unpivot_sleeper_transactions(transactions)

    assert touches.to_dict("records") == [
        {"transaction_id": "t1", "created": 1000, "action": "add", "platform_player_id": "11256"},
    ]


# 5. A single transaction can carry both an add and a drop (the ordinary waiver swap) — both
#    surface as their own row, neither one swallowing the other.
def test_unpivot_carries_both_add_and_drop_from_one_transaction():
    transactions = pd.DataFrame([
        {"transaction_id": "t1", "created": 1000, "adds.11256": 5.0, "drops.PIT": 5.0},
    ])

    touches = unpivot_sleeper_transactions(transactions)

    assert set(touches["action"]) == {"add", "drop"}
    assert set(touches["platform_player_id"]) == {"11256", "PIT"}


# 6. A player with no entry at all on a given row (the ordinary case — most columns are null on
#    most rows, since a column exists if *any* transaction ever touched that player) contributes no
#    row for that transaction.
def test_unpivot_skips_null_entries():
    transactions = pd.DataFrame([
        {"transaction_id": "t1", "created": 1000, "adds.11256": None},
        {"transaction_id": "t2", "created": 2000, "adds.11256": 7.0},
    ])

    touches = unpivot_sleeper_transactions(transactions)

    assert touches["transaction_id"].tolist() == ["t2"]


# 7. A player touched by two different transactions inside the window counts twice — this table
#    reports velocity, not merely whether he was touched at all.
def test_transaction_counts_counts_every_qualifying_transaction():
    touches = pd.DataFrame([
        {"transaction_id": "t1", "created": 1000, "action": "add", "platform_player_id": "11256"},
        {"transaction_id": "t2", "created": 2000, "action": "drop", "platform_player_id": "11256"},
    ])

    counts = sleeper_transaction_counts(touches, since_ms=0)

    assert counts.to_dict("records") == [
        {"platform_player_id": "11256", "transaction_count": 2},
    ]


# 8. A transaction outside the trailing window doesn't count — the whole point of a *trailing*
#    window rather than an all-time total.
def test_transaction_counts_excludes_transactions_before_the_window():
    touches = pd.DataFrame([
        {"transaction_id": "t1", "created": 1000, "action": "add", "platform_player_id": "11256"},
    ])

    counts = sleeper_transaction_counts(touches, since_ms=2000)

    assert counts.empty


# 9. A player touched by the same transaction through both an add and a drop column (a data shape
#    that shouldn't happen, but costs nothing to guard) is still one transaction, not two — matching
#    the ticket's "count of sleeper_transactions *rows*", not count of add/drop events.
def test_transaction_counts_counts_distinct_transactions_not_touches():
    touches = pd.DataFrame([
        {"transaction_id": "t1", "created": 1000, "action": "add", "platform_player_id": "11256"},
        {"transaction_id": "t1", "created": 1000, "action": "drop", "platform_player_id": "11256"},
    ])

    counts = sleeper_transaction_counts(touches, since_ms=0)

    assert counts.to_dict("records") == [
        {"platform_player_id": "11256", "transaction_count": 1},
    ]
