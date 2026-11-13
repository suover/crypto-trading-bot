import { useRef, type ReactNode } from 'react';
import {
  AlertCircle,
  ArrowDownRight,
  ArrowUpRight,
  ChevronLeft,
  ChevronRight,
  Inbox,
  Info,
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip';
import { cn } from '@/lib/utils';

export function SectionHeading({
  eyebrow,
  title,
  description,
  action,
}: {
  eyebrow?: string;
  title: string;
  description?: string;
  action?: ReactNode;
}) {
  return (
    <div className="section-heading">
      <div>
        {eyebrow && <span className="eyebrow">{eyebrow}</span>}
        <h1>{title}</h1>
        {description && <p>{description}</p>}
      </div>
      {action && <div className="heading-action">{action}</div>}
    </div>
  );
}

export function Panel({
  title,
  description,
  action,
  children,
  className,
}: {
  title: string;
  description?: string;
  action?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section className={cn('card panel', className)}>
      <div className="panel-heading">
        <div>
          <h2>{title}</h2>
          {description && <p>{description}</p>}
        </div>
        {action}
      </div>
      {children}
    </section>
  );
}

export function MetricCard({
  label,
  value,
  detail,
  trend,
  icon,
  className,
}: {
  label: string;
  value: ReactNode;
  detail?: ReactNode;
  trend?: ReactNode;
  icon?: ReactNode;
  className?: string;
}) {
  return (
    <article className={cn('card metric-card', className)}>
      <div className="metric-label">
        {label}
        {icon}
      </div>
      <div className="metric-value">{value}</div>
      <div className="metric-footer">
        {trend && <span className="metric-trend">{trend}</span>}
        {detail && <span>{detail}</span>}
      </div>
    </article>
  );
}

const statuses: Record<string, { label: string; tone: string }> = {
  LIVE_DONE: { label: '체결 완료', tone: 'positive' },
  ELIGIBLE_FOR_PROMOTION_REVIEW: { label: '승격 검토 가능', tone: 'positive' },
  INVALID_DATA: { label: '데이터 무효', tone: 'negative' },
  CREATED: { label: '추천 생성', tone: 'neutral' },
  APPROVAL_PENDING: { label: '승인 대기', tone: 'warning' },
  APPROVAL_EXPIRED: { label: '승인 만료', tone: 'neutral' },
  LIVE_EXECUTION_UNKNOWN: { label: '체결 확인 필요', tone: 'warning' },
  BUY: { label: 'BUY', tone: 'positive' },
  SELL: { label: 'SELL', tone: 'negative' },
  HOLD: { label: 'HOLD', tone: 'neutral' },
  RUNNING: { label: 'Running', tone: 'positive' },
  DISABLED: { label: 'Disabled', tone: 'neutral' },
  ERROR: { label: 'Error', tone: 'negative' },
  UNKNOWN: { label: 'Unknown', tone: 'warning' },
  COMPLETE: { label: '완료', tone: 'positive' },
  SUCCESS: { label: '정상 완료', tone: 'positive' },
  PARTIAL: { label: 'PARTIAL', tone: 'warning' },
  FAILED: { label: '실패', tone: 'negative' },
  PENDING: { label: '승인 대기', tone: 'warning' },
  APPROVED: { label: '승인됨', tone: 'positive' },
  REJECTED: { label: '거절됨', tone: 'neutral' },
  EXPIRED: { label: '만료됨', tone: 'neutral' },
  NOT_REQUIRED: { label: '승인 불필요', tone: 'neutral' },
  LIVE_EXECUTED: { label: '체결 완료', tone: 'positive' },
  LIVE_EXECUTED_CANCELLED: { label: '체결 · 미체결 잔량 취소', tone: 'positive' },
  LIVE_WAIT: { label: '주문 대기', tone: 'warning' },
  LIVE_UNKNOWN: { label: '확인 필요', tone: 'warning' },
  LIVE_CANCELLED: { label: '미체결 취소', tone: 'neutral' },
  NOT_EXECUTED: { label: '주문 없음', tone: 'neutral' },
  INVALID_PROMOTION_DATA: { label: '검증 데이터 무효', tone: 'negative' },
  INSUFFICIENT_DATA: { label: '데이터 부족', tone: 'warning' },
  NOT_ELIGIBLE: { label: '기준 미충족', tone: 'negative' },
  ELIGIBLE_FOR_REVIEW: { label: '검토 가능', tone: 'positive' },
  ELIGIBLE_FOR_HUMAN_REVIEW: { label: '사람 검토 가능', tone: 'positive' },
  BASELINE: { label: 'BASELINE', tone: 'blue' },
  FULL_LIVE: { label: 'FULL LIVE', tone: 'blue' },
  INACTIVE: { label: '비활성', tone: 'neutral' },
  NOT_APPROVED: { label: '미승인', tone: 'neutral' },
  ENROLLED: { label: 'Shadow 등록', tone: 'blue' },
  FULLY_MATCHED: { label: '전체 매칭', tone: 'positive' },
  PARTIALLY_MATCHED: { label: '부분 매칭', tone: 'warning' },
  UNMATCHED: { label: '매칭 불가', tone: 'neutral' },
  PRICED: { label: '평가 완료', tone: 'positive' },
  UNPRICED: { label: '평가 불가', tone: 'warning' },
  EXCLUDED: { label: '계산 대상 제외', tone: 'neutral' },
  SUFFICIENT: { label: '충분', tone: 'positive' },
  INSUFFICIENT: { label: '부족', tone: 'warning' },
  UNAVAILABLE: { label: '데이터 없음', tone: 'neutral' },
  UPTREND: { label: '상승 추세', tone: 'positive' },
  DOWNTREND: { label: '하락 추세', tone: 'negative' },
  NEUTRAL: { label: '중립', tone: 'neutral' },
  PASS: { label: 'PASS', tone: 'positive' },
  FAIL: { label: 'FAIL', tone: 'negative' },
  INVALID: { label: '무효', tone: 'negative' },
  NOT_STARTED: { label: '미진행', tone: 'neutral' },
  IN_PROGRESS: { label: '검증 중', tone: 'blue' },
  READY_FOR_REVIEW: { label: '검토 가능', tone: 'positive' },
  INFO: { label: 'Info', tone: 'blue' },
  WARNING: { label: 'Warning', tone: 'warning' },
  OPEN: { label: '미해결', tone: 'warning' },
  RESOLVED: { label: '해결됨', tone: 'neutral' },
  RANKED: { label: 'RANKED', tone: 'blue' },
  HELD: { label: 'HELD', tone: 'neutral' },
};

export function StatusBadge({ status }: { status: string }) {
  const entry = statuses[status] ?? { label: status, tone: 'neutral' };
  return (
    <span className="status-badge" data-tone={entry.tone} title={status}>
      <span className="status-dot" aria-hidden="true" />
      {entry.label}
    </span>
  );
}

export function EmptyState({
  title = '표시할 데이터가 없습니다',
  description,
}: {
  title?: string;
  description?: string;
}) {
  return (
    <div className="empty-state" role="status">
      <span className="empty-icon">
        <Inbox size={23} />
      </span>
      <h3>{title}</h3>
      <p>{description ?? '검색어나 필터를 변경해 다시 확인해 주세요.'}</p>
    </div>
  );
}

export function ErrorState({ onRetry }: { onRetry: () => void }) {
  return (
    <div className="card empty-state" role="alert">
      <span className="empty-icon negative">
        <AlertCircle size={24} />
      </span>
      <h2>데모 데이터를 불러오지 못했습니다</h2>
      <p>오류 시나리오를 확인 중입니다. 하단에서 정상 데이터를 선택하거나 다시 시도하세요.</p>
      <Button variant="outline" onClick={onRetry}>
        다시 시도
      </Button>
    </div>
  );
}

export function LoadingSkeleton() {
  return (
    <div className="loading-skeleton" role="status" aria-label="데이터 불러오는 중">
      <span className="sr-only">데이터 불러오는 중</span>
      <div className="grid-metrics">
        {['a', 'b', 'c', 'd'].map((key) => (
          <div key={key} className="card skeleton metric-card" />
        ))}
      </div>
      <div className="card skeleton" style={{ height: 360 }} />
    </div>
  );
}

export function DetailDrawer({
  open,
  onOpenChange,
  title,
  description,
  children,
  sourceLabel = 'DETAIL VIEW · DEMO',
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: string;
  description?: string;
  children: ReactNode;
  sourceLabel?: string;
}) {
  const opener = useRef<HTMLElement | null>(null);
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        className="detail-drawer"
        onOpenAutoFocus={() => {
          const active = document.activeElement;
          opener.current =
            active instanceof HTMLElement && active !== document.body ? active : null;
        }}
        onCloseAutoFocus={(event) => {
          event.preventDefault();
          const target = opener.current?.isConnected
            ? opener.current
            : document.querySelector<HTMLElement>('#main-content');
          target?.focus();
        }}
      >
        <DialogHeader>
          <span className="eyebrow">{sourceLabel}</span>
          <DialogTitle>{title}</DialogTitle>
          <DialogDescription>
            {description ?? 'Mock 데이터의 조회 전용 상세 정보입니다.'}
          </DialogDescription>
        </DialogHeader>
        <div className="drawer-body">{children}</div>
      </DialogContent>
    </Dialog>
  );
}

export function InfoTip({ label, children }: { label: string; children?: ReactNode }) {
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <button className="info-tip" type="button" aria-label={label}>
          {children ?? <Info size={14} />}
        </button>
      </TooltipTrigger>
      <TooltipContent className="max-w-xs">{label}</TooltipContent>
    </Tooltip>
  );
}

export function Pagination({
  page,
  pageCount,
  total,
  onPageChange,
}: {
  page: number;
  pageCount: number;
  total: number;
  onPageChange: (page: number) => void;
}) {
  return (
    <nav className="pagination" aria-label="페이지 이동">
      <span>
        총 {total.toLocaleString('ko-KR')}개{' '}
        <span className="text-muted">
          · {page} / {Math.max(1, pageCount)} 페이지
        </span>
      </span>
      <div className="pagination-actions">
        <Button
          variant="outline"
          size="icon"
          aria-label="이전 페이지"
          disabled={page <= 1}
          onClick={() => onPageChange(page - 1)}
        >
          <ChevronLeft size={16} />
        </Button>
        <Button
          variant="outline"
          size="icon"
          aria-label="다음 페이지"
          disabled={page >= pageCount}
          onClick={() => onPageChange(page + 1)}
        >
          <ChevronRight size={16} />
        </Button>
      </div>
    </nav>
  );
}

export function CoinIdentity({
  symbol,
  name,
  market,
}: {
  symbol: string;
  name?: string;
  market?: string | null;
}) {
  return (
    <span className="asset-cell">
      <span className={cn('asset-symbol', `asset-${symbol.toLowerCase()}`)} aria-hidden="true">
        {symbol === 'BTC'
          ? '₿'
          : symbol === 'ETH'
            ? 'Ξ'
            : symbol === 'KRW'
              ? '₩'
              : symbol.slice(0, 1)}
      </span>
      <span>
        <strong>{name ?? symbol}</strong>
        <span className="table-subtext">{market ?? symbol}</span>
      </span>
    </span>
  );
}

export function Change({ value, children }: { value: number | null; children: ReactNode }) {
  return (
    <span
      className={cn(
        'change',
        value === null || value === 0 ? 'text-muted' : value > 0 ? 'positive' : 'negative',
      )}
    >
      {value !== null &&
        value !== 0 &&
        (value > 0 ? <ArrowUpRight size={14} /> : <ArrowDownRight size={14} />)}
      {children}
    </span>
  );
}
