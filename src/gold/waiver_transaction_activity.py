"""Build `waiver_transaction_activity`: transaction velocity and, where the platform reports it,
roster share, one row per (league_key, platform_player_id) — issue #174, part of the waiver epic
(#110).

Pure warehouse-to-warehouse SQL/Python — no fetch step, no network. `waiver_rankings` prices a free
agent; it says nothing about whether he'll still be there tomorrow. `sleeper_transactions` and
`espn_transactions` are already loaded (`src/silver/sleeper.py`/`espn.py`) and, until this table,
read by nothing in `src/gold/`; `espn_player_ownership.player.ownership.*` likewise. Matching
`weekly_player_context`'s "one signal per table, joined by the consumer" convention, this table is
joined onto `waiver_rankings` (or anything else) by whoever needs it — it is not baked in.

## The two platforms answer "will he still be there" differently, so the two arms below don't match

ESPN carries a direct roster-share figure per player
(`espn_player_ownership.player.ownership.percentOwned`/`.percentChange`/`.percentStarted`) — no
computation needed, just a join. Sleeper is a single 14-team league with no league-wide ownership
stat at all, so its own "will he still be there" signal has to be transaction velocity itself: how
many qualifying `sleeper_transactions` rows touched that player in a trailing window. ESPN gets a
transaction count too, computed the identical way off `espn_transactions`, for symmetry with
Sleeper's count even though ESPN also has the direct figure — but a Sleeper row never invents a
roster-share number it has no source for, and an ESPN row is never dropped just because it wasn't
recently transacted on: `percent_owned`/`percent_owned_change`/`percent_started` are left null for
every Sleeper row rather than guessed at, and `transaction_count` defaults to `0`, not null, for an
ESPN row with no recent activity, because "no transactions" is a real, known zero.

## Population: whatever each platform actually has to say

Sleeper's whole reason for a row to exist here is transaction activity, so its rows are exactly the
players touched by a qualifying transaction inside the window — nothing invents a `0`-count row for
a player nobody has moved on. ESPN's ownership figures exist independently of activity, so its rows
are every player `espn_player_ownership` carries — its natural grain, not narrowed to `free_agents`
(that filter, if a consumer wants it, is exactly the kind of thing this table's join-in design
leaves to the consumer, per `weekly_player_context`'s rule above).

## Sleeper's `adds`/`drops` are dynamic columns, not a static schema

`sleeper_transactions.adds`/`.drops` are the raw JSON's *object* fields (`{"11256": 5}` — a player
ID mapped to the roster ID that made the move), flattened by `_load_json_to_table`'s
`pd.json_normalize` into one column per key ever actually seen across every transaction on file —
confirmed against the warehouse: `adds`/`drops` themselves are 0/117 non-null, while the real
per-player rows live in columns like `adds.11256`, `drops.PIT` (a team abbreviation for a Sleeper
DST — the same identity `draft_board.sleeper_id` already uses for a defense, per `drop_candidates`'s
own docstring). A rebuild against a different raw export sees an entirely different key set, so
nothing here can reference one of these columns by name ahead of time; `unpivot_sleeper_transactions`
walks whatever `adds.*`/`drops.*` columns are actually present on the frame it's handed, which is
also what lets it attribute a real add/drop to the specific player named in its own column rather
than smearing it across every row of the transaction (the ticket's acceptance bar).

## The trailing window is wall-clock, not a game week

"Will he still be there tomorrow" is a calendar-day question, not one `sleeper_nfl_state`'s
season/week answers — so the window is anchored on `datetime.now(timezone.utc)` at build time, the
same kind of freshness check `sleeper.fetch_players` already makes for its own 24-hour cache, rather
than any schedule-derived date. `TRAILING_WINDOW_DAYS` (7) is the ticket's own example figure, and
is deliberately a single named constant rather than a per-row column: it applies uniformly to both
platforms, and it's the kind of parameter this warehouse keeps in code rather than turning into
data (`weekly_outcome_rates`' floor/ceiling thresholds are the analogous case for a derived,
not-invented, cutoff).

`sleeper_transactions.created` and `espn_transactions.proposedDate` are both epoch milliseconds;
`proposedDate` is used for ESPN rather than `processDate` because the latter is null for every
`FREEAGENT` row and every still-`PENDING` waiver — `proposedDate` is the one timestamp every
qualifying row actually carries.

## Trade-type transactions are excluded by construction, not by a negative filter

Sleeper: `type IN ('waiver', 'free_agent')` (`status = 'complete'`) is an allowlist, so a `'trade'`
row (a type this league hasn't produced yet, but the schema allows) is excluded the same way any
other unlisted type would be, never a special case. ESPN: `type IN ('WAIVER', 'FREEAGENT')` and
`status = 'EXECUTED'` on the transaction, exploded from its `items` struct list — a `'ROSTER'`-type
transaction (a lineup move, not a roster add/drop at all) or a trade never enters the window.

## Identity: the same crosswalk `waiver_rankings.py`/`drop_candidates.py` already built off `draft_board`

Neither platform's own ID is the warehouse's canonical `player_id` (gsis_id, or a team abbreviation
for `DST`) — both are resolved through `draft_board.sleeper_id`/`espn_id`, the identical crosswalk
those two tables use, rather than re-derived a third time. A platform ID `draft_board` never priced
(a deep bench player nobody's board bothered pricing at draft time) still gets a row: `player_id`
rides along as a nullable enrichment column, exactly as `waiver_rankings`/`drop_candidates` already
treat this same gap, rather than the row disappearing because identity resolution came up empty.
`player_name`/`position` come from each platform's own player table instead
(`sleeper_players.full_name`/`.position`, `espn_player_ownership."player.fullName"` and
`.defaultPositionId` through `espn.POSITION_IDS`), so a name and position show up even when
`player_id` doesn't. Sleeper's team-defense rows carry no `full_name` at all — only `player_id`
equal to the team's own abbreviation — so `player_name` falls back to that abbreviation, the same
`COALESCE(full_name, player_id)` `free_agents.py` already uses for the identical gap.
"""

from datetime import datetime, timedelta, timezone
from pathlib import Path

import duckdb
import pandas as pd

from src import console
from src.silver.espn import POSITION_IDS

WAREHOUSE_PATH = Path("data/warehouse.duckdb")

TRAILING_WINDOW_DAYS = 7

_OUTPUT_COLUMNS = [
    "league_key", "platform_player_id", "player_id", "player_name", "position",
    "transaction_count", "percent_owned", "percent_owned_change", "percent_started",
]

_ESPN_ACTIVITY_SQL = """
WITH espn_identity AS (
    SELECT espn_id, ANY_VALUE(player_id) AS player_id
    FROM draft_board WHERE espn_id IS NOT NULL GROUP BY espn_id
),
espn_activity AS (
    SELECT
        CAST(touch.item.playerId AS VARCHAR) AS platform_player_id,
        COUNT(DISTINCT t.id) AS transaction_count
    FROM espn_transactions t, UNNEST(t.items) AS touch(item)
    WHERE t.type IN ('WAIVER', 'FREEAGENT') AND t.status = 'EXECUTED' AND t.proposedDate >= ?
    GROUP BY 1
)
SELECT
    'espn' AS league_key,
    CAST(o.id AS VARCHAR) AS platform_player_id,
    ei.player_id,
    o."player.fullName" AS player_name,
    espn_position(o."player.defaultPositionId") AS position,
    COALESCE(ea.transaction_count, 0) AS transaction_count,
    o."player.ownership.percentOwned" AS percent_owned,
    o."player.ownership.percentChange" AS percent_owned_change,
    o."player.ownership.percentStarted" AS percent_started
FROM espn_player_ownership o
LEFT JOIN espn_identity ei ON ei.espn_id = CAST(o.id AS VARCHAR)
LEFT JOIN espn_activity ea ON ea.platform_player_id = CAST(o.id AS VARCHAR)
"""

_SLEEPER_ACTIVITY_SQL = """
SELECT
    'sleeper' AS league_key,
    sc.platform_player_id,
    si.player_id,
    COALESCE(sp.full_name, sp.player_id) AS player_name,
    sp.position,
    sc.transaction_count,
    CAST(NULL AS DOUBLE) AS percent_owned,
    CAST(NULL AS DOUBLE) AS percent_owned_change,
    CAST(NULL AS DOUBLE) AS percent_started
FROM sleeper_counts sc
LEFT JOIN sleeper_players sp ON sp.player_id = sc.platform_player_id
LEFT JOIN (
    SELECT sleeper_id, ANY_VALUE(player_id) AS player_id
    FROM draft_board WHERE sleeper_id IS NOT NULL GROUP BY sleeper_id
) si ON si.sleeper_id = sc.platform_player_id
"""


def sleeper_add_drop_column(column: str) -> tuple[str, str] | None:
    """('add'|'drop', player key) for one of Sleeper's dynamic `adds.<key>`/`drops.<key>` columns,
    or `None` for any other column — including the bare, un-flattened `adds`/`drops` scalar columns
    `json_normalize` leaves behind (see the module docstring)."""
    if column.startswith("adds."):
        return "add", column[len("adds."):]
    if column.startswith("drops."):
        return "drop", column[len("drops."):]
    return None


def unpivot_sleeper_transactions(transactions: pd.DataFrame) -> pd.DataFrame:
    """`transactions` (Sleeper's wide shape, already filtered to the rows that count) turned into
    one row per (transaction, action, player) touched — see the module docstring for why this has
    to walk whatever `adds.*`/`drops.*` columns are actually present rather than naming any of
    them.
    """
    rows = []
    for column in transactions.columns:
        parsed = sleeper_add_drop_column(column)
        if parsed is None:
            continue
        action, platform_player_id = parsed
        touched = transactions[transactions[column].notna()]
        for transaction_id, created in zip(touched["transaction_id"], touched["created"]):
            rows.append({
                "transaction_id": transaction_id,
                "created": created,
                "action": action,
                "platform_player_id": platform_player_id,
            })
    return pd.DataFrame(rows, columns=["transaction_id", "created", "action", "platform_player_id"])


def sleeper_transaction_counts(touches: pd.DataFrame, since_ms: int) -> pd.DataFrame:
    """One row per player touched by a qualifying Sleeper transaction at or after `since_ms`
    (epoch milliseconds), with the count of distinct transactions that touched him. Distinct
    `transaction_id`, not row count, so a player who somehow shows up under both an add and a drop
    column on the same transaction still counts once — see the module docstring.
    """
    recent = touches[touches["created"] >= since_ms]
    return (
        recent.groupby("platform_player_id")["transaction_id"]
        .nunique()
        .reset_index(name="transaction_count")
    )


def _espn_position(position_id: int | None) -> str | None:
    return POSITION_IDS.get(position_id)


def build_waiver_transaction_activity() -> None:
    since_ms = int(
        (datetime.now(timezone.utc) - timedelta(days=TRAILING_WINDOW_DAYS)).timestamp() * 1000
    )

    con = duckdb.connect(str(WAREHOUSE_PATH))
    con.create_function("espn_position", _espn_position, ["BIGINT"], "VARCHAR")

    raw_transactions = con.execute(
        "SELECT * FROM sleeper_transactions WHERE status = 'complete' "
        "AND type IN ('waiver', 'free_agent')"
    ).df()
    touches = unpivot_sleeper_transactions(raw_transactions)
    sleeper_counts = sleeper_transaction_counts(touches, since_ms)

    sleeper_rows = con.execute(_SLEEPER_ACTIVITY_SQL).df()
    espn_rows = con.execute(_ESPN_ACTIVITY_SQL, [since_ms]).df()

    activity = pd.concat([sleeper_rows, espn_rows], ignore_index=True)[_OUTPUT_COLUMNS]
    con.execute("CREATE OR REPLACE TABLE waiver_transaction_activity AS SELECT * FROM activity")

    (unresolved,) = con.execute(
        "SELECT COUNT(*) FROM activity WHERE player_id IS NULL"
    ).fetchone()
    if unresolved:
        console.note(
            f"waiver_transaction_activity: {unresolved} rows have no player_id crosswalk"
        )

    con.close()
    console.table("waiver_transaction_activity", len(activity))


if __name__ == "__main__":
    build_waiver_transaction_activity()
