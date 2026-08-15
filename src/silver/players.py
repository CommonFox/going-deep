"""Shared player-name normalization, mirroring nflverse's own `merge_name` convention.

Not a data source itself. FantasyPros' ADP CSVs and FantasyFootballCalculator's API give player
names as free text with no platform ID `ids` (the nflverse player crosswalk loaded by
nfl_data.py) already knows, unlike ESPN/Sleeper/CBS projections in consensus.py, which carry a
native platform ID the crosswalk can join through directly.

This reproduces nflverse's own `merge_name` transform (lowercase, strip periods/apostrophes, strip
Jr/Sr/II/III/IV/V suffixes, collapse whitespace) closely enough to join on it: applying this to
every name in `ids` and comparing against `ids.merge_name` itself matches on 99.75% of rows
(12,441/12,472). The remaining misses are genuine nickname aliasing nflverse's crosswalk already
knows about (e.g. "Sauce Gardner" -> "Ahmad Gardner", "Michael Woods" -> "Mike Woods") that no
regex could reproduce.
"""

import re

_SUFFIX_RE = re.compile(r"\s+(jr|sr|ii|iii|iv|v)\.?$", re.IGNORECASE)
_PUNCT_RE = re.compile(r"[.'’]")


def merge_name(name: str) -> str:
    name = name.lower()
    name = _PUNCT_RE.sub("", name)
    name = _SUFFIX_RE.sub("", name)
    return re.sub(r"\s+", " ", name).strip()
