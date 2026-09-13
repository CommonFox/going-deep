"""How current the warehouse is, for display on every page of the web app.

Every number the app shows was computed by a warehouse rebuild and is exactly as current as that
rebuild. `src/draft/live.py` refuses outright once a board is too old, because a bad pick can't be
undone; nothing here is that irreversible, so this is a warning banner instead of a hard stop — the
drafter's rule, softened for a tool that gets checked repeatedly through the week rather than once
before a pick clock starts.
"""

from datetime import datetime, timedelta

from src.query import WAREHOUSE_PATH

# A day means the app was built with the last day's news in it — waiver claims and lineup calls
# both turn on injury/role news that can land at any hour.
MAX_WAREHOUSE_AGE = timedelta(days=1)


def warehouse_built_at() -> datetime:
    """When the warehouse was last written, which is when its numbers were computed."""
    if not WAREHOUSE_PATH.exists():
        raise RuntimeError(
            f"{WAREHOUSE_PATH} does not exist — run scripts/build_warehouse.sh before using this "
            "app."
        )
    return datetime.fromtimestamp(WAREHOUSE_PATH.stat().st_mtime)


def staleness_warning(
    built_at: datetime, now: datetime, max_age: timedelta = MAX_WAREHOUSE_AGE
) -> str | None:
    """None if the warehouse is fresh enough, else a message naming when it was built.

    Pure, so the rule is checkable without a clock or a file. The build time is in the message
    because "the warehouse is stale" isn't actionable and "the warehouse is from last Tuesday" is.
    """
    age = now - built_at
    if age <= max_age:
        return None
    return (
        f"Warehouse last built {built_at:%Y-%m-%d %H:%M}, {age.total_seconds() / 3600:.0f}h ago — "
        f"older than the {max_age.total_seconds() / 3600:.0f}h this app expects. Numbers may be "
        "out of date; run scripts/build_warehouse.sh to refresh."
    )
