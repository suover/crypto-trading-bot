/** Formatting only, using decimal digits/BigInt; no floating-point money arithmetic. */
export function decimalText(value: string | null): string {
  if (value === null) return '미기록';
  const negative = value.startsWith('-'),
    [whole, fraction = ''] = value.replace(/^-/, '').split('.');
  const tail = fraction.replace(/0+$/, '');
  const sign = negative && /[1-9]/.test(whole + fraction) ? '-' : '';
  return sign + BigInt(whole).toLocaleString('ko-KR') + (tail ? '.' + tail : '');
}
export const moneyText = (v: string | null) => (v === null ? '미기록' : decimalText(v) + '원');
export const percentText = (v: string | null) => (v === null ? '미기록' : decimalText(v) + '%');
export function ratioText(v: string | null): string {
  if (v === null) return '미기록';
  const [whole, fraction = ''] = v.split('.');
  const digits = fraction.padEnd(2, '0');
  return percentText(
    (whole + digits.slice(0, 2)).replace(/^0+(?=\d)/, '') +
      (digits.length > 2 ? '.' + digits.slice(2) : ''),
  );
}
export const valueTone = (v: string | null) =>
  v === null || !/[1-9]/.test(v) ? '' : v.startsWith('-') ? 'negative' : 'positive';
// datetime-local input is explicitly KST, regardless of the browser's timezone.
export function kstInstant(v: string): string {
  if (!v) return '';
  if (!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/.test(v)) throw new Error('Invalid date');
  const utc = new Date(v + ':00+09:00');
  if (
    !Number.isFinite(utc.getTime()) ||
    new Date(utc.getTime() + 9 * 3600000).toISOString().slice(0, 16) !== v
  )
    throw new Error('Invalid date');
  return utc.toISOString();
}
