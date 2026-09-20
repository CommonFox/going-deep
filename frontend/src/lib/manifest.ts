/** The export manifest contract settled in docs/export-format-spike.md (#125) and built by
 * src/export/manifest.py (#126): a `built_at` timestamp fetched with `cache: 'no-store'`, a
 * `schema_version` to refuse a mismatch, and `available` so the app never has to probe with a
 * failed fetch to know which (table, league, season, week) combinations exist. */

import { fetchExportFile } from './exportFetch'

export interface AvailableEntry {
  table: string;
  league_key: string;
  season: number;
  week: number;
}

export interface Manifest {
  built_at: string;
  schema_version: number;
  available: AvailableEntry[];
}

const MAX_AGE_MS = 24 * 60 * 60 * 1000;

/** None if the export is fresh enough, else a message naming when it was built — ported from
 * src/web/warehouse_status.py's `staleness_warning`, same 24h threshold and reasoning (waiver
 * claims and lineup calls both turn on injury/role news that can land at any hour). Pure, so the
 * rule is checkable without a clock. */
export function stalenessWarning(
  builtAt: Date,
  now: Date,
  maxAgeMs: number = MAX_AGE_MS,
): string | null {
  const ageMs = now.getTime() - builtAt.getTime();
  if (ageMs <= maxAgeMs) return null;

  const ageHours = Math.round(ageMs / 3_600_000);
  const maxAgeHours = Math.round(maxAgeMs / 3_600_000);
  const builtAtLabel = builtAt.toISOString().slice(0, 16).replace('T', ' ');
  return (
    `Data last exported ${builtAtLabel}, ${ageHours}h ago — older than the ${maxAgeHours}h this ` +
    'app expects. Numbers may be out of date until the warehouse is rebuilt.'
  );
}

export async function fetchManifest(): Promise<Manifest> {
  return fetchExportFile<Manifest>('manifest.json');
}

/** Whether `table`'s file for `key` is in the manifest's `available` list — the check a page makes
 * before fetching, so it never has to probe with a fetch that 404s (see this module's own
 * docstring). One place for the four-field match, rather than each caller re-writing it per table
 * it checks. */
export function isAvailable(
  manifest: Manifest,
  table: string,
  key: { league_key: string; season: number; week: number },
): boolean {
  return manifest.available.some(
    (entry) =>
      entry.table === table &&
      entry.league_key === key.league_key &&
      entry.season === key.season &&
      entry.week === key.week,
  );
}
