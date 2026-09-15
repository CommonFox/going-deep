"""Waiver board page — issue #85, under the waiver-wire epic (#78).

Reads `waiver_rankings` (built by `src/gold/waiver_rankings.py`, #83) through `src.query.q()`
only, on every rerun — see `src/web/app.py`'s note on why a connection can't be held across
reruns.

`waiver_rankings` is already scoped to each league's current week (see its own module docstring),
so this page adds no week selector of its own. Switching league is what changes which rows are in
play at all: availability and scoring both differ per league, so the table is re-queried with a
fresh `WHERE league_key = ?` rather than filtered client-side out of one merged frame.

Weekly and rest-of-season points are surfaced as two independently-sortable columns rather than
one blended score, matching #85's own reasoning: a bye-week fill-in and a real role change can
rank oppositely on the two, and picking one for the user would hide exactly the disagreement this
page exists to show.
"""

import pandas as pd
import streamlit as st

from src.query import q

_LEAGUE_LABELS = {"sleeper": "Sleeper", "espn": "ESPN"}
_POSITIONS = ["QB", "RB", "WR", "TE"]


def _points_label(points: float) -> str:
    return "No projection" if pd.isna(points) else f"{points:.2f} pts"


st.title("Waiver Board")

leagues = q("SELECT DISTINCT league_key, season, week FROM waiver_rankings ORDER BY league_key")
if leagues.empty:
    st.info("No waiver_rankings data yet — run scripts/build_warehouse.sh.")
    st.stop()

league_key = st.selectbox(
    "League", leagues["league_key"], format_func=lambda key: _LEAGUE_LABELS.get(key, key.title()),
)
league_row = leagues.loc[leagues["league_key"] == league_key].iloc[0]
season, week = int(league_row["season"]), int(league_row["week"])
st.caption(f"Week {week} · {season} season")

col1, col2 = st.columns([2, 1])
positions = col1.multiselect("Position", _POSITIONS, default=_POSITIONS)
sort_by = col2.radio("Sort by", ["This week", "Rest of season"], horizontal=True)
sort_column = "weekly_points" if sort_by == "This week" else "ros_points"

board = q(
    "SELECT * FROM waiver_rankings WHERE league_key = ? AND season = ? AND week = ?",
    [league_key, season, week],
)
board = board[board["position"].isin(positions)]

if board.empty:
    st.info("No available players match this filter.")
    st.stop()

ranked = board.sort_values(sort_column, ascending=False, na_position="last")

for row in ranked.itertuples():
    with st.container(border=True):
        cols = st.columns([3, 1, 2, 2, 3])
        cols[0].markdown(f"**{row.player_name}**")
        cols[1].write(row.position)
        cols[2].write(f"Week: {_points_label(row.weekly_points)}")
        cols[3].write(f"ROS: {_points_label(row.ros_points)}")

        if pd.isna(row.replacement_level_points):
            cols[4].caption("Player not yet identified — no replacement-level context")
        else:
            cols[4].caption(
                f"Replacement level ({row.position}, top {int(row.starters_at_position)} "
                f"starters): {row.replacement_level_points:.2f} pts"
            )
