"""`normalize_team` — the one existing pure function the live-draft work leans on.

Ticket #31 resolves team defenses to Sleeper IDs by abbreviation, because a defense has no player
ID anywhere. That join only works if the board's "LA" and Sleeper's "LAR" are already the same
string by the time it runs, so these cases pin the mapping down before anything depends on it.
"""

from src.silver.teams import normalize_team


def test_canonical_abbreviation_passes_through():
    assert normalize_team("KC") == "KC"


def test_divergent_abbreviation_folds_to_canonical():
    # The exact collision the draft board hits: the board writes LA, Sleeper writes LAR.
    assert normalize_team("LAR") == "LA"
    assert normalize_team("JAC") == "JAX"
    assert normalize_team("WSH") == "WAS"


def test_arizona_folds_to_the_canonical_abbreviation():
    # The draft board carries both spellings at once: `rosters` writes AZ for every Arizona player
    # while Sleeper writes ARI for the defense, so without this the same club joins to itself as
    # two different teams and half of it goes missing.
    assert normalize_team("AZ") == "ARI"


def test_relocated_franchise_folds_to_its_current_abbreviation():
    assert normalize_team("OAK") == "LV"
    assert normalize_team("SD") == "LAC"
    assert normalize_team("STL") == "LA"


def test_nickname_resolves():
    assert normalize_team("Rams") == "LA"
    assert normalize_team("49ers") == "SF"


def test_full_team_name_resolves():
    assert normalize_team("Los Angeles Rams") == "LA"
    assert normalize_team("San Francisco 49ers") == "SF"


def test_defense_suffix_is_stripped_before_matching():
    assert normalize_team("Rams D/ST") == "LA"


def test_case_and_surrounding_whitespace_do_not_matter():
    assert normalize_team("  lar  ") == "LA"


def test_unrecognised_value_is_uppercased_rather_than_raising():
    # A source inventing a code should surface as an unmatched row downstream, not an exception
    # in the middle of a build.
    assert normalize_team("xyz") == "XYZ"
