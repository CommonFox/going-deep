/** One fetch shape for every file under `data/export/` (see src/export/build.py) — `cache:
 * 'no-store'` so a CDN's cached copy never answers in place of the real file, matching the
 * manifest's own freshness contract (docs/export-format-spike.md, #125) applied to every export
 * file, not just manifest.json. */

export async function fetchExportFile<T>(path: string): Promise<T> {
  const response = await fetch(`/${path}`, { cache: 'no-store' })
  if (!response.ok) {
    throw new Error(`fetch failed for /${path}: ${response.status}`)
  }
  return response.json()
}

/** The `<table>/<league_key>/<season>-<week>.json` key scheme settled in #125 §2, for the
 * week-scoped tables (`optimal_lineup`, `optimal_lineup_bench`, `waiver_rankings`). */
export function tableFilePath(table: string, leagueKey: string, season: number, week: number): string {
  return `${table}/${leagueKey}/${season}-${week}.json`
}
