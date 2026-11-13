// Synthetic DTO-shaped HTTP fixtures. Numeric literals deliberately exceed JS safe integers.
export const bigId = '9007199254740993';
export const referenceId = '9007199254740995';
export const at = '2026-01-01T00:00:00.123456Z';
const scope = '{"userId":1,"exchange":"UPBIT","quoteAsset":"KRW"}';
export const summaryBody = `{"scope":${scope},"candidateCount":21,"shadowEnrollmentCount":1,"snapshotCount":21,"latestSnapshotId":${referenceId},"latestSnapshotCapturedAt":"${at}","generatedAt":"2026-01-01T01:00:00Z"}`;
export function candidateBody(id = bigId, name = 'API Policy', enrolled = true) {
  return `{"id":${id},"policyName":${JSON.stringify(name)},"registeredAt":"${at}","exchange":"UPBIT","quoteAsset":"KRW","effectiveTopN":7,"shadowEnrolled":${enrolled}}`;
}
export const detailBody = `{${candidateBody().slice(1, -1)},"referenceSnapshotId":${referenceId},
  "componentWeights":{"liquidity":0.123456789123456789,"trend_alignment":0.876543210987654321,"volume_confirmation":0},
  "registrationContext":{"datasetSchemaVersion":"dataset-v1","baselinePolicySignature":"baseline-identity","snapshotIdWatermark":${referenceId},"capturedAtWatermark":"${at}","referenceSnapshotCapturedAt":"${at}"},
  "enrollment":{"id":301,"shadowEnrolledAt":"${at}","storedGateStatus":"ELIGIBLE_FOR_REVIEW","storedGateEvaluatedAt":"${at}"},"generatedAt":"${at}"}`;
export function pageBody(items: string[], page = 0, size = 20, total = items.length) {
  return `{"items":[${items.join(',')}],"page":${page},"size":${size},"totalElements":${total},"totalPages":${Math.ceil(total / size)},"generatedAt":"${at}"}`;
}
export function snapshotBody(id = referenceId) {
  return `{"id":${id},"capturedAt":"${at}","createdAt":"${at}","policySignature":"stored-policy","researchCandidateCount":20,"prefilterCandidateCount":10,"rankedCandidateCount":7,"finalCandidateCount":8}`;
}
function state(signature: string, top: number) {
  return `{"scope":${scope},"datasetSchemaVersion":"dataset-v1","baselinePolicySignature":"${signature}","effectiveTopN":${top},"lastObservedSnapshotId":${referenceId},"reviewedSnapshotId":${bigId},"reviewStatus":"OBSERVED","lastStatus":"INSUFFICIENT_RESEARCH_DATA","lastAttemptAt":null,"lastReferenceSnapshotId":null,"lastRegistrationStatus":null,"lastRegisteredSnapshotId":null,"lastRegisteredAt":null}`;
}
export const automationBody = `{"scope":${scope},"observed":true,"states":[${state('baseline-a', 7)},${state('baseline-b', 5)}],"generatedAt":"${at}"}`;
export const emptyAutomationBody = `{"scope":${scope},"observed":false,"states":[],"generatedAt":"${at}"}`;
export function fixtureResponse(url: URL): string {
  const page = Number(url.searchParams.get('page') ?? 0),
    size = Number(url.searchParams.get('size') ?? 20);
  if (url.pathname.endsWith('/summary')) return summaryBody;
  if (url.pathname.endsWith('/automation-status')) return automationBody;
  if (url.pathname.endsWith('/snapshots')) {
    return pageBody(
      Array.from({ length: Math.max(0, Math.min(size, 21 - page * size)) }, (_, i) =>
        snapshotBody(String(BigInt(referenceId) + BigInt(page * size + i))),
      ),
      page,
      size,
      21,
    );
  }
  if (url.pathname.endsWith('/candidates')) {
    const keyword = url.searchParams.get('keyword');
    if (keyword)
      return pageBody(
        keyword === 'missing' ? [] : [candidateBody()],
        page,
        size,
        keyword === 'missing' ? 0 : 1,
      );
    return pageBody(
      Array.from({ length: Math.max(0, Math.min(size, 21 - page * size)) }, (_, i) =>
        candidateBody(
          String(BigInt(bigId) + BigInt(page * size + i)),
          page * size + i === 0 ? 'API Policy' : 'API Policy ' + (page * size + i),
          i === 0,
        ),
      ),
      page,
      size,
      21,
    );
  }
  return detailBody;
}
