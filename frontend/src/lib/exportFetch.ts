/** One fetch shape for every file under `data/export/` (see src/export/build.py) — `cache:
 * 'no-store'` so a CDN's cached copy never answers in place of the real file, matching the
 * manifest's own freshness contract (docs/export-format-spike.md, #125) applied to every export
 * file, not just manifest.json.
 *
 * The URL differs by build (#130). `npm run dev`'s Vite plugin (vite.config.ts) serves
 * `data/export/` straight off disk at the file's own path, so a dev build fetches it directly. A
 * production build has no local warehouse to read — `api/data.ts` proxies to the private Vercel
 * Blob store instead, which is why only that build prefixes the request through it. */

export async function fetchExportFile<T>(path: string): Promise<T> {
  const url = import.meta.env.PROD ? `/api/data?pathname=${encodeURIComponent(path)}` : `/${path}`
  const response = await fetch(url, { cache: 'no-store' })
  if (!response.ok) {
    throw new Error(`fetch failed for ${path}: ${response.status}`)
  }
  return response.json()
}

/** The `<table>/<league_key>/<season>-<week>.json` key scheme settled in #125 §2, for the
 * week-scoped tables (`optimal_lineup`, `optimal_lineup_bench`, `waiver_rankings`). Takes the key
 * as one `{league_key, season, week}` triple — the shape `AvailableEntry`/`CurrentWeekEntry`
 * already carry — rather than three positional args a caller could transpose. */
export function tableFilePath(
  table: string,
  key: { league_key: string; season: number; week: number },
): string {
  return `${table}/${key.league_key}/${key.season}-${key.week}.json`
}
