/** `optimal_lineup`/`optimal_lineup_bench` column definitions, shared between the real `/lineup`
 * route (#128) and the kitchen sink's gallery so the two can't silently drift apart on the next
 * shape change — kept in one place rather than each defining its own copy of the same columns. */

import type { Column } from '../components/DataTable/DataTable'
import type { OptimalLineupRow } from './fixtures'

export interface BenchRow {
  player_id: string | null
  player_name: string
  position: string
  projected_points: number | null
}

export const starterColumns: Column<OptimalLineupRow>[] = [
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

export const benchColumns: Column<BenchRow>[] = [
  { key: 'player', header: 'Player', accessor: (r) => r.player_name },
  { key: 'position', header: 'Pos', accessor: (r) => r.position },
  {
    key: 'projected',
    header: 'Projected points',
    render: (r) => (r.projected_points != null ? r.projected_points.toFixed(2) : '—'),
  },
]
