import { describe, expect, it } from 'vitest';
import { dateOnly, dateTime, krw, percent, quantity, ratio, shortTime, toNumber } from './format';

describe('display-only formatters', () => {
  it('distinguishes missing values from measured zero', () => {
    for (const value of [null, undefined, '', ' ', 'NaN', 'Infinity', Number.NaN]) {
      expect(krw(value)).toBe('—');
      expect(toNumber(value)).toBeNull();
    }
    expect(krw('0')).toBe('₩0');
    expect(krw('1250000')).toBe('₩1,250,000');
    expect(krw('1000', true)).toBe('+₩1,000');
    expect(quantity('0.00001234')).toBe('0.00001234');
    expect(quantity(null)).toBe('—');
  });

  it('keeps percentage points separate from 0..1 ratios', () => {
    expect(percent(8.05, true)).toBe('+8.05%');
    expect(percent(-0.08, true)).toBe('-0.08%');
    expect(ratio(0.73)).toBe('73.00%');
    expect(ratio(0)).toBe('0.00%');
    expect(ratio(null)).toBe('—');
  });

  it('always formats UTC timestamps in Asia/Seoul, including the date rollover', () => {
    expect(dateTime('2026-11-06T21:00:00Z')).toBe('2026.11.07 06:00');
    expect(dateOnly('2026-11-06T21:00:00Z')).toBe('2026.11.07');
    expect(shortTime('2026-11-06T15:00:00Z')).toBe('00:00');
    expect(dateTime('invalid')).toBe('—');
    expect(dateOnly(null)).toBe('—');
  });
});
