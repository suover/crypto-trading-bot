type NumericDisplayValue = string | number | null | undefined;

/** Display/chart conversion only; never used to size an order or persist accounting. */
export function toNumber(value: NumericDisplayValue): number | null {
  if (value === null || value === undefined || (typeof value === 'string' && value.trim() === ''))
    return null;
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
}

export function krw(value: NumericDisplayValue, signed = false): string {
  const number = toNumber(value);
  if (number === null) return '—';
  return new Intl.NumberFormat('ko-KR', {
    style: 'currency',
    currency: 'KRW',
    maximumFractionDigits: 0,
    signDisplay: signed ? 'exceptZero' : 'auto',
  }).format(number);
}

/** value is in percentage points, not a 0..1 ratio. */
export function percent(value: NumericDisplayValue, signed = false): string {
  const number = toNumber(value);
  if (number === null) return '—';
  return `${new Intl.NumberFormat('ko-KR', {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
    signDisplay: signed ? 'exceptZero' : 'auto',
  }).format(number)}%`;
}

/** value is a 0..1 ratio (confidence, trade ratio or weight). */
export function ratio(value: NumericDisplayValue): string {
  const number = toNumber(value);
  return number === null ? '—' : percent(number * 100);
}

export function quantity(value: NumericDisplayValue): string {
  const number = toNumber(value);
  return number === null
    ? '—'
    : new Intl.NumberFormat('ko-KR', {
        maximumFractionDigits: 8,
      }).format(number);
}

function dateParts(value: string | null | undefined) {
  if (!value || !Number.isFinite(Date.parse(value))) return null;
  const parts = new Intl.DateTimeFormat('en-CA', {
    timeZone: 'Asia/Seoul',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hourCycle: 'h23',
  }).formatToParts(new Date(value));
  return Object.fromEntries(parts.map(({ type, value: part }) => [type, part]));
}

export function dateTime(value: string | null | undefined): string {
  const parts = dateParts(value);
  return parts ? `${parts.year}.${parts.month}.${parts.day} ${parts.hour}:${parts.minute}` : '—';
}

export function dateOnly(value: string | null | undefined): string {
  const parts = dateParts(value);
  return parts ? `${parts.year}.${parts.month}.${parts.day}` : '—';
}

export function shortTime(value: string | null | undefined): string {
  const parts = dateParts(value);
  return parts ? `${parts.hour}:${parts.minute}` : '—';
}
