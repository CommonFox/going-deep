/** Ported from `tests/test_web_warehouse_status.py` when `src/web/` was retired (#130) — same
 * three cases, same boundary, now against `stalenessWarning` instead of Python's
 * `staleness_warning`. Keeps the rule's test coverage from disappearing along with the Streamlit
 * app it used to live next to. */

import { describe, expect, it } from 'vitest'
import { distinctLeagueKeys, stalenessWarning } from './manifest'
import type { Manifest } from './manifest'

const NOW = new Date('2026-09-13T12:00:00Z')
const MAX_AGE_MS = 24 * 60 * 60 * 1000

describe('stalenessWarning', () => {
  it('gives no warning for an export built today', () => {
    const builtAt = new Date(NOW.getTime() - 3 * 60 * 60 * 1000)
    expect(stalenessWarning(builtAt, NOW)).toBeNull()
  })

  it('warns and reports when a stale export was built', () => {
    const builtAt = new Date(NOW.getTime() - 6 * 24 * 60 * 60 * 1000)
    const message = stalenessWarning(builtAt, NOW)
    expect(message).not.toBeNull()
    expect(message).toContain(builtAt.toISOString().slice(0, 16).replace('T', ' '))
  })

  it('treats the configured window as still fresh at its exact edge, stale a minute past it', () => {
    expect(stalenessWarning(new Date(NOW.getTime() - MAX_AGE_MS), NOW)).toBeNull()
    expect(stalenessWarning(new Date(NOW.getTime() - MAX_AGE_MS - 60_000), NOW)).not.toBeNull()
  })
})

describe('distinctLeagueKeys', () => {
  it('dedupes league_key across every table/season/week combination', () => {
    const manifest: Manifest = {
      built_at: NOW.toISOString(),
      schema_version: 1,
      available: [
        { table: 'optimal_lineup', league_key: 'sleeper', season: 2026, week: 2 },
        { table: 'optimal_lineup', league_key: 'sleeper', season: 2026, week: 3 },
        { table: 'waiver_rankings', league_key: 'sleeper', season: 2026, week: 2 },
        { table: 'optimal_lineup', league_key: 'espn', season: 2026, week: 2 },
      ],
    }
    expect(distinctLeagueKeys(manifest)).toEqual(['espn', 'sleeper'])
  })

  it('sorts alphabetically regardless of the order entries appear in', () => {
    const manifest: Manifest = {
      built_at: NOW.toISOString(),
      schema_version: 1,
      available: [
        { table: 'optimal_lineup', league_key: 'sleeper', season: 2026, week: 2 },
        { table: 'optimal_lineup', league_key: 'espn', season: 2026, week: 2 },
      ],
    }
    expect(distinctLeagueKeys(manifest)).toEqual(['espn', 'sleeper'])
  })

  it('returns an empty list when the manifest has no available entries', () => {
    const manifest: Manifest = { built_at: NOW.toISOString(), schema_version: 1, available: [] }
    expect(distinctLeagueKeys(manifest)).toEqual([])
  })
})
