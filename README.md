# going-deep

A personal fantasy football tools project — self-hosted stats, analysis, and insights, in the
spirit of FantasyPros / DraftSharks / RotoWire, built for one user's own leagues.

## Architecture

Data flows through a raw-archive → DuckDB warehouse pipeline:

1. **Fetch** — pull data from a source (e.g. nflverse via `nfl_data_py`) and save the raw,
   unparsed response to `data/raw/<source>/...` (gitignored). This raw archive is the source of
   truth for rebuilding the warehouse.
2. **Load** — read the raw files and load them into `data/warehouse.duckdb` (gitignored), a
   single local DuckDB file. Loads are idempotent and never hit the network, so the warehouse can
   be rebuilt from the raw archive at any time, on any machine.

The `src/` layout follows a medallion-style split: `silver/` holds one module per raw data
source, each exposing a `fetch_*`/`load_*` function pair per table; `gold/` holds modules built on
top of already-loaded silver tables — proprietary/derived models with no fetch step and no
network access of their own.

## Data sources (`src/silver/`)

- `src/silver/nfl_data.py` — nflverse data via `nfl_data_py`: weekly stats, schedules, rosters,
  snap counts, injuries, seasonal data, depth charts, player bios, Next Gen Stats, FTN charting
  data, PFR advanced pass/rush stats, and a cross-platform player ID crosswalk.
- `src/silver/sleeper.py` — Sleeper's public league API (no auth required): league settings,
  rosters, users, weekly matchups (including starting lineups), transactions, current NFL state,
  and the full player dictionary. Set `LEAGUE_ID` in the module before running.
- `src/silver/espn.py` — ESPN's fantasy API: league settings, teams, rosters, weekly matchups and
  boxscores, the player pool (with ownership %, ADP, and projections), and transactions. Private
  leagues require `ESPN_S2` and `SWID` cookies from a logged-in browser session, set via a
  gitignored `.env` file (see `.env.example`). Set `LEAGUE_ID` and `SEASON` in the module before
  running.
- `src/silver/fantasypros.py` — FantasyPros consensus expert rankings (ECR): preseason overall
  draft rankings (standard/half-PPR/PPR) and in-season weekly rankings by position. No auth
  required; extracts the `ecrData` JSON embedded in FantasyPros' rankings pages, since they don't
  offer a free public API. Set `current_week` in the module before running in-season. Also loads
  `fantasypros_adp`: historical average draft position by season, from CSVs manually downloaded
  from FantasyPros' ADP page (a client-rendered app with no embeddable data, unlike the rankings
  pages) and dropped into `data/raw/fantasypros/` — the one silver table in this warehouse with a
  human "fetch" step instead of a network call. `team`/`bye` reflect the player's team as of
  whenever the CSV was downloaded, not their actual historical team that season, and are sparse
  for older seasons (FantasyPros' own archive quality); the ADP rank/value itself is genuine
  historical data.
- `src/silver/fantasyfootballcalculator.py` — FantasyFootballCalculator's historical ADP by
  scoring format (standard/half-PPR/PPR) and season, back to 2015, via FFC's free public JSON API.
  No auth required. FFC's `teams` query parameter is cosmetic — verified it returns identical
  underlying ADP values regardless of team count — so this is one pooled dataset per format/season,
  not genuinely split by league size. A second, independent ADP source alongside
  `fantasypros_adp`, so no single source's platform-specific bias dominates.
- `src/silver/fftoday.py` — FFToday's own season-long fantasy point projections (standard/half-PPR/
  PPR) by position, including DST. No auth required; parses the plain HTML projections table,
  since FFToday doesn't offer an API. Rate-limits aggressive scraping, so requests are spaced out.
- `src/silver/cbs.py` — CBS Sports' own season-long fantasy point projections (standard/PPR) by
  position, including DST. No auth required; parses the plain HTML projections table, since CBS
  doesn't offer a free API.
- `src/silver/teams.py` — shared NFL team-abbreviation normalizer, not a data source itself. Each
  projection site represents team defenses differently (a full name, a bare nickname, or a
  non-canonical abbreviation like "LAR"); this maps any of those to the abbreviation nflverse
  uses elsewhere in this warehouse (e.g. "LA" for the Rams), so DST rows can be joined by team.
- `src/silver/players.py` — shared player-name normalizer, not a data source itself. Reproduces
  nflverse's own `merge_name` convention (lowercase, strip punctuation/suffixes) closely enough to
  join FantasyPros'/FFC's free-text ADP names onto the `ids` crosswalk's `merge_name`, for sources
  that carry no platform ID the crosswalk already knows.

## Proprietary models (`src/gold/`)

- `src/gold/consensus.py` — builds two tables: `consensus_projections` (skill positions QB/RB/
  WR/TE/K, joined via the nflverse player ID crosswalk onto `gsis_id`) and
  `consensus_dst_projections` (team defenses, joined by normalized team abbreviation instead,
  since defenses aren't in the player crosswalk). Each is a median/floor (20th percentile)/
  ceiling (80th percentile) PPR projection per player or team, aggregated across every
  independent projection source above (ESPN, Sleeper/RotoWire, FFToday, CBS) plus the in-house
  model below. The in-house model's contribution is scoped to the season the other four sources
  actually represent (derived from their own data), not its own most-recent `target_season` —
  nflverse-fed `inhouse_projections` can lag the external sites' current-season projections, so a
  stale in-house number drops out of the blend instead of silently mixing with four current ones.
  Pure SQL over already-loaded tables — no fetch step, no network.
- `src/gold/adp_consensus.py` — builds `adp_consensus`: a consensus average draft position per
  player-season (QB/RB/WR/TE), blending `fantasypros_adp` and `ffc_adp` (FantasyFootballCalculator,
  PPR format) onto a shared `gsis_id` via the `merge_name` normalization in `players.py`, since
  neither ADP source carries a platform ID the nflverse crosswalk already knows the way ESPN/
  Sleeper/CBS projections do in `consensus.py`. Each site counts as one vote regardless of how many
  scoring formats it offers — FFC's standard/half-PPR numbers are kept as their own columns for
  reference but excluded from the blend, so one site's multiple formats can't outvote the other
  site's single blended number. Pure SQL/Python over already-loaded tables — no fetch step, no
  network.
- `src/gold/offensive_line.py` — builds `offensive_line_grades`: a per-team-per-season 0-100
  offensive line grade from PFR's advanced pass/rush stats, combining pass-block (QB pressure
  rate allowed, weighted by pass attempts) and run-block (RB/FB yards before contact per rush
  attempt) into one score. Pure SQL over already-loaded tables — no fetch step, no network.
- `src/gold/skill_position_grades.py` — builds `skill_position_grades`: a per-team-per-season
  0-100 corps-strength grade for WR, TE, and RB, from nflverse Next Gen Stats "over expectation"
  metrics (separation and YAC over expectation for WR/TE, rush yards over expectation for RB) so
  the grade reflects talent rather than just recycling the volume/scoring this warehouse is
  ultimately projecting. Pure SQL over already-loaded tables — no fetch step, no network.
- `src/gold/player_baselines.py` — builds `player_weighted_baselines`: a per-player,
  per-target-season PPR points-per-game baseline (QB/RB/WR/TE) from nflverse weekly stats, looking
  back up to 4 seasons and weighting more recent seasons more heavily (1.0/0.9/0.8/0.7). A season
  only counts toward the baseline if the player played at least 6 games in it, so an
  injury-shortened or backup-role cameo doesn't distort the per-game rate. Also outputs
  `weighted_games_per_season`, the same recency-weighted average applied to games played instead —
  a durability signal. A feature-engineering building block, not a projection itself. Pure SQL
  over already-loaded tables — no fetch step, no network.
- `src/gold/league_settings.py` — builds `league_settings`: one row per league (Sleeper, ESPN)
  normalizing each platform's scoring rules (points per reception/yard/TD/turnover, etc.) and
  starting-roster construction (team count and QB/RB/WR/TE/FLEX/superflex/bench/IR slot counts)
  into a shared schema. Sleeper's settings arrive as flat columns; ESPN's arrive as a nested array
  of `{statId, points}` items and a slot-id-keyed lineup dict, both keyed by undocumented numeric
  IDs (mapped here using the cwendt94/espn-api project's reference tables, spot-checked against
  this league's actual raw settings). Exists so scoring-sensitive models (e.g. league-winning-RB
  thresholds, points-over-replacement) can run one formula per league and get a league-appropriate
  number back instead of a constant tuned to one scoring format. Pure SQL/Python over already-
  loaded tables — no fetch step, no network.
- `src/gold/inhouse_projections.py` — builds `inhouse_projections`: a home-grown PPG projection
  from a gradient-boosted model (scikit-learn's `HistGradientBoostingRegressor`), trained on a
  shift-based setup — `player_weighted_baselines` plus prior-season `offensive_line_grades`/
  `skill_position_grades` as features, actual next-season PPG as the label — then converted to a
  season-total point projection via the `weighted_games_per_season` durability signal. The only
  `src/gold` module that isn't pure SQL (Python/pandas/scikit-learn over already-loaded tables
  instead), and the only one with a genuine train/holdout split, printing out-of-sample MAE/R2 on
  each run, alongside out-of-sample permutation feature importance — which preseason signals
  (recency-weighted history, durability, OL/skill-position grades) actually move the prediction,
  not just which ones are in the feature list. Feeds into `consensus.py` as a fifth projection
  source.
- `src/gold/points_over_replacement.py` — builds `points_over_replacement`: each skill-position
  player's season-total fantasy points, recomputed from nflverse weekly stats under each league's
  own `league_settings` scoring coefficients (not nflverse's canned PPR formula), minus that
  league-season-position's replacement level. Replacement level is a combined-flex-pool
  Value-Based-Drafting calculation: dedicated starters (`team_count x slots`) are filled first per
  position, then whatever's left over from RB/WR/TE is pooled, ranked by points, and FLEX slots are
  filled from that pool regardless of position — so a position that wins more flex spots in a given
  season automatically gets a deeper replacement level, with no hardcoded split. Pure SQL/Python
  over already-loaded tables — no fetch step, no network.
- `src/gold/boom_bust.py` — builds `boom_bust`: classifies each skill-position player-season into
  a preseason-ADP-relative outcome bucket (Boomed/Returned on ADP/Fine/Busted/Got Injured/No
  Preseason ADP), by converting both `points_over_replacement` and `adp_consensus`'s
  `consensus_adp` into percentiles within the same season-position pool of ADP-tracked players, so
  the comparison is scoring-format- and league-size-independent rather than a fixed PPG number or
  ADP-spot count. Boom/Bust/Fine/Returned split at symmetric +/-20-percentile-point bands around
  "met expectation"; fewer than 12 games played overrides those bands as Got Injured, but only for
  players who had preseason draft capital to begin with. Pure SQL over already-loaded tables — no
  fetch step, no network.
- `src/gold/breakout_candidates.py` — builds `breakout_candidates`: ranks `inhouse_projections`'
  already-ADP-blind prediction into a position-relative percentile per target season and lines it
  up against `adp_consensus`'s preseason percentile, so a player the model likes that the real-world
  draft market doesn't (or hasn't seen at all — `consensus_adp` stays NULL rather than being
  coerced to a default) shows up directly as a high `predicted_delta`. League-agnostic by design —
  ranks `inhouse_projections`' raw PPG-based projection as-is rather than retraining against either
  league's own scoring the way `points_over_replacement`/`boom_bust` do. No bucketing and no
  games-played check the way `boom_bust` has, since both describe an *actual* outcome that, for the
  live target season, hasn't happened yet — `predicted_delta` is left as a continuous score to
  sort/filter directly. Pure SQL over already-loaded tables — no fetch step, no network.

## Environment

A Python 3.11 virtualenv lives at `.venv/` (gitignored):

```bash
source .venv/bin/activate
pip install -r requirements.txt
```

## Usage

Run a source module directly to fetch and load its tables into the warehouse:

```bash
python -m src.silver.nfl_data
```

To rebuild the full warehouse from scratch (e.g. on a new machine), run every source in
sequence:

```bash
./scripts/build_warehouse.sh
```

Query the warehouse with the DuckDB CLI or Python:

```bash
python -c "import duckdb; print(duckdb.connect('data/warehouse.duckdb').sql('SHOW TABLES'))"
```
