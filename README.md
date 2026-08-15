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
  offer a free public API. Set `current_week` in the module before running in-season.
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

## Proprietary models (`src/gold/`)

- `src/gold/consensus.py` — builds two tables: `consensus_projections` (skill positions QB/RB/
  WR/TE/K, joined via the nflverse player ID crosswalk onto `gsis_id`) and
  `consensus_dst_projections` (team defenses, joined by normalized team abbreviation instead,
  since defenses aren't in the player crosswalk). Each is a median/floor (20th percentile)/
  ceiling (80th percentile) PPR projection per player or team, aggregated across every
  independent projection source above (ESPN, Sleeper/RotoWire, FFToday, CBS). Pure SQL over
  already-loaded tables — no fetch step, no network.
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
  injury-shortened or backup-role cameo doesn't distort the per-game rate. A feature-engineering
  building block, not a projection itself — meant to feed a future in-house predictive model. Pure
  SQL over already-loaded tables — no fetch step, no network.

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
