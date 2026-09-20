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
 * quantiles/rates for the last section. `dvp_vs_league_avg_zscore`, `role_depth_rank` and every
 * `role_` share/delta field are the exact set #162 will later tag with `tone: 'good'` for a close
 * call's two panels — named here so that ticket has something to find. */

import type { DetailField, DetailSection } from '../components/DetailPanel/DetailPanel'
import type { WeeklyPlayerContextRow } from './fixtures'

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

export function buildDetailSections(row: WeeklyPlayerContextRow | undefined): DetailSection[] {
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
        points(row?.game_implied_team_total, 'Implied team total'),
        gamescriptLean(row?.game_gamescript_lean),
        decimal(row?.dvp_vs_league_avg_zscore, 'Defense vs. position (z-score)'),
        rank(row?.dvp_rank, 'Defense rank'),
      ],
    },
    {
      title: 'Role trend',
      fields: [
        percent(row?.role_snap_share, 'Snap share'),
        deltaPercentagePoints(row?.role_snap_share_delta, 'Snap share Δ'),
        percent(row?.role_target_share, 'Target share'),
        deltaPercentagePoints(row?.role_target_share_delta, 'Target share Δ'),
        percent(row?.role_air_yards_share, 'Air yards share'),
        deltaPercentagePoints(row?.role_air_yards_share_delta, 'Air yards share Δ'),
        decimal(row?.role_wopr, 'WOPR'),
        decimal(row?.role_wopr_delta, 'WOPR Δ'),
        percent(row?.role_carries_share, 'Carries share'),
        deltaPercentagePoints(row?.role_carries_share_delta, 'Carries share Δ'),
        rank(row?.role_depth_rank, 'Depth chart rank'),
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
