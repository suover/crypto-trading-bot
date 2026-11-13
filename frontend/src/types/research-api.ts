/** API read models; never a partial or fabricated DashboardData. */
export type LongString = string & { readonly __long: unique symbol };
export type DecimalString = string & { readonly __decimal: unique symbol };
export interface Scope {
  userId: LongString;
  exchange: string;
  quoteAsset: string;
}
export interface Summary {
  scope: Scope;
  candidateCount: LongString;
  shadowEnrollmentCount: LongString;
  snapshotCount: LongString;
  latestSnapshotId: LongString | null;
  latestSnapshotCapturedAt: string | null;
  generatedAt: string;
}
export interface Page<T> {
  items: T[];
  page: number;
  size: number;
  totalElements: LongString;
  totalPages: LongString;
  generatedAt: string;
}
export interface Candidate {
  id: LongString;
  policyName: string;
  registeredAt: string;
  exchange: string;
  quoteAsset: string;
  effectiveTopN: number;
  shadowEnrolled: boolean;
}
export interface CandidateDetail extends Candidate {
  referenceSnapshotId: LongString;
  componentWeights: Record<string, DecimalString>;
  registrationContext: {
    datasetSchemaVersion: string;
    baselinePolicySignature: string;
    snapshotIdWatermark: LongString;
    capturedAtWatermark: string;
    referenceSnapshotCapturedAt: string;
  };
  enrollment: {
    id: LongString;
    shadowEnrolledAt: string;
    storedGateStatus: string;
    storedGateEvaluatedAt: string;
  } | null;
  generatedAt: string;
}
export interface Snapshot {
  id: LongString;
  capturedAt: string;
  createdAt: string;
  policySignature: string;
  researchCandidateCount: number;
  prefilterCandidateCount: number;
  rankedCandidateCount: number;
  finalCandidateCount: number;
}
export interface AutomationState {
  scope: Scope;
  datasetSchemaVersion: string;
  baselinePolicySignature: string;
  effectiveTopN: number;
  lastObservedSnapshotId: LongString;
  reviewedSnapshotId: LongString;
  reviewStatus: string;
  lastStatus: string | null;
  lastAttemptAt: string | null;
  lastReferenceSnapshotId: LongString | null;
  lastRegistrationStatus: string | null;
  lastRegisteredSnapshotId: LongString | null;
  lastRegisteredAt: string | null;
}
export interface AutomationStatus {
  scope: Scope;
  observed: boolean;
  states: AutomationState[];
  generatedAt: string;
}
export interface ApiErrorDto {
  code: string;
  message: string;
  generatedAt: string;
}
export type CandidateSort = `${'registeredAt' | 'id' | 'policyName'},${'asc' | 'desc'}`;
export interface CandidateFilters {
  keyword: string;
  registeredFrom: string;
  registeredTo: string;
  shadowEnrolled: '' | 'true' | 'false';
  page: number;
  size: number;
  sort: CandidateSort;
}
export const defaultFilters: CandidateFilters = {
  keyword: '',
  registeredFrom: '',
  registeredTo: '',
  shadowEnrolled: '',
  page: 0,
  size: 20,
  sort: 'registeredAt,desc',
};
