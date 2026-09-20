/** `weekly_player_context` (src/gold/weekly_player_context.py), shaped into the four
 * `DetailSection`s #161 asks for — Projections, Matchup & environment, Role trend, Outcome rates —
 * for the `DetailPanel` on the Lineup page's starter and bench rows.
 *
 * Every field is built through one of the formatters below rather than inline, so the `== null`
 * check (never truthiness — a real `0` share or a `false` `is_starter` is data, not a miss) and the
 * unknown-tone convention only have to be right once. `row` itself is optional: a player with no
 * `weekly_player_context` row at all — not exported yet, or missing from a join upstream — renders
 * every field unknown rather than the panel erroring or going blank, exactly like a row with some
 * columns null.
 *
 * Field selection follows #161's own Solution section: source figures for Projections, the
 * game- and dvp-prefixed pair CONTEXT.md's **Defense vs. position** entry calls "figures" for
 * Matchup & environment, the CONTEXT.md **Role** entry's own metric list (snap/target/air-yards/
 * WOPR/carries share, each with a delta, plus depth rank and starter) for Role trend, and outcome
 * quantiles/rates for the last section.
 *
 * #162's close-call explanation layers on top rather than growing a second shape: `buildDetailSections`
 * takes an optional `opponent` row, and `toned()` re-tags the exact field set #162's own Solution
 * section named —
 * `game_implied_team_total`, `dvp_vs_league_avg_zscore`, every `role_` share/delta (not
 * `role_depth_rank_delta` — the ticket names the rank itself only) and `role_depth_rank` (inverted:
 * lower is better) — `tone: 'good'` for whichever side's raw value favors it, leaving the other
 * neutral rather than `'bad'`. `outcome_ceiling_rate`/`outcome_floor_rate` are deliberately left out
 * of that set: informational, never a verdict. `starterPairing`/`benchPairing` decide, from
 * `optimal_lineup`'s own `is_close_call`/`bench_player_id`, which two panels pair up and default
 * open together; a non-close-call row gets `opponentPlayerId: null`, so `buildDetailSections` falls
 * back to its #161 single-row shape untouched. */

import type { DetailField, DetailSection } from '../components/DetailPanel/DetailPanel'
import type { OptimalLineupRow, WeeklyPlayerContextRow } from './fixtures'

const UNKNOWN = '—'

function points(value: number | null | undefined, label: string): DetailField {
  return value == null ? { label, value: UNKNOWN, tone: 'unknown' } : { label, value: `${value.toFixed(2)} pts` }
}

function percent(value: number | null | undefined, label: string): DetailField {
  return value == null
    ? { label, value: UNKNOWN, tone: 'unknown' }
    : { label, value: `${Math.round(value * 100)}%` }
}

// A share expressed in percentage points, not percent-of-value — the input is already the
// difference of two shares, so "+3.2pp" reads correctly where "+3.2%" would misstate it as a
// relative change.
function deltaPercentagePoints(value: number | null | undefined, label: string): DetailField {
  if (value == null) return { label, value: UNKNOWN, tone: 'unknown' }
  const formatted = (value * 100).toFixed(1)
  return { label, value: value >= 0 ? `+${formatted}pp` : `${formatted}pp` }
}

function decimal(value: number | null | undefined, label: string): DetailField {
  return value == null ? { label, value: UNKNOWN, tone: 'unknown' } : { label, value: value.toFixed(2) }
}

function rank(value: number | null | undefined, label: string): DetailField {
  return value == null ? { label, value: UNKNOWN, tone: 'unknown' } : { label, value: `#${value}` }
}

function text(value: string | null | undefined, label: string): DetailField {
  return value == null ? { label, value: UNKNOWN, tone: 'unknown' } : { label, value }
}

function bool(value: boolean | null | undefined, label: string): DetailField {
  return value == null ? { label, value: UNKNOWN, tone: 'unknown' } : { label, value: value ? 'Yes' : 'No' }
}

// `game_environment.py`'s five fixed buckets for `gamescript_lean`. A value outside this set (there
// isn't one today, but the CASE that produces it lives in a different module) passes through as-is
// rather than disappearing, so a future bucket shows up as its own raw name instead of going blank.
const GAMESCRIPT_LABELS: Record<string, string> = {
  big_favorite: 'Big favorite',
  favorite: 'Favorite',
  pick_em: "Pick 'em",
  underdog: 'Underdog',
  big_underdog: 'Big underdog',
}

function gamescriptLean(value: string | null | undefined): DetailField {
  const label = 'Gamescript lean'
  if (value == null) return { label, value: UNKNOWN, tone: 'unknown' }
  return { label, value: GAMESCRIPT_LABELS[value] ?? value }
}

// FantasyPros contributes a positional rank, not a points total (weekly_player_context.py's own
// docstring), so it reads as "WR12" rather than a points figure the way Sleeper/ESPN do.
function fantasyProsRank(position: string | null | undefined, posRank: number | null | undefined): DetailField {
  const label = 'FantasyPros'
  if (posRank == null || position == null) return { label, value: UNKNOWN, tone: 'unknown' }
  return { label, value: `${position}${posRank}` }
}

// Whether a higher or a lower raw value is the one that favors a player — `role_depth_rank` is the
// one field in #162's list where rank 1 (lowest number) is best, inverted from every share/z-score
// around it.
type ToneDirection = 'higher' | 'lower'

function compareTone(
  value: number | null | undefined,
  opponentValue: number | null | undefined,
  direction: ToneDirection,
): DetailField['tone'] | undefined {
  if (value == null || opponentValue == null || value === opponentValue) return undefined
  const favors = direction === 'higher' ? value > opponentValue : value < opponentValue
  return favors ? 'good' : undefined
}

// Re-tags an already-formatted field against a counterpart's raw value, without disturbing the
// `'unknown'` tone a null value already earned — a close call doesn't make a missing figure less
// missing, and only the favored side ever moves off neutral (#162: never `'bad'`).
function toned(
  field: DetailField,
  value: number | null | undefined,
  opponentValue: number | null | undefined,
  direction: ToneDirection,
): DetailField {
  if (field.tone === 'unknown') return field
  const tone = compareTone(value, opponentValue, direction)
  return tone ? { ...field, tone } : field
}

export function buildDetailSections(
  row: WeeklyPlayerContextRow | undefined,
  opponent?: WeeklyPlayerContextRow,
): DetailSection[] {
  return [
    {
      title: 'Projections',
      fields: [
        points(row?.sleeper_points, 'Sleeper'),
        points(row?.espn_points, 'ESPN'),
        fantasyProsRank(row?.position, row?.fantasypros_pos_rank),
        points(row?.points_gap, 'Source gap'),
      ],
    },
    {
      title: 'Matchup & environment',
      fields: [
        text(row?.game_opponent, 'Opponent'),
        toned(
          points(row?.game_implied_team_total, 'Implied team total'),
          row?.game_implied_team_total,
          opponent?.game_implied_team_total,
          'higher',
        ),
        gamescriptLean(row?.game_gamescript_lean),
        toned(
          decimal(row?.dvp_vs_league_avg_zscore, 'Defense vs. position (z-score)'),
          row?.dvp_vs_league_avg_zscore,
          opponent?.dvp_vs_league_avg_zscore,
          'higher',
        ),
        rank(row?.dvp_rank, 'Defense rank'),
      ],
    },
    {
      title: 'Role trend',
      fields: [
        toned(percent(row?.role_snap_share, 'Snap share'), row?.role_snap_share, opponent?.role_snap_share, 'higher'),
        toned(
          deltaPercentagePoints(row?.role_snap_share_delta, 'Snap share Δ'),
          row?.role_snap_share_delta,
          opponent?.role_snap_share_delta,
          'higher',
        ),
        toned(
          percent(row?.role_target_share, 'Target share'),
          row?.role_target_share,
          opponent?.role_target_share,
          'higher',
        ),
        toned(
          deltaPercentagePoints(row?.role_target_share_delta, 'Target share Δ'),
          row?.role_target_share_delta,
          opponent?.role_target_share_delta,
          'higher',
        ),
        toned(
          percent(row?.role_air_yards_share, 'Air yards share'),
          row?.role_air_yards_share,
          opponent?.role_air_yards_share,
          'higher',
        ),
        toned(
          deltaPercentagePoints(row?.role_air_yards_share_delta, 'Air yards share Δ'),
          row?.role_air_yards_share_delta,
          opponent?.role_air_yards_share_delta,
          'higher',
        ),
        toned(decimal(row?.role_wopr, 'WOPR'), row?.role_wopr, opponent?.role_wopr, 'higher'),
        toned(
          decimal(row?.role_wopr_delta, 'WOPR Δ'),
          row?.role_wopr_delta,
          opponent?.role_wopr_delta,
          'higher',
        ),
        toned(
          percent(row?.role_carries_share, 'Carries share'),
          row?.role_carries_share,
          opponent?.role_carries_share,
          'higher',
        ),
        toned(
          deltaPercentagePoints(row?.role_carries_share_delta, 'Carries share Δ'),
          row?.role_carries_share_delta,
          opponent?.role_carries_share_delta,
          'higher',
        ),
        toned(
          rank(row?.role_depth_rank, 'Depth chart rank'),
          row?.role_depth_rank,
          opponent?.role_depth_rank,
          'lower',
        ),
        bool(row?.role_is_starter, 'Starter'),
      ],
    },
    {
      title: 'Outcome rates',
      fields: [
        points(row?.outcome_median, 'Median'),
        points(row?.outcome_floor, 'Floor'),
        points(row?.outcome_ceiling, 'Ceiling'),
        percent(row?.outcome_ceiling_rate, 'Ceiling rate'),
        percent(row?.outcome_floor_rate, 'Floor rate'),
      ],
    },
  ]
}

export interface ClosePairing {
  defaultOpen: boolean
  opponentPlayerId: string | null
}

/** A starting slot's row: whether its panel should default open and, if so, which player's
 * `weekly_player_context` row `buildDetailSections` should compare it against. Only a close call
 * pairs two panels — every other row opens exactly as #161 left it. */
export function starterPairing(row: OptimalLineupRow): ClosePairing {
  if (!row.is_close_call) return { defaultOpen: false, opponentPlayerId: null }
  return { defaultOpen: true, opponentPlayerId: row.bench_player_id }
}

/** The bench-side mirror of `starterPairing`: a bench player's panel opens and tones against
 * whichever close-call starter named them as its `bench_player_id`, if any starter did. One bench
 * player can be the flagged alternative for more than one slot at once (seen live: a single tight
 * end sitting behind both the FLEX and TE close calls in the same lineup) — `DetailField` only
 * carries one tone per field, so this pairs with the first such starter in `starters`' own order
 * rather than inventing a way to blend multiple comparisons into one panel. */
export function benchPairing(benchPlayerId: string, starters: OptimalLineupRow[]): ClosePairing {
  const starter = starters.find((s) => s.is_close_call && s.bench_player_id === benchPlayerId)
  return starter ? { defaultOpen: true, opponentPlayerId: starter.player_id } : { defaultOpen: false, opponentPlayerId: null }
}
