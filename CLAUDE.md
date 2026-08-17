# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project overview

`going-deep` is a personal fantasy football tools project (self-hosted, in the spirit of
FantasyPros / DraftSharks / RotoWire), built around a raw-archive → DuckDB warehouse pipeline.
See `README.md` for the full architecture description.

## Environment

A Python 3.11 virtualenv lives at `.venv/` (gitignored). Activate it before running any Python:

```bash
source .venv/bin/activate
pip install -r requirements.txt
```

Dependencies are pinned in `requirements.txt` — keep it in sync when adding new packages.

## Conventions

- `src/` follows a medallion-style split:
  - `src/silver/<source>.py` — one module per raw data source, using plain functions (no classes
    unless clearly justified). Each table gets a `fetch_*`/`load_*` function pair:
    - `fetch_*` pulls from the network and saves the raw, unparsed response to
      `data/raw/<source>/...` (gitignored) as the source of truth.
    - `load_*` reads a raw file and loads it into `data/warehouse.duckdb` (gitignored),
      idempotently (e.g. `CREATE OR REPLACE TABLE ... AS SELECT * FROM read_parquet(...)`) and
      without ever hitting the network, so the warehouse can be rebuilt from the raw archive
      alone.
  - `src/gold/<model>.py` — proprietary/derived models built on top of already-loaded silver
    tables (e.g. `consensus.py`). Pure SQL/Python over the warehouse — no fetch step, no network.
- `if __name__ == "__main__":` in each source module runs the full fetch→load sequence for that
  source end to end.
- Console output goes through `src/console.py`, never a bare `print`:
  - `console.table(name, rows)` after writing a warehouse table — one line, always shown. This is
    the progress a build is read for.
  - `console.archived(path, rows)` after writing a raw file, and `console.note(msg)` for anything
    unusual (a skipped fetch, a source that returned nothing).
  - `@console.analysis` on any `_print_*`/`_report` helper that exists only to print a model
    report. `scripts/build_warehouse.sh` sets `GOING_DEEP_QUIET`, which no-ops those functions so a
    full build stays scannable; running the module directly still prints everything, and so does
    `build_warehouse.sh --verbose`. Put expensive report-only computation *inside* the decorated
    function so a quiet build skips the work, not just the printing.
- `python -m src.summary` lists every warehouse table and its row count; `--brief` gives the
  per-layer roll-up the build script ends with.
- One feature per branch, with frequent commits pushed to `origin` so work can resume from a
  different machine. Use conventional commits (`feat:`, `fix:`, `chore:`, `docs:`, etc.) for
  commit subjects.
- When adding a new data source, add it under `src/silver/`; when adding a new derived/proprietary
  model, add it under `src/gold/`. Follow the same fetch/load split and folder layout rather than
  inventing a new pattern.

## Working style

Implement changes directly — write and run the code yourself rather than coaching the user through
writing it. Verify changes actually work (e.g. run the fetch/load pipeline, check row counts) before
reporting a task done.
