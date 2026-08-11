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

- One module per data source at `src/ffb/<source>.py`, using plain functions (no classes unless
  clearly justified).
- Each table gets a `fetch_*`/`load_*` function pair:
  - `fetch_*` pulls from the network and saves the raw, unparsed response to
    `data/raw/<source>/...` (gitignored) as the source of truth.
  - `load_*` reads a raw file and loads it into `data/warehouse.duckdb` (gitignored), idempotently
    (e.g. `CREATE OR REPLACE TABLE ... AS SELECT * FROM read_parquet(...)`) and without ever
    hitting the network, so the warehouse can be rebuilt from the raw archive alone.
- `if __name__ == "__main__":` in each source module runs the full fetch→load sequence for that
  source end to end.
- One feature per branch, with frequent commits pushed to `origin` so work can resume from a
  different machine. Use conventional commits (`feat:`, `fix:`, `chore:`, `docs:`, etc.) for
  commit subjects.
- When adding a new data source, follow this same fetch/load split and folder layout rather than
  inventing a new pattern.

## Working style

Implement changes directly — write and run the code yourself rather than coaching the user through
writing it. Verify changes actually work (e.g. run the fetch/load pipeline, check row counts) before
reporting a task done.
