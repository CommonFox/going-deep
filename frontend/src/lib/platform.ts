/** Display label for a `league_key` on the platform toggle (#180) — ESPN is an acronym, not a
 * word, so it's the one case that isn't just capitalizing the raw key. */
export function platformLabel(leagueKey: string): string {
  if (leagueKey === 'espn') return 'ESPN'
  return leagueKey.charAt(0).toUpperCase() + leagueKey.slice(1)
}
