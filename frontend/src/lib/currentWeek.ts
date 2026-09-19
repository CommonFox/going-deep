/** `current_week.json`, written by `src/export/current_week.py` (#128): the earliest week whose
 * games aren't finished yet, precomputed per (league_key, season) at export time since
 * `schedules` — what the Streamlit lineup optimizer reads live to work this out — isn't part of
 * the static export. The lineup page reads this instead of trying to derive "today" itself. */

import { fetchExportFile } from './exportFetch'

export interface CurrentWeekEntry {
  league_key: string
  season: number
  week: number
}

export async function fetchCurrentWeek(): Promise<CurrentWeekEntry[]> {
  return fetchExportFile<CurrentWeekEntry[]>('current_week.json')
}
