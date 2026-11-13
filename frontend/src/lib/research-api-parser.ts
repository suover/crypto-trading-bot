import { isLosslessNumber, parse, compareNumber } from 'lossless-json';
import type {
  Summary,
  Candidate,
  CandidateDetail,
  Snapshot,
  AutomationStatus,
  AutomationState,
  Scope,
  Page,
  LongString,
  DecimalString,
} from '@/types/research-api';

export class InvalidApiData extends Error {
  constructor() {
    super('잘못된 API 데이터입니다. 응답 계약을 확인해 주세요.');
    this.name = 'InvalidApiData';
  }
}
function invalid(): never {
  throw new InvalidApiData();
}
function object(v: unknown): Record<string, unknown> {
  if (typeof v !== 'object' || v === null || Array.isArray(v) || isLosslessNumber(v))
    return invalid();
  return v as Record<string, unknown>;
}
function text(v: unknown): string {
  return typeof v === 'string' && v.length > 0 ? v : invalid();
}
function bool(v: unknown): boolean {
  return typeof v === 'boolean' ? v : invalid();
}
function array(v: unknown): unknown[] {
  return Array.isArray(v) ? v : invalid();
}
function rawNumber(v: unknown): string {
  return isLosslessNumber(v) ? v.value : invalid();
}
function long(v: unknown, zero = false): LongString {
  const raw = rawNumber(v);
  if (!/^(0|[1-9]\d*)$/.test(raw) || raw.length > 19) return invalid();
  const n = BigInt(raw);
  if (n > 9223372036854775807n || n < (zero ? 0n : 1n)) return invalid();
  return raw as LongString;
}
export function validId(v: string): v is LongString {
  return /^[1-9]\d{0,18}$/.test(v) && BigInt(v) <= 9223372036854775807n;
}
function int(v: unknown, min = 0, max = 2147483647): number {
  const raw = long(v, true),
    n = BigInt(raw);
  return n >= BigInt(min) && n <= BigInt(max) ? Number(n) : invalid();
}
export function timestamp(v: unknown): string {
  const s = text(v);
  const parts = /^(\d{4}-\d{2}-\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.\d{1,9})?Z$/.exec(s);
  if (
    !parts ||
    +parts[2] > 23 ||
    +parts[3] > 59 ||
    +parts[4] > 59 ||
    !Number.isFinite(Date.parse(s)) ||
    new Date(parts[1] + 'T00:00:00Z').toISOString().slice(0, 10) !== parts[1]
  )
    return invalid();
  return s; // Keep original precision; Date is used only for validation/display.
}
function nullable<T>(v: unknown, read: (x: unknown) => T): T | null {
  return v === null ? null : read(v);
}
function scope(v: unknown): Scope {
  const o = object(v);
  return { userId: long(o.userId), exchange: text(o.exchange), quoteAsset: text(o.quoteAsset) };
}
function candidate(v: unknown): Candidate {
  const o = object(v);
  return {
    id: long(o.id),
    policyName: text(o.policyName),
    registeredAt: timestamp(o.registeredAt),
    exchange: text(o.exchange),
    quoteAsset: text(o.quoteAsset),
    effectiveTopN: int(o.effectiveTopN, 1),
    shadowEnrolled: bool(o.shadowEnrolled),
  };
}
function decode<T>(input: string, read: (v: unknown) => T): T {
  try {
    return read(parse(input));
  } catch {
    return invalid();
  }
}
function page<T>(v: unknown, read: (v: unknown) => T): Page<T> {
  const o = object(v),
    size = int(o.size, 1, 100),
    totalElements = long(o.totalElements, true);
  const totalPages = long(o.totalPages, true),
    pageNumber = int(o.page);
  const items = array(o.items).map(read);
  if (
    BigInt(totalPages) !== (BigInt(totalElements) + BigInt(size) - 1n) / BigInt(size) ||
    items.length > size ||
    BigInt(items.length) > BigInt(totalElements) ||
    (BigInt(pageNumber) >= BigInt(totalPages) && items.length !== 0)
  )
    return invalid();
  return {
    items,
    page: pageNumber,
    size,
    totalElements,
    totalPages,
    generatedAt: timestamp(o.generatedAt),
  };
}
export function parseSummary(input: string): Summary {
  return decode(input, (v) => {
    const o = object(v);
    const result = {
      scope: scope(o.scope),
      candidateCount: long(o.candidateCount, true),
      shadowEnrollmentCount: long(o.shadowEnrollmentCount, true),
      snapshotCount: long(o.snapshotCount, true),
      latestSnapshotId: nullable(o.latestSnapshotId, long),
      latestSnapshotCapturedAt: nullable(o.latestSnapshotCapturedAt, timestamp),
      generatedAt: timestamp(o.generatedAt),
    };
    if ((result.latestSnapshotId === null) !== (result.latestSnapshotCapturedAt === null))
      return invalid();
    return result;
  });
}
export const parseCandidates = (input: string): Page<Candidate> =>
  decode(input, (v) => page(v, candidate));
export function parseDetail(input: string): CandidateDetail {
  return decode(input, (v) => {
    const o = object(v),
      base = candidate(v),
      context = object(o.registrationContext);
    const weights = Object.fromEntries(
      Object.entries(object(o.componentWeights)).map(([key, value]) => {
        const raw = rawNumber(value);
        // Weight domain is finite 0..1. Comparison never passes through Number.
        const exponent = /e([+-]?\d+)$/i.exec(raw);
        if (
          raw.length > 512 ||
          (exponent && (BigInt(exponent[1]) > 308n || BigInt(exponent[1]) < -308n)) ||
          compareNumber(raw, '0') < 0 ||
          compareNumber(raw, '1') > 0
        )
          return invalid();
        return [key, raw as DecimalString];
      }),
    );
    const enrollment = nullable(o.enrollment, (v) => {
      const e = object(v);
      return {
        id: long(e.id),
        shadowEnrolledAt: timestamp(e.shadowEnrolledAt),
        storedGateStatus: text(e.storedGateStatus),
        storedGateEvaluatedAt: timestamp(e.storedGateEvaluatedAt),
      };
    });
    if (base.shadowEnrolled !== (enrollment !== null)) return invalid();
    return {
      ...base,
      referenceSnapshotId: long(o.referenceSnapshotId),
      componentWeights: weights,
      registrationContext: {
        datasetSchemaVersion: text(context.datasetSchemaVersion),
        baselinePolicySignature: text(context.baselinePolicySignature),
        snapshotIdWatermark: long(context.snapshotIdWatermark),
        capturedAtWatermark: timestamp(context.capturedAtWatermark),
        referenceSnapshotCapturedAt: timestamp(context.referenceSnapshotCapturedAt),
      },
      enrollment,
      generatedAt: timestamp(o.generatedAt),
    };
  });
}
export function parseSnapshots(input: string): Page<Snapshot> {
  return decode(input, (v) =>
    page(v, (v) => {
      const o = object(v);
      return {
        id: long(o.id),
        capturedAt: timestamp(o.capturedAt),
        createdAt: timestamp(o.createdAt),
        policySignature: text(o.policySignature),
        researchCandidateCount: int(o.researchCandidateCount),
        prefilterCandidateCount: int(o.prefilterCandidateCount),
        rankedCandidateCount: int(o.rankedCandidateCount),
        finalCandidateCount: int(o.finalCandidateCount),
      };
    }),
  );
}
export function parseAutomation(input: string): AutomationStatus {
  return decode(input, (v) => {
    const o = object(v),
      s = scope(o.scope),
      observed = bool(o.observed);
    const states = array(o.states).map((v): AutomationState => {
      const a = object(v);
      return {
        scope: scope(a.scope),
        datasetSchemaVersion: text(a.datasetSchemaVersion),
        baselinePolicySignature: text(a.baselinePolicySignature),
        effectiveTopN: int(a.effectiveTopN, 1),
        lastObservedSnapshotId: long(a.lastObservedSnapshotId),
        reviewedSnapshotId: long(a.reviewedSnapshotId),
        reviewStatus: text(a.reviewStatus),
        lastStatus: nullable(a.lastStatus, text),
        lastAttemptAt: nullable(a.lastAttemptAt, timestamp),
        lastReferenceSnapshotId: nullable(a.lastReferenceSnapshotId, long),
        lastRegistrationStatus: nullable(a.lastRegistrationStatus, text),
        lastRegisteredSnapshotId: nullable(a.lastRegisteredSnapshotId, long),
        lastRegisteredAt: nullable(a.lastRegisteredAt, timestamp),
      };
    });
    const identities = states.map((a) =>
      JSON.stringify([
        a.scope.userId,
        a.scope.exchange,
        a.scope.quoteAsset,
        a.datasetSchemaVersion,
        a.baselinePolicySignature,
        a.effectiveTopN,
      ]),
    );
    if (
      observed !== states.length > 0 ||
      new Set(identities).size !== states.length ||
      states.some(
        (a) =>
          a.scope.userId !== s.userId ||
          a.scope.exchange !== s.exchange ||
          a.scope.quoteAsset !== s.quoteAsset,
      )
    )
      return invalid();
    return { scope: s, observed, states, generatedAt: timestamp(o.generatedAt) };
  });
}
