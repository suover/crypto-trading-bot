import { describe, expect, it } from 'vitest';
import { createMockRepository } from './mock-dashboard-repository';

describe('read-only mock repository', () => {
  it('returns deterministic isolated snapshots without network calls', async () => {
    const repository = createMockRepository(0);
    const first = await repository.read();
    const second = await repository.read();
    expect(first).toEqual(second);
    expect(first).not.toBe(second);
    first.analyses.length = 0;
    expect(second.analyses.length).toBe(28);
    expect(Object.keys(repository)).toEqual(['read']);
  });

  it('exposes a genuine empty state rather than fabricated zero NAV', async () => {
    const data = await createMockRepository(0).read({ scenario: 'empty' });
    expect(data.analyses).toEqual([]);
    expect(data.portfolio.positions).toEqual([]);
    expect(data.portfolio.performance).toEqual([]);
    expect(data.research.candidates).toEqual([]);
    expect(data.summary.totalValueKrw).toBeNull();
    expect(data.summary.botRealizedPnlKrw).toBeNull();
    expect(data.portfolio.summary.cashKrw).toBeNull();
    expect(data.portfolio.summary.knownValueKrw).toBeNull();
    expect(data.operations.workers).toEqual([]);
  });

  it('can demonstrate an error without contacting a server', async () => {
    await expect(createMockRepository(0).read({ scenario: 'error' })).rejects.toThrow('데모 오류');
  });

  it('cancels an indefinite loading scenario when the user changes scenario or unmounts', async () => {
    const controller = new AbortController();
    const pending = createMockRepository(0).read({
      scenario: 'loading',
      signal: controller.signal,
    });
    const assertion = expect(pending).rejects.toMatchObject({ name: 'AbortError' });
    controller.abort();
    await assertion;
  });

  it('rejects already aborted reads', async () => {
    const controller = new AbortController();
    controller.abort();
    await expect(createMockRepository(0).read({ signal: controller.signal })).rejects.toMatchObject(
      { name: 'AbortError' },
    );
  });
});
