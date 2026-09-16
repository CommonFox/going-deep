# Export format spike (#125)

A go/no-go on the static-export architecture for #111 (front end rebuild): can everything the app
needs to show fit in a static JSON payload small enough to ship to a browser? Measured against the
real warehouse (2026, week 2, both live leagues) rather than estimated.

The throwaway measurement script and a working Vite/React scaffold that loads one real exported
file live on `spike/125-export-format` (not merged here, per the `/prototype` skill's rule for
this ticket) — see that branch's `spikes/125-export-format/README.md` to reproduce.

## 1. What is the payload?

| Table | rows | raw JSON | gzip |
|---|---:|---:|---:|
| `optimal_lineup` (whole table, both leagues, 18 weeks) | 360 | 85.7 KB | 3.4 KB |
| `optimal_lineup_bench` (whole table) | 256 | 34.9 KB | 2.0 KB |
| `my_roster` (whole table) | 32 | 3.1 KB | 0.7 KB |
| `ros_points` (whole table) | 1,446 | 289.4 KB | 37.7 KB |
| `waiver_rankings` (whole table) | 4,661 | 1.48 MB | 71.4 KB |
| `weekly_projections` (whole table, 18 weeks) | 29,024 | 7.41 MB | 494.2 KB |
| **Sum of all six, whole tables** | | **9.30 MB** | **609.4 KB** |

Even the naive worst case — export every table in full and ship it all on first load — is 609 KB
gzipped. That is comfortably inside a "interactive in a couple seconds on cellular" budget on its
own. The real per-page numbers are smaller still:

| Page slice (one real league, one real week) | rows | raw | gzip |
|---|---:|---:|---:|
| Lineup optimizer (`optimal_lineup` + `optimal_lineup_bench`, sleeper/2026/wk2) | 16 | 3.2 KB | 0.7 KB |
| Waiver board, sleeper (the larger league's free-agent pool) | 3,853 | 1.22 MB | 51.7 KB |
| Waiver board, espn (the smaller league) | 808 | 268.9 KB | 20.3 KB |
| `weekly_projections`, one week, not currently read by any page | 1,616 | 437.5 KB | 51.7 KB |

**Verdict: the payload budget is not a constraint.** Nothing here comes close to threatening a
phone-on-cellular load, whole-table or per-page. The architecture is a go on payload size alone —
the remaining questions are about shape and freshness, not whether it fits.

## 2. How is it keyed?

**One file per (table, league_key, season, week)** for the two week-scoped tables the live pages
read (`optimal_lineup`, `optimal_lineup_bench`, `waiver_rankings`), e.g.
`optimal_lineup/sleeper/2026-2.json`. One file per (table, league_key) for the tables that carry no
week axis at all — `my_roster` and `ros_points` are single current snapshots, not a week-indexed
series (see the note on that in §4). `weekly_projections` is not league-specific, so it keys as one
file per (season, week).

This was close between two options and the deciding argument is that **both existing Streamlit
pages already query this way** — `lineup_optimizer.py` and `waiver_board.py` both scope every
`src.query.q()` call to a specific `league_key`/`season`/`week` (`src/web/pages/lineup_optimizer.py`,
`src/web/pages/waiver_board.py`). A per-(league, week) export is the SPA fetching exactly the slice
the page was always going to ask for, with the current-week/current-league selector staying a
client-side choice of *which file*, not a filter over a bigger one. The "one file per table"
alternative would mean shipping and filtering out 17 of 18 weeks' worth of `waiver_rankings` (71 KB
gzipped) to render one — cheap in absolute terms here, but it's the wrong direction as
`weekly_player_context` (#124) lands and tables get wider.

## 3. What is the freshness contract?

A `manifest.json`, fetched with `cache: 'no-store'` so it can never be answered from a stale cached
copy, carrying:

- `built_at` — an explicit ISO timestamp written at export time, not inferred from a file mtime or
  an HTTP `Last-Modified` header (a CDN can rewrite either, and Vercel's default caching would).
- `schema_version` — an integer, bumped on any breaking shape change, so the SPA can refuse to
  render mismatched fields rather than silently showing blanks.
- `available` — which (table, league, season, week) combinations actually have files, so the SPA
  doesn't need to probe with 404s to know what exists.

The staleness *rule* carries over unchanged from `src/web/warehouse_status.py`'s
`MAX_WAREHOUSE_AGE = timedelta(days=1)` and `staleness_warning()` — same reasoning (`"waiver claims
and lineup calls both turn on injury/role news that can land at any hour"`), same threshold, just
evaluated against `manifest.built_at` instead of a file's mtime. The scaffold on the spike branch
ports this rule directly (`stalenessWarning()` in `App.tsx`) against the real exported timestamp.
"Impossible to serve stale silently" is the `no-store` fetch plus this banner — the manifest itself
can go stale (a CDN could cache it against instructions), but the banner computation runs client-side
against whatever `built_at` actually arrived, so a stale manifest still produces a visible warning
rather than a silently wrong page.

## 4. Does anything need a query the export can't precompute?

**No** — checked against every page in every wave-3 epic (#109, #110, #112, #113), not just the two
that exist today:

- Every page scopes to one league and one week (or one league's roster, ~15–17 players). Nothing
  reads across the whole player universe. The waiver board's worst case — one league's entire
  free-agent pool — is 3,853 rows and 52 KB gzipped, small enough to sort and filter client-side
  with no query engine at all.
- The trade tool (#113) is the one epic that sounded like it might need real querying ("finding a
  trade" scans combinations), but its own ticket scopes that to opponent rosters *within one
  league* — the same small-roster shape, times up to 9 opponents. Still no free-text search over
  the full player pool.
- Nothing in any epic asks for cross-league or cross-season querying in the browser.

DuckDB-WASM over an exported Parquet file remains a real option if a future page genuinely needs
open-ended free-text player search, but nothing currently planned needs it, so it stays a footnote
rather than a decision this spike has to make.

## 5. SPA scaffold

`npm create vite@latest -- --template react-ts`, no component library adopted yet — deferred to
#127 (design system and app shell), which is explicitly out of scope here. The scaffold on the
spike branch fetches the manifest and one real `waiver_rankings` export and renders a sorted table;
`npm run build` and `npm run preview` both run clean against the real files.

## Finding worth flagging to #126/#128

**87% of rows in the sleeper league's week-2 `waiver_rankings` export have `player_id IS NULL`**
(3,343 of 3,853) — the deep free-agent tail is mostly unidentified players. `waiver_board.py`
doesn't need a stable per-row identity today (`itertuples()` over a DataFrame), but a React port
does (list keys, at minimum), and `player_id` alone cannot be that key. The spike's own component
works around this with a `player_name + position + index` fallback key — worth deciding for real
in #128 rather than re-discovering it there.

## Go/no-go

**Go.** Static export plus a React SPA is confirmed on payload size, has a settled key scheme that
matches how the existing pages already query, a freshness contract with the same rule the Streamlit
app already applies, and no page across four epics needs a browser-side query engine. Nothing here
motivates the DuckDB-WASM fallback or a re-think of the architecture in #111's problem statement.
