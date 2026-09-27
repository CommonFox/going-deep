import { describe, expect, it } from 'vitest'
import { platformLabel } from './platform'

describe('platformLabel', () => {
  it('renders espn as the ESPN acronym', () => {
    expect(platformLabel('espn')).toBe('ESPN')
  })

  it('capitalizes sleeper', () => {
    expect(platformLabel('sleeper')).toBe('Sleeper')
  })

  it('falls back to capitalizing an unrecognized league_key rather than erroring', () => {
    expect(platformLabel('yahoo')).toBe('Yahoo')
  })
})
