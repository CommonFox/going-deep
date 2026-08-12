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

Each data source gets its own module at `src/ffb/<source>.py`, exposing a `fetch_*`/`load_*`
function pair per table.

## Data sources

- `src/ffb/nfl_data.py` — nflverse data via `nfl_data_py`: weekly stats, schedules, rosters, snap
  counts, injuries, seasonal data, depth charts, player bios, Next Gen Stats, FTN charting data,
  and a cross-platform player ID crosswalk.
- `src/ffb/sleeper.py` — Sleeper's public league API (no auth required): league settings, rosters,
  users, weekly matchups (including starting lineups), transactions, current NFL state, and the
  full player dictionary. Set `LEAGUE_ID` in the module before running.
- `src/ffb/espn.py` — ESPN's fantasy API: league settings, teams, rosters, weekly matchups, and
  the player pool. Private leagues require `ESPN_S2` and `SWID` cookies from a logged-in browser
  session, set via a gitignored `.env` file (see `.env.example`). Set `LEAGUE_ID` and `SEASON` in
  the module before running.

## Environment

A Python 3.11 virtualenv lives at `.venv/` (gitignored):

```bash
source .venv/bin/activate
pip install -r requirements.txt
```

## Usage

Run a source module directly to fetch and load its tables into the warehouse:

```bash
python -m src.ffb.nfl_data
```

Query the warehouse with the DuckDB CLI or Python:

```bash
python -c "import duckdb; print(duckdb.connect('data/warehouse.duckdb').sql('SHOW TABLES'))"
```
