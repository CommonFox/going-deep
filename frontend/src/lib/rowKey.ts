/** #125's spike found 87% of rows in the deep waiver free-agent tail have `player_id === null` —
 * a stable list key can't rely on it alone. Falls back to name+position+index, the spike's own
 * workaround, decided for real here instead of re-discovered in #128. */
export function getRowKey(
  id: string | null | undefined,
  name: string | null | undefined,
  position: string | null | undefined,
  index: number,
): string {
  if (id) return id;
  return `${name ?? 'unknown'}-${position ?? 'unknown'}-${index}`;
}
