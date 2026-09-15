"""Lineup optimizer page — issue #90, under the lineup-optimizer epic (#86).

Reads `optimal_lineup` and `optimal_lineup_bench` (both built by `src/gold/optimal_lineup.py`,
#89) through `src.query.q()` only, plus `schedules` to work out which week is "current" — this
page recomputes nothing about a player's value, matching #86's own reasoning for why the lineup
optimizer is a gold model rather than a live tool: a roster doesn't change mid-week, so there is
nothing here that needs today's data beyond the date itself.

`optimal_lineup` spans the whole season (every week `weekly_projections` covers), not just the
week ahead, so "current week" is resolved from `schedules` here rather than read off the table: the
earliest week whose games aren't all finished yet.
"""

from datetime import date

import streamlit as st

from src.query import q

_LEAGUE_LABELS = {"sleeper": "Sleeper", "espn": "ESPN"}

# Display order for starting slots: skill positions, then FLEX/SUPERFLEX, then the rest. A slot
# label not in this list (there shouldn't be one) sorts after everything named.
_SLOT_ORDER = ["QB", "RB", "WR", "TE", "FLEX", "SUPERFLEX", "K", "DST", "DEF", "P"]


def _slot_sort_key(slot: str) -> tuple[int, int]:
    prefix = slot.rstrip("0123456789")
    number = slot[len(prefix):]
    rank = _SLOT_ORDER.index(prefix) if prefix in _SLOT_ORDER else len(_SLOT_ORDER)
    return rank, int(number) if number else 0


def _current_week(season: int) -> int:
    """The earliest week this season whose games aren't all finished yet — the week a drafter is
    actually setting a lineup for today, not the warehouse's last-built week."""
    weeks = q(
        "SELECT week, MAX(gameday) AS ends FROM schedules WHERE season = ? "
        "GROUP BY week ORDER BY week",
        [season],
    )
    today = date.today().isoformat()
    upcoming = weeks[weeks["ends"] >= today]
    return int(upcoming["week"].min()) if not upcoming.empty else int(weeks["week"].max())


st.title("Lineup Optimizer")

leagues = q("SELECT DISTINCT league_key, season FROM optimal_lineup ORDER BY league_key")
if leagues.empty:
    st.info("No optimal_lineup data yet — run scripts/build_warehouse.sh.")
    st.stop()

league_key = st.selectbox(
    "League", leagues["league_key"], format_func=lambda key: _LEAGUE_LABELS.get(key, key.title()),
)
season = int(leagues.loc[leagues["league_key"] == league_key, "season"].max())
week = _current_week(season)
st.caption(f"Week {week} · {season} season")

lineup = q(
    "SELECT * FROM optimal_lineup WHERE league_key = ? AND season = ? AND week = ?",
    [league_key, season, week],
)
bench = q(
    "SELECT * FROM optimal_lineup_bench WHERE league_key = ? AND season = ? AND week = ?",
    [league_key, season, week],
)

if lineup.empty:
    st.info(f"No lineup data for week {week} yet — this league's projections may not reach it.")
    st.stop()

lineup = lineup.sort_values(by="slot", key=lambda col: col.map(_slot_sort_key))

st.subheader("Starters")
for row in lineup.itertuples():
    with st.container(border=True):
        cols = st.columns([1, 3, 2, 4])
        cols[0].markdown(f"**{row.slot}**")

        if row.player_id is None:
            cols[1].markdown("*Empty — not enough eligible players*")
            continue

        cols[1].write(row.player_name)
        cols[2].write(f"{row.projected_points:.2f} pts")
        if row.is_close_call:
            gap = row.projected_points - row.bench_projected_points
            cols[3].warning(
                f"Close call vs. **{row.bench_player_name}** "
                f"({row.bench_projected_points:.2f} pts, -{gap:.2f})",
                icon="⚠️",
            )

missing = bench[bench["projected_points"].isna()]
if not missing.empty:
    names = ", ".join(f"{row.player_name} ({row.position})" for row in missing.itertuples())
    st.caption(f"No projection this week, so left out of consideration entirely: {names}")

st.subheader("Bench")
available = bench[bench["projected_points"].notna()].sort_values(
    "projected_points", ascending=False
)
if available.empty:
    st.caption("Nothing left on the bench with a projection this week.")
else:
    st.dataframe(
        available[["player_name", "position", "projected_points"]].rename(columns={
            "player_name": "Player", "position": "Pos", "projected_points": "Projected points",
        }),
        hide_index=True,
        width="stretch",
    )
