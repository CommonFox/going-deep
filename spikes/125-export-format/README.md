# Spike: export format, payload budget, SPA scaffold (#125)

THROWAWAY. Per the `/prototype` skill's rule for this ticket, this directory is not folded into
main — it is the primary source behind the decisions written up in `docs/export-format-spike.md`
on `feat/export-format-spike`. Do not build on top of this; #126 builds the real `src/export/`
and #127 builds the real app shell.

## What's here

- `measure_and_export.py` — reads the real warehouse via `src.query.q()`, prints raw/gzip sizes
  for whole tables and for realistic per-page slices, and writes one real payload straight into
  `spa/public/data/` for the scaffold to load. That path is gitignored like everything under
  `data/` elsewhere in this repo, so it has to be regenerated, not checked out.
- `spa/` — `npm create vite -- --template react-ts`, with `App.tsx` rewritten to fetch
  `/data/manifest.json` (no-store) and `/data/waiver_rankings/sleeper/2026-2.json`, then render a
  real table sorted by ROS points. Proves the fetch-a-JSON-file-and-render architecture end to end
  against real data, including the null-`player_id` free-agent tail (see the write-up).

## Running it

```
source .venv/bin/activate                                          # from the repo root
PYTHONPATH=. python3 spikes/125-export-format/measure_and_export.py  # writes spa/public/data/
cd spikes/125-export-format/spa
npm install
npm run build && npm run preview   # or npm run dev
```
