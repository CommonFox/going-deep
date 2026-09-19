/** `waiver_rankings` column definitions, shared between the real `/waiver` route (#129) and the
 * kitchen sink's gallery — same "one place" reasoning `lineupColumns.tsx` documents for #128, so
 * the two can't silently drift apart on the next shape change.
 *
 * Ported from `src/web/pages/waiver_board.py`'s `_points_label` and inline replacement-level
 * caption: a null weekly/ROS number reads as "No projection", an ESPN-sourced weekly number is
 * tagged (ROS is never ESPN-sourced, so it's never tagged), and replacement_level_points is
 * checked for null on its own — not tied to player_id — exactly as the Streamlit page does. */

import type { Column } from '../components/DataTable/DataTable'
import type { WaiverRankingRow } from './fixtures'
import styles from './waiverColumns.module.css'

function pointsLabel(points: number | null, source?: string | null) {
  if (points == null) return <span className="unknown">No projection</span>
  const tag = source === 'espn' ? ' (ESPN proj)' : ''
  return `${points.toFixed(2)} pts${tag}`
}

export const waiverColumns: Column<WaiverRankingRow>[] = [
  {
    key: 'player',
    header: 'Player',
    render: (r) => (
      <>
        {r.player_id ? r.player_name : <span className="unknown">{r.player_name}</span>}
        {r.availability === 'on_waivers' && <span className={styles.badge}>on waivers</span>}
      </>
    ),
  },
  { key: 'position', header: 'Pos', accessor: (r) => r.position },
  {
    key: 'weekly',
    header: 'This week',
    sortable: true,
    accessor: (r) => r.weekly_points,
    render: (r) => pointsLabel(r.weekly_points, r.weekly_points_source),
  },
  {
    key: 'ros',
    header: 'Rest of season',
    sortable: true,
    accessor: (r) => r.ros_points,
    render: (r) => pointsLabel(r.ros_points),
  },
  {
    key: 'replacement',
    header: 'Replacement level',
    render: (r) =>
      r.replacement_level_points == null ? (
        <span className="unknown">Player not yet identified — no replacement-level context</span>
      ) : (
        `${r.position}, top ${r.starters_at_position} starters: ` +
        `${r.replacement_level_points.toFixed(2)} pts`
      ),
  },
]
