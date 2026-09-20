/** `optimal_lineup`/`optimal_lineup_bench` column definitions, shared between the real `/lineup`
 * route (#128) and the kitchen sink's gallery so the two can't silently drift apart on the next
 * shape change — kept in one place rather than each defining its own copy of the same columns. */

import type { Column } from '../components/DataTable/DataTable'
import type { OptimalLineupRow, WeeklyPlayerContextRow } from './fixtures'
import { weeklyRankTier } from './playerDetail'

export interface BenchRow {
  player_id: string | null
  player_name: string
  position: string
  projected_points: number | null
}

// #163: the row shape `Lineup.tsx` actually hands the table — the exported `optimal_lineup`/
// `optimal_lineup_bench` row plus whichever `weekly_player_context` row matches that player, if
// any, so the rank/tier column below can read it without a second lookup of its own. `context` is
// optional rather than required so a caller with no context loaded (the kitchen sink's static
// fixtures) still type-checks, rendering the same unknown dash a missing row gets anywhere else.
export type StarterDisplayRow = OptimalLineupRow & { context?: WeeklyPlayerContextRow }
export type BenchDisplayRow = BenchRow & { context?: WeeklyPlayerContextRow }

export const starterColumns: Column<StarterDisplayRow>[] = [
  { key: 'slot', header: 'Slot', accessor: (r) => r.slot },
  {
    key: 'player',
    header: 'Player',
    render: (r) =>
      r.player_id ? r.player_name : <span className="unknown">not enough eligible players</span>,
  },
  {
    key: 'projected',
    header: 'Projected',
    render: (r) => (r.projected_points != null ? `${r.projected_points.toFixed(2)} pts` : '—'),
  },
  { key: 'rankTier', header: 'Rank / tier', render: (r) => weeklyRankTier(r.context) },
  {
    key: 'closeCall',
    header: 'Close call',
    // `Math.abs` rather than the signed difference: the starter and bench alternative can land
    // either side of each other within the close-call margin, and a bare "-" prefix (Streamlit's
    // own literal string) double-negates when the bench player is the one narrowly ahead.
    render: (r) =>
      r.is_close_call
        ? `vs. ${r.bench_player_name} (${(r.bench_projected_points ?? 0).toFixed(2)} pts, +${Math.abs(
            (r.projected_points ?? 0) - (r.bench_projected_points ?? 0),
          ).toFixed(2)})`
        : '—',
  },
]

export const benchColumns: Column<BenchDisplayRow>[] = [
  { key: 'player', header: 'Player', accessor: (r) => r.player_name },
  { key: 'position', header: 'Pos', accessor: (r) => r.position },
  {
    key: 'projected',
    header: 'Projected points',
    render: (r) => (r.projected_points != null ? r.projected_points.toFixed(2) : '—'),
  },
  { key: 'rankTier', header: 'Rank / tier', render: (r) => weeklyRankTier(r.context) },
]
