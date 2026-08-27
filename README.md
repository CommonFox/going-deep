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
  data, PFR advanced pass/rush stats, and a cross-platform player ID crosswalk. Most feeds are a
  record of games already played and stop at the last completed season, but schedules, depth-chart
  snapshots and rosters all describe a season *before* it's played — the schedule is published in
  May, snapshots run from the previous March through the summer, and preseason rosters carry
  `draft_number`/`years_exp` — so those three are fetched a year further forward. That is what
  gives `inhouse_projections` a role signal and a rookie draft board for the season it projects.
  Bump `_UPCOMING_SEASON` once a year. Also loads `pbp_punts`: every punt play since 2015, read
  straight from nflverse's play-by-play release with rows and columns pruned at fetch time (~2k
  punts a season out of ~50k plays, against ~370 columns). Play-by-play is the only feed carrying
  where a punt came to rest, which is what "punts inside the 10" — a scoring category in the ESPN
  league and in no per-player feed anywhere — has to be derived from.
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
  Alongside the blend, both tables expose each source's own number as its own column
  (`espn_points`, `sleeper_points`, `cbs_points`, `fftoday_points`, plus `inhouse_points` on the
  player table), so any consensus row can be traced back to what each site actually said.
  The external sources decompose a season differently from the in-house model, and reconciling that
  is what makes the five numbers averageable. Verified against CBS, the one source giving per-game
  points alongside the season total: it projects 17.0 games for every player it covers, backups
  included — so it never discounts for injury risk, but it does discount for *role*, through the
  per-game term instead (Jake Browning: 0.9 points per game across a full 17). The other three
  behave the same way. The in-house arm is therefore blended through `projected_points_full`, which
  reproduces that same split, rather than its availability-discounted `projected_points`; mixing
  the two would pull every percentile down hardest on exactly the players most likely to miss time.
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
  a durability signal.

  A points-per-game number alone throws away everything about *how* those points were earned,
  which is most of what makes next season predictable — volume is far more stable year over year
  than touchdown rate, so 17 PPG on 9 targets a game means something very different from 17 PPG on
  4 targets and triple the league's touchdown rate. The same recency weighting is therefore also
  applied to a component block: **volume** (targets/receptions/carries/attempts/yards/touchdowns
  per game, with touchdowns kept separate from yards so an unsustainable scoring rate stays its own
  visible signal), **role** (target share, air-yards share, WOPR, and snap share joined from
  `snap_counts` through the `ids` crosswalk at 99%+ coverage), and **efficiency** (passing/rushing/
  receiving EPA and CPOE, left NULL where a position doesn't do that thing rather than zero-filled,
  so "didn't do this" stays distinguishable from "did this badly"). Plus
  `weighted_td_regressed_ppg` — PPG recomputed with the player's own touchdowns swapped for the
  touchdowns their yardage would have produced at that season's league-average rate, per position
  but falling back to a pooled rate where a position-season is too thin to estimate from (RBs threw
  for 3 touchdowns on 9 yards league-wide in 2024, a rate of 0.33 touchdowns *per yard*). A
  feature-engineering building block, not a projection itself. Pure SQL over already-loaded
  tables — no fetch step, no network.
- `src/gold/league_settings.py` — builds `league_settings`: one row per league (Sleeper, ESPN)
  normalizing each platform's scoring rules (points per reception/yard/TD/turnover, etc.) and
  starting-roster construction (team count and QB/RB/WR/TE/FLEX/superflex/K/P/bench/IR slot counts)
  into a shared schema, including the ten punting categories the ESPN league scores and Sleeper
  has no concept of. Sleeper's settings arrive as flat columns; ESPN's arrive as a nested array
  of `{statId, points}` items and a slot-id-keyed lineup dict, both keyed by undocumented numeric
  IDs (mapped here using the cwendt94/espn-api project's reference tables, spot-checked against
  this league's actual raw settings). Exists so scoring-sensitive models (e.g. league-winning-RB
  thresholds, points-over-replacement) can run one formula per league and get a league-appropriate
  number back instead of a constant tuned to one scoring format. Pure SQL/Python over already-
  loaded tables — no fetch step, no network.
- `src/gold/inhouse_projections.py` — builds `inhouse_projections`: a home-grown PPG projection
  from a gradient-boosted model (scikit-learn's `HistGradientBoostingRegressor`), trained on a
  shift-based setup — `player_weighted_baselines` (its full volume/role/efficiency component block,
  not just `weighted_ppg_ppr`) plus prior-season `offensive_line_grades`/
  `skill_position_grades` as features, actual next-season PPG as the label — then converted to a
  season-total point projection. Also takes a role block as of the start of the season being
  projected — `target_is_starter` and `changed_team`, from that season's week 1 depth chart, which
  is published before a snap is played and so says nothing about the label. That is what lets the
  model tell an incumbent from a career backup with an identical per-game history, and it also
  repoints the OL/skill-corps grades at the team a player is *joining* rather than the one he left.

  Emits the season total twice, because the model and the sites decompose a season differently.
  `projected_points` (PPG x separately-modelled `expected_games`) is the honest expectation, and
  the number to rank a real roster on. `projected_points_full` is what `consensus.py` blends: it
  keeps the role discount the sites apply but drops the injury discount they don't, by measuring a
  player's expected games against a starter's at the same position and season. Season length is
  read from `schedules` rather than hardcoded to 17, since the warehouse still holds 16-game
  seasons.

  A floor and a ceiling come out alongside the expectation, from the same features and estimator
  refit under the pinball loss: `ppg_p10`/`ppg_p90`, the season totals `projected_points_floor`/
  `projected_points_ceiling`, and `upside` as the gap between ceiling and expectation. A conditional
  mean cannot tell a season-long RB2 from a backup one injury away from a starter's workload, which
  is exactly what "who might boom" is asking. Both are reported as measured: the ceiling is well
  calibrated out-of-sample (89.9% of actual veteran seasons land under `ppg_p90` against a 90%
  target, no crossed intervals) while the floor sits too high (15.4% under `ppg_p10` against 10%),
  because the scoring label's games floor excludes short seasons so nothing teaches the model how
  far down a bad one goes. `upside` earns its place modestly — holding the mean projection fixed
  within quintiles, the high-upside half beats its own projection by 3+ PPG 15.5% of the time
  against 11.9% (z=2.51, p=0.012, n=2416), positive in all five quintiles, dying by a 5-PPG
  threshold. It tilts the odds; it is not a boom detector.

  Both totals depend on `expected_games`, whose label is anchored on the **roster** rather
  than the box score: a player who was in the league all season and never took a snap has no
  `weekly_stats` row at all, so counting only those rows made him read as unobserved instead of as
  the zero he is (~90-110 players a season). Without those zeros the model can't answer below about
  4 games and career backups keep season totals they'll never earn. The scoring walk-forward can't
  see any of this — its games floor excludes exactly the affected players — so availability is
  scored on its own population.

  A second, much smaller model covers everyone the first structurally can't see: players with **no
  `player_weighted_baselines` row**, having never put together a season of 6+ games. Every feature
  above derives from that row, so without one there's nothing to predict from, and those players
  were absent from the table entirely (150 that ESPN projected and this warehouse didn't). Two
  groups land there and they're the same modelling problem — rookies with no NFL history at all,
  and fringe veterans who've played but never enough in one season to earn a baseline. It reads
  what a human reads for an unproven player: draft capital, the landing spot's line and skill-corps
  grades, whether he's already won a week 1 job, and an unweighted career rate that's NULL for a
  true rookie and so tells the model which of the two it's looking at. Its cohort — every such
  player reaching that season's week 1 depth chart — is a *closed* population, so its availability
  label is uncensored by construction. Draft capital comes from `rosters.draft_number` rather than
  `players.draft_pick`, which is the more natural home for it but lags by months and carried no
  2026 class at all during the 2026 preseason. Both arms write to the same table, so `role_games`
  normalises over one combined population and consumers don't need to know there are two models
  behind the column. Backtested the same walk-forward way, this arm is the one place in the
  warehouse that **beats** preseason ADP at ranking (Spearman 0.618 vs 0.494 pooled over
  2020-2025), which is less surprising than it sounds: ADP for unproven players is hype-driven,
  while draft slot and a won job are not. The only `src/gold` module that isn't pure SQL
  (Python/pandas/scikit-learn over already-loaded tables instead). Feeds into `consensus.py` as a
  fifth projection source.

  Also builds `inhouse_backtest`, the accept/reject instrument for any future change to the model:
  a **walk-forward** evaluation that predicts each labeled season from a model trained only on the
  seasons before it, one fold per season, scored per position (MAE, R2, Spearman) against three
  benchmarks — carrying last season's weighted rate forward unchanged, the preseason draft market's
  own ordering (`adp_consensus`, re-scored on the ADP-covered players so both sit on the same
  population), and for the availability half, `weighted_games_per_season`. ADP is the parity
  benchmark rather than a feature: `breakout_candidates.py`'s whole premise is that this model is
  ADP-blind, so a model that had seen ADP couldn't meaningfully disagree with it. Every fold's
  out-of-sample predictions are stored in `inhouse_projections` alongside the live season, so
  downstream models can be backtested too. Each run also prints out-of-sample permutation feature
  importance — which preseason signals actually move the prediction, not just which ones are in the
  feature list.
- `src/gold/points_over_replacement.py` — builds `points_over_replacement`: each skill-position
  player's season-total fantasy points, recomputed from nflverse weekly stats under each league's
  own `league_settings` scoring coefficients (not nflverse's canned PPR formula), minus that
  league-season-position's replacement level. Replacement level is a combined-flex-pool
  Value-Based-Drafting calculation: dedicated starters (`team_count x slots`) are filled first per
  position, then whatever's left over from RB/WR/TE is pooled, ranked by points, and FLEX slots are
  filled from that pool regardless of position — so a position that wins more flex spots in a given
  season automatically gets a deeper replacement level, with no hardcoded split. Pure SQL/Python
  over already-loaded tables — no fetch step, no network.
- `src/gold/punters.py` — builds `punter_seasons`, `punter_projections` and `punter_backtest`: the
  ESPN league starts a punter, and no external source in this warehouse prices the position, so
  this is the one model that stands entirely on its own. Punter-weeks are scored under
  `league_settings`' punting categories week by week (the gross-average bonus is awarded per game,
  so a season total can't produce it), with inside-the-10 punts derived from `pbp_punts` — verified
  against ESPN's own published actuals to within nine punts league-wide.

  The projection is a component model rather than a learner, because there isn't the data to
  support one: ~250 usable consecutive punter-season pairs, and per-punt rates that self-correlate
  between r=0.06 and r=0.21. Each component is an empirical-Bayes estimate shrunk toward the league
  rate by a constant fit on training seasons only, so a stat that doesn't persist collapses to the
  mean instead of inventing an edge. Two structural choices do the real work: **volume comes from
  the team, not the punter** (punt volume is a property of a bad offense — for punters who changed
  teams, their new team's prior punt rate predicts them better than their own history does), and
  **expected games comes from incumbency** (a punter who held the same job last season averages
  15.3 games and 148 points; everyone else, ~12 games and ~117). Walk-forward 2019-2025 it beats
  every naive baseline on MAE, RMSE and rank correlation — and still can't reliably pick the top
  five, because who keeps the job in November is the dominant term and nothing predicts it. Pure
  SQL/Python over already-loaded tables — no fetch step, no network.
- `src/gold/punt_environment.py` — builds `punt_environment`: one row per team-season describing
  the punting situation a team creates, on offense (how often it punts, from where, and what those
  punts are worth) and on defense (punts forced and punter points allowed — the matchup side).
  Exists because the intuition behind `punters.py`'s volume model is only half right. A punt struck
  from the opponent's side of the field is worth 3.73 league points; one from inside a team's own
  10 is worth 0.39 — less than the flat point a punt pays, because two-thirds get returned. So
  teams that punt most punt from deeper and lose ~11% of their value per punt, and volume still
  wins comfortably (5.60 punt points a game in the fewest-punting quintile against 8.72 in the
  most). The table is **diagnostic, not predictive**, and the docstring says why: average punt spot
  explains a team's points per punt at r=0.61 within a season but predicts next season's punter
  scoring at r=0.02, matchup-aware weekly rankings score no better out of sample than team punt
  rate alone, and weather doesn't order at all. Pure SQL/Python over already-loaded tables — no
  fetch step, no network.
- `src/gold/boom_bust.py` — builds `boom_bust`: classifies each skill-position player-season into
  an outcome bucket (League Winner/Delivered/Beat His Price/Met His Price/Fine/Busted/Got Injured/
  Never Had The Job/No Preseason ADP), measured on an **absolute** scale rather than a relative
  one. `finish_tier` buckets a positional finish into groups of `team_count` — the RB1/RB2/WR3
  language, derived rather than hardcoded to 12 — `expected_tier` applies the same width to the
  player's rank by preseason ADP, and `is_elite_finish` asks the uncensored question "was he a
  top-`team_count` asset". `tier_delta` and the continuous `percentile_delta` keep the relative
  "did he beat his price" read alongside, but no longer define the bucket.

  That split is a deliberate correction. Defining a boom as a +20-percentile-point *move* made it
  structurally unreachable from the top of the board — a first-round pick already sits near the
  99th percentile — so the table reported a 0% boom rate for rounds 1-4 and ~25% for round 15
  regardless of what those players did. On the absolute measure the rate falls 62.8% -> 6.4% from
  round 1 to round 15, which is the real shape. The old single "Got Injured" bucket is likewise
  split from "Never Had The Job", since conflating the fallen star with the career backup made
  injuries look like they climbed 18% -> 30% with ADP round when late picks are simply backups who
  were never going to play; splitting on whether a player started in the majority of the weeks he
  actually appeared separates them cleanly (mean ADP 147 vs 276). Pure SQL over already-loaded
  tables — no fetch step, no network.
- `src/gold/draft_value.py` — builds `draft_value`: what each player returned (or is projected to
  return) *over what he cost*, which is the question a draft actually turns on and which neither
  `points_over_replacement` nor `adp_consensus` answers alone. `expected_value` is an isotonic
  regression of realised value on `consensus_adp`, fit per league and position — isotonic because
  the one thing known a priori is the shape (later picks return less, monotonically) — and fit
  walk-forward, so no season is scored against a benchmark that had already seen it. Subtracting
  gives `surplus_value` (backward-looking, the study column) and `projected_surplus` (forward-
  looking, the draft board, from `inhouse_projections`).

  Value is `points_over_replacement` **floored at zero**, because nobody is forced to start a
  player worse than the waiver wire. That isn't cosmetic: on signed PoR the expected curve for a QB
  at pick 235 sits near -139, so eight quarterbacks projected *below* replacement ranked in the top
  25 of the 2026 board. They weren't beating their price, they were being less bad than a floor set
  by how deep their position's replacement level is. Sorting past seasons into deciles by the
  preseason call, the bottom decile realised -2.3 surplus and the top +16.6, with the share beating
  their price rising 36% -> 54%; ADP is ~orthogonal to surplus by construction, so that is signal
  genuinely additional to what the market prices. One measured caveat is recorded in the module:
  the drafted pool's mean value swings ~6 points a season for reasons no preseason curve can see
  and no training window removes, so surplus is a **within-season ranking** rather than a
  calibrated point total — hence `surplus_rank`/`projected_surplus_rank` and `surplus_centered`.
  `inhouse_projections`' full-PPR points are put on each league's scale by a through-origin
  per-position coefficient (R2 0.996-1.000), and the projected replacement level reuses
  `points_over_replacement`'s own `_replacement_levels` so the two definitions can't drift apart.
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
- `src/gold/draft_strategy.py` — builds `draft_strategy_results` and `draft_strategy_summary`: the
  one model here that prices a *plan* rather than a player. "Three running backs and two receivers in
  the first five rounds" is a claim about roster construction, not about any individual, and the only
  way to settle it is to run the draft — so this runs ~81,000 of them. Each is a snake draft off that
  season's real `adp_consensus` board, `team_count` teams over `skill starters + bench` rounds, with
  every team scored on the hindsight-best starting lineup it could field from the roster it finished
  with, in that league's own scoring. A drafted player who never played is carried as a genuine zero,
  on the same reasoning as `draft_value`: the expected return on a plan has to include its bust rate.

  A strategy constrains only the first five picks and is expressed as a **composition** — how many of
  each position, with ADP resolving the order, since nobody committed to "3 RB" passes the best
  receiver on the board to force a back. That keeps the space at 36 rather than 4^5, and ordering is
  then asked separately as its own `ordering` strategies (every permutation of 3RB+2WR and 2RB+3WR).
  `points_vs_field` is measured against the other teams in the *same* draft, so the do-nothing ADP
  control sits at exactly zero by construction — which is also the build's own correctness check.

  The measured answer is mostly a negative one, and deliberately reported that way. The scarcity
  premise behind the "full house" opening is real but shallow: an elite back outscores an elite
  receiver at the same positional finish, and that edge is gone by about RB15 in the 12-team half-PPR
  league and by RB5 in the 10-team full-PPR one. Three backs in five rounds therefore spends a
  premium pick in rounds 3-5, exactly where backs return least and hit least often, and it ranks
  mid-table in one league and near the bottom in the other. But **no position's slope clears
  significance** under either league's actual settings, tested season-clustered (the eleven
  per-season means, not the ~1,500 individual drafts, since one torn ACL moves every RB-heavy draft
  that year together). What the sweep can identify reliably is which openings *lose* — the all-in
  ones — plus one balanced opening, `2RB2WR1TE`, that clears |t| > 2 in both leagues independently.

  Two robustness dimensions are stored alongside rather than collapsed. `field_model = 'mixed'` gives
  every opponent its own seeded random opening instead of pure ADP: rankings are stable (Spearman
  0.77-0.96) but levels move, and disciplined best-available-by-ADP goes from a break-even control to
  the top of the board in the 12-team league — when everyone else reaches to fill a quota, the
  drafter without one collects what they leave. `variant = 'superflex'` converts a bench spot into a
  superflex slot (roster size unchanged) purely as a counterfactual, since **neither league is
  superflex** — verified against the platforms' raw settings, not inferred. There the quarterback
  slope becomes the largest and most significant effect anywhere in this warehouse, which is the
  answer to "should I take quarterbacks earlier": not in these leagues, and emphatically yes in one
  that adds the slot. That figure is an upper bound and the docstring says why — the ADP board being
  drafted from is still a 1QB board, so the focal team buys quarterbacks at prices real superflex
  drafts have already corrected. Pure SQL/Python over already-loaded tables — no fetch step, no
  network. Read alongside `notebooks/draft_strategy.ipynb`, which is where the findings are written up.
- `src/gold/player_archetypes.py` — builds `player_archetypes` and `archetype_outcomes`: sorts every
  drafted player-season into a career-stage archetype, then measures what each archetype is actually
  worth. The taxonomy crosses "has he ever finished elite" (`prior_elite_finishes`, counted strictly
  from earlier seasons so a player's own breakout can never classify him) against the age curve
  (27+ or year 7+), giving `Unproven Youth` / `Unproven Prime` / `Proven Prime` /
  `Unproven Veteran` / `Proven Veteran`.

  `archetype_outcomes` reports each cell's rate profile over `boom_bust`'s buckets — boomed,
  busted, got injured, returned on ADP — but the headline correction is that **those rates are
  mostly a restatement of ADP**: proven prime RBs finish elite 48.6% of the time against 8.1% for
  unproven youth, and they are drafted at pick 30 against pick 170. Scoring archetypes off them pays
  for information already in the price. The score is therefore built on `draft_value`'s
  `surplus_centered` — value minus what that *ADP slot* historically returned. After that
  adjustment only three cells clear |t| >= 2 on one league: `RB`/`Proven Prime` at **+17.8 points
  (t=2.63, positive in 6 of 8 seasons, and positive inside every ADP band it appears in)**, and
  `WR`/`Proven Veteran` (-6.3) and `WR`/`Unproven Veteran` (-3.9), i.e. aging receivers are
  systematically overpriced. Notably the "trusty veteran" penalty is a *receiver* effect —
  `RB`/`Proven Veteran` sits at +2.3, indistinguishable from zero.

  Everything else is noise, so `archetype_edge` is gated to 0.0 outside those cells rather than
  reported for all twenty: scored naively, `QB`/`Proven Prime` would carry +10.0 off 46 rows and
  then invert out of sample. A general "rank players by their archetype's historical surplus" score
  does not work at all — walk-forward over 2,667 player-seasons it lands at Spearman -0.031
  (p=0.11), and shrinkage, beat-price rate and a shrunk rate all do worse. Two caveats are recorded
  in the module: the gate applied walk-forward passes only the *negative* cells (which do hold up —
  marked-down players realise -1.83 surplus and finish elite 3.4% against +0.44 and 17.4% for
  ungated ones), and it never passes `RB`/`Proven Prime` in any historical fold, because that cell
  needs the full eight seasons to clear t=2. The RB prime edge is a strong full-sample finding that
  was not prospectively detectable at the sample sizes available.

## Environment

A Python 3.11 virtualenv lives at `.venv/` (gitignored):

```bash
source .venv/bin/activate
pip install -r requirements.txt
pip install -r requirements-notebooks.txt   # optional, only for notebooks/
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

## Testing

```bash
pytest
```

Run from the repo root, with `.venv` activated. Configuration is in `pytest.ini`; tests live in
`tests/`.

Most of this repo is warehouse-to-warehouse SQL, verified by the row counts each module prints
through `src/console.py`. Tests are for the modules whose logic is pure enough to check in
isolation — currently the team normalizer and the live-draft work built on top of it.

The suite never opens the warehouse. `tests/conftest.py` makes any attempt to open a DuckDB file
raise, for every test, without opting in. This is the same file-lock problem `src/query.py`
describes for notebooks: a connection held open during a test run makes a concurrent
`build_warehouse.sh` fail with `Could not set lock on file`, an error that names the warehouse and
never mentions tests. Fixtures are small hand-written frames instead, whose expected values can be
worked out by hand and do not shift under a rebuild.

Tests are written **before** the code they test — see `CLAUDE.md` for the rule and why.

## Notebooks (`notebooks/`)

Findings worth keeping, written as live queries so re-running updates them instead of leaving them
stale — see `notebooks/README.md`. Currently `punting.ipynb` (the position no external source
prices) and `draft_strategy.ipynb` (what roster construction is actually worth in each league).
`src/query.py` is the read-only accessor they use:

```python
from src.query import q, tables, columns, peek

q("SELECT * FROM punter_projections ORDER BY projected_points DESC LIMIT 10")
```

It opens a connection per call and closes it, on purpose. DuckDB takes a file lock on the
warehouse — one writer or many readers — and a long-lived notebook kernel holding one will make
`build_warehouse.sh` fail with `Could not set lock on file` with nothing pointing at the notebook
as the cause.

```bash
./scripts/run_notebooks.sh          # re-execute every notebook against the current warehouse
```
