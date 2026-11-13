import type { Performance } from '@/types/overview-api';
// Timestamp sort keeps sub-millisecond precision and deterministic BIGINT tie-breaking.
const instantKey = (s: string) =>
  s.slice(0, 19) + (s.split('.')[1]?.slice(0, -1) ?? '').padEnd(9, '0');
export function performanceSeries(items: Performance[]) {
  const chronological = [...items].sort(
    (a, b) =>
      instantKey(a.periodEndAt).localeCompare(instantKey(b.periodEndAt)) ||
      (BigInt(a.id) < BigInt(b.id) ? -1 : 1),
  );
  let segment = -1;
  let previous: Performance | undefined;
  const keys: string[] = [];
  const points = chronological.map((row) => {
    const drawable = row.performanceStatus !== 'PARTIAL' && row.performanceIndex !== null;
    const continuous =
      drawable &&
      previous &&
      previous.performanceStatus !== 'PARTIAL' &&
      previous.performanceIndex !== null &&
      row.performanceStatus !== 'BASELINE' &&
      row.periodStartAt !== null &&
      instantKey(row.periodStartAt) === instantKey(previous.periodEndAt);
    if (drawable && !continuous) {
      segment++;
      keys.push('s' + segment);
    }
    previous = row;
    // Only chart coordinates are approximate. Labels/table/tooltips use the original decimal string.
    return { row, ...(drawable ? { ['s' + segment]: Number(row.performanceIndex) } : {}) };
  });
  return { points, keys };
}
