"""Print which fantasy matchups are still worth watching during Sunday or Monday night football.

    python -m src.gameday.storylines
    python -m src.gameday.storylines --week 3
    python -m src.gameday.storylines --day monday

A matchup is only worth a look once the early/late Sunday slate has cleared and what's left comes
down to whoever is playing tonight — that's the whole reason `--day` exists rather than a live
"has this game finished" check. `tonight_teams` reads that off the schedule instead: the last
kickoff of the day (>= 18:00) on a Sunday is the Sunday-night game, and any Monday game is a
Monday-night one. A matchup makes the list only if at least one side still has a starter on one of
those teams — one where both lineups are already fully played out is decided and not a storyline.

Like `src.draft.live`, this is the one part of `src/gameday/` that touches the world: it polls
Sleeper's matchups endpoint live (never archived — `src.silver.sleeper.fetch_matchups` is the
once-a-week archive step) and reads `sleeper_rosters`, `sleeper_users`, `sleeper_players`,
`sleeper_projections` and `schedules` read-only through `src.query.q`. Everything else here is
pure: payloads and frames in, dicts or a string out.
"""

import argparse
from datetime import datetime

import pandas as pd
import requests

from src.query import q
from src.silver.sleeper import LEAGUE_ID, SEASON
from src.silver.teams import normalize_team

BASE_URL = "https://api.sleeper.app/v1"
TIMEOUT_SECONDS = 10

# Games kicking off at or after this hour count as "tonight's" — the last slot of a Sunday slate
# (SNF) or any Monday game. Early/late Sunday windows (13:00, 16:05/16:25) fall below it.
NIGHT_GAMETIME_FLOOR = "18:00"

# Sleeper fills an unfilled starting slot with 0, not a player ID.
EMPTY_SLOT = {0, "0"}


def team_owners(rosters: pd.DataFrame, users: pd.DataFrame) -> dict[int, str]:
    """roster_id -> the name to show for that team: its Sleeper team name, or its owner's handle."""
    merged = rosters.merge(users, left_on="owner_id", right_on="user_id", how="left")
    owners = {}
    for _, row in merged.iterrows():
        team_name = row.get("metadata.team_name")
        name = team_name if pd.notna(team_name) and team_name else row["display_name"]
        owners[row["roster_id"]] = name
    return owners


def player_lookup(players: pd.DataFrame) -> dict[str, dict]:
    """player_id -> name/position/team, for every Sleeper player including team defenses.

    A defense's `full_name` is blank in Sleeper's own dictionary — only a real player carries one —
    so it falls back to first + last name, which for a defense is the city and the mascot.
    """
    lookup = {}
    for _, row in players.iterrows():
        full_name = row.get("full_name")
        name = full_name if pd.notna(full_name) and full_name else f"{row['first_name']} {row['last_name']}"
        team = row.get("team")
        lookup[row["player_id"]] = {
            "name": name,
            "position": row["position"],
            "team": normalize_team(team) if pd.notna(team) and team else None,
        }
    return lookup


def tonight_teams(
    schedule: pd.DataFrame, week: int, weekday: str, floor: str = NIGHT_GAMETIME_FLOOR
) -> set[str]:
    """Which NFL teams (normalized abbreviations) kick off at/after `floor` on `weekday`."""
    games = schedule[
        (schedule["week"] == week)
        & (schedule["weekday"] == weekday)
        & (schedule["gametime"] >= floor)
    ]
    teams = set(games["home_team"]) | set(games["away_team"])
    return {normalize_team(team) for team in teams}


def group_by_matchup(matchups: list[dict]) -> dict[int, list[dict]]:
    """Sleeper's flat per-roster rows, paired up by the `matchup_id` they share."""
    grouped: dict[int, list[dict]] = {}
    for row in matchups:
        grouped.setdefault(row["matchup_id"], []).append(row)
    return grouped


def remaining_starters(
    starters: list, players: dict, projections: dict, tonight: set[str]
) -> list[dict]:
    """Which of a lineup's starters are on a team that hasn't played tonight's game yet."""
    remaining = []
    for player_id in starters:
        if player_id in EMPTY_SLOT:
            continue
        info = players.get(player_id)
        if info is None or info["team"] not in tonight:
            continue
        remaining.append({
            "player_id": player_id,
            "name": info["name"],
            "position": info["position"],
            "team": info["team"],
            "projected": projections.get(player_id),
        })
    return remaining


def build_storylines(
    matchups: list[dict], owners: dict, players: dict, projections: dict, tonight: set[str]
) -> list[dict]:
    """One entry per matchup still in question, closest margin first.

    "In question" means at least one side has a starter left to play tonight — a matchup where
    both lineups are fully played out already can't move, and isn't a storyline. The two teams in
    each entry are ordered leading team first.
    """
    lines = []
    for matchup_id, rows in group_by_matchup(matchups).items():
        if len(rows) != 2:
            # A bye or an odd-sized league leaves a roster with no opponent; there's nothing to
            # compare it against.
            continue
        teams = []
        for row in rows:
            remaining = remaining_starters(row["starters"], players, projections, tonight)
            teams.append({
                "roster_id": row["roster_id"],
                "name": owners.get(row["roster_id"], f"Roster {row['roster_id']}"),
                "points": row["points"],
                "remaining": remaining,
                "remaining_projected": sum(p["projected"] or 0 for p in remaining),
            })
        if sum(len(team["remaining"]) for team in teams) == 0:
            continue
        teams.sort(key=lambda team: team["points"], reverse=True)
        lines.append({
            "matchup_id": matchup_id,
            "margin": abs(teams[0]["points"] - teams[1]["points"]),
            "teams": teams,
        })
    lines.sort(key=lambda line: (line["margin"], line["matchup_id"]))
    return lines


def render(storylines: list[dict], week: int, weekday: str) -> str:
    """The plain-text printout — one paragraph per matchup, closest first."""
    header = f"Week {week} {weekday}-night storylines"
    if not storylines:
        return f"{header}\n\nno matchups still have a starter left to play tonight.\n"

    lines = [header, ""]
    for line in storylines:
        leader, trailer = line["teams"]
        lines.append(
            f"{leader['name']} {leader['points']:.2f} vs {trailer['name']} {trailer['points']:.2f}"
            f"  (margin {line['margin']:.2f})"
        )
        for team in line["teams"]:
            if not team["remaining"]:
                lines.append(f"  {team['name']}: nobody left tonight")
                continue
            players_named = ", ".join(
                f"{p['name']} ({p['position']}, {p['team']})"
                + (f" — proj {p['projected']:.1f}" if p["projected"] is not None else "")
                for p in team["remaining"]
            )
            lines.append(f"  {team['name']} still has: {players_named}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _get(path: str):
    response = requests.get(f"{BASE_URL}{path}", timeout=TIMEOUT_SECONDS)
    response.raise_for_status()
    return response.json()


def fetch_matchups(league_id: str, week: int) -> list[dict]:
    """This week's matchups, live. Never archived — see `src.silver.sleeper.fetch_matchups` for
    the once-a-week version that is."""
    return _get(f"/league/{league_id}/matchups/{week}")


def current_week() -> int:
    """Sleeper's own idea of what week it is right now."""
    return _get("/state/nfl")["week"]


def load_context(week: int) -> dict:
    """Everything read-only from the warehouse: team names, the player dictionary, projections."""
    rosters = q("SELECT roster_id, owner_id FROM sleeper_rosters")
    users = q('SELECT user_id, display_name, "metadata.team_name" FROM sleeper_users')
    players = q(
        "SELECT player_id, full_name, first_name, last_name, position, team FROM sleeper_players"
    )
    projections = q(
        "SELECT sleeper_id, pts_half_ppr FROM sleeper_projections WHERE week = ?", [week]
    )
    schedule = q(
        "SELECT week, weekday, gametime, home_team, away_team FROM schedules WHERE season = ?",
        [SEASON],
    )
    return {
        "owners": team_owners(rosters, users),
        "players": player_lookup(players),
        "projections": dict(zip(projections["sleeper_id"], projections["pts_half_ppr"])),
        "schedule": schedule,
    }


def main(week: int | None = None, weekday: str | None = None) -> None:
    week = week or current_week()
    weekday = weekday or datetime.now().strftime("%A")
    if weekday not in ("Sunday", "Monday"):
        print(f"today is {weekday}, not Sunday or Monday — pass --day to preview one anyway")
        return

    context = load_context(week)
    tonight = tonight_teams(context["schedule"], week, weekday)
    if not tonight:
        print(f"no {weekday}-night game found in week {week}'s schedule")
        return

    matchups = fetch_matchups(LEAGUE_ID, week)
    lines = build_storylines(
        matchups, context["owners"], context["players"], context["projections"], tonight
    )
    print(render(lines, week, weekday))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Print which live Sleeper matchups are still in question tonight."
    )
    parser.add_argument(
        "--week", type=int, default=None,
        help="week to preview (default: Sleeper's current week)",
    )
    parser.add_argument(
        "--day", choices=["sunday", "monday"], default=None,
        help="which night to preview (default: today's, if it is Sunday or Monday)",
    )
    arguments = parser.parse_args()

    try:
        main(arguments.week, arguments.day.capitalize() if arguments.day else None)
    except requests.RequestException as error:
        print(f"Sleeper did not answer: {error}")
        raise SystemExit(1)
