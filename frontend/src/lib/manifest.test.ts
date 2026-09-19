/** Ported from `tests/test_web_warehouse_status.py` when `src/web/` was retired (#130) — same
 * three cases, same boundary, now against `stalenessWarning` instead of Python's
 * `staleness_warning`. Keeps the rule's test coverage from disappearing along with the Streamlit
 * app it used to live next to. */

import { describe, expect, it } from 'vitest'
import { stalenessWarning } from './manifest'

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
