"""Canonical NFL team abbreviations, matching the convention nfl_data_py uses elsewhere in this
warehouse (e.g. the `rosters` table) — "LA" (not "LAR") for the Rams, "JAX" (not "JAC") for the
Jaguars. Projection sources represent team defenses inconsistently (full team names, bare
nicknames, or a divergent abbreviation), so `normalize_team` maps any of those down to the
canonical abbreviation, for joining DST rows across sources by team instead of a player ID.
"""

NICKNAME_TO_ABBR = {
    "Cardinals": "ARI", "Falcons": "ATL", "Ravens": "BAL", "Bills": "BUF",
    "Panthers": "CAR", "Bears": "CHI", "Bengals": "CIN", "Browns": "CLE",
    "Cowboys": "DAL", "Broncos": "DEN", "Lions": "DET", "Packers": "GB",
    "Texans": "HOU", "Colts": "IND", "Jaguars": "JAX", "Chiefs": "KC",
    "Chargers": "LAC", "Rams": "LA", "Raiders": "LV", "Dolphins": "MIA",
    "Vikings": "MIN", "Patriots": "NE", "Saints": "NO", "Giants": "NYG",
    "Jets": "NYJ", "Eagles": "PHI", "Steelers": "PIT", "Seahawks": "SEA",
    "49ers": "SF", "Buccaneers": "TB", "Titans": "TEN", "Commanders": "WAS",
}  # fmt: skip

ABBR_ALIASES = {
    "LAR": "LA", "JAC": "JAX", "WSH": "WAS", "GNB": "GB", "NOR": "NO",
    "SFO": "SF", "TAM": "TB", "NWE": "NE", "KAN": "KC", "LVR": "LV",
}  # fmt: skip


def normalize_team(value: str) -> str:
    """Normalize a team name, nickname, or abbreviation to the canonical abbreviation."""
    value = value.strip()
    if value.upper() in ABBR_ALIASES:
        return ABBR_ALIASES[value.upper()]
    if value.upper() in NICKNAME_TO_ABBR.values():
        return value.upper()
    nickname = value.replace("D/ST", "").strip().split()[-1]
    return NICKNAME_TO_ABBR.get(nickname, value.upper())
