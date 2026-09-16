import { useEffect, useState } from 'react'

// THROWAWAY — spike for going-deep issue #125. Proves the static-export architecture end to end:
// fetch a manifest (no-store, so it can never serve a stale cached copy), check its build
// timestamp against the same 1-day rule src/web/warehouse_status.py already uses, then render one
// real exported table. Not the app shell (#127 owns that) — just evidence the plan works.

const MAX_WAREHOUSE_AGE_MS = 24 * 60 * 60 * 1000

interface Manifest {
  built_at: string
  schema_version: number
  available: { table: string; league_key?: string; season: number; week?: number }[]
}

interface WaiverRow {
  league_key: string
  season: number
  week: number
  // Most rows in the free-agent tail have no resolved id (see docs/export-format-spike.md) — 87%
  // in the larger league's week-2 export — so nothing here can key on player_id alone.
  player_id: string | null
  player_name: string
  position: string
  availability: string
  weekly_points: number | null
  weekly_points_source: string | null
  ros_points: number | null
}

function stalenessWarning(builtAt: Date, now: Date): string | null {
  const ageMs = now.getTime() - builtAt.getTime()
  if (ageMs <= MAX_WAREHOUSE_AGE_MS) return null
  const hours = Math.round(ageMs / (60 * 60 * 1000))
  return `Export built ${builtAt.toLocaleString()}, ${hours}h ago — older than the 24h this app expects.`
}

function App() {
  const [manifest, setManifest] = useState<Manifest | null>(null)
  const [rows, setRows] = useState<WaiverRow[] | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    // no-store: the freshness contract depends on this fetch never being answered from cache.
    fetch('/data/manifest.json', { cache: 'no-store' })
      .then((r) => r.json())
      .then(setManifest)
      .catch((e) => setError(String(e)))

    fetch('/data/waiver_rankings/sleeper/2026-2.json')
      .then((r) => r.json())
      .then(setRows)
      .catch((e) => setError(String(e)))
  }, [])

  if (error) return <p style={{ color: 'red' }}>{error}</p>
  if (!manifest || !rows) return <p>Loading…</p>

  const warning = stalenessWarning(new Date(manifest.built_at), new Date())

  return (
    <div style={{ fontFamily: 'sans-serif', padding: '1rem', maxWidth: 720, margin: '0 auto' }}>
      <h1>Waiver Board (spike)</h1>
      <p>
        Export built {new Date(manifest.built_at).toLocaleString()} · schema v{manifest.schema_version} ·{' '}
        {rows.length} rows loaded from a real per-(league, season, week) export
      </p>
      {warning && (
        <p style={{ background: '#fee', border: '1px solid #c00', padding: '0.5rem' }}>{warning}</p>
      )}
      <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.9rem' }}>
        <thead>
          <tr>
            <th style={{ textAlign: 'left' }}>Player</th>
            <th style={{ textAlign: 'left' }}>Pos</th>
            <th style={{ textAlign: 'left' }}>Availability</th>
            <th style={{ textAlign: 'right' }}>Week pts</th>
            <th style={{ textAlign: 'right' }}>ROS pts</th>
          </tr>
        </thead>
        <tbody>
          {rows
            .slice()
            .sort((a, b) => (b.ros_points ?? 0) - (a.ros_points ?? 0))
            .slice(0, 25)
            .map((row, i) => (
              <tr key={row.player_id ?? `${row.player_name}-${row.position}-${i}`} style={{ borderTop: '1px solid #ddd' }}>
                <td>{row.player_name}</td>
                <td>{row.position}</td>
                <td>{row.availability}</td>
                <td style={{ textAlign: 'right' }}>{row.weekly_points?.toFixed(1) ?? '—'}</td>
                <td style={{ textAlign: 'right' }}>{row.ros_points?.toFixed(1) ?? '—'}</td>
              </tr>
            ))}
        </tbody>
      </table>
      <p style={{ color: '#666' }}>Top 25 of {rows.length} by ROS points, from the real Sleeper league, week 2.</p>
    </div>
  )
}

export default App
