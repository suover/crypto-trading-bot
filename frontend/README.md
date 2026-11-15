# Crypto Trading Bot Dashboard

Python trading/research engine의 주요 개념을 살펴볼 수 있는 React 기반 **조회 전용 대시보드**입니다. 기본 DEMO는 계좌 가치, AI 판단, 체결, 연구 정책과 운영 상태를 다섯 화면으로 제공합니다. 배포용 빌드에서는 비로그인은 DEMO, 로그인 후에는 연결된 거래 사용자의 Research, Overview, AI Analysis 데이터로 자동 전환합니다. 개발 서버에서도 API 플래그를 켜면 같은 흐름을 확인할 수 있습니다.

DEMO의 모든 금액·상태·시각은 합성 데이터입니다. 개발 서버에서 API 플래그를 모두 끄면 백엔드 없이 데모를 볼 수 있습니다. API 연결 환경에서는 진입 시 서버 세션을 확인하고, 비로그인 상태에서는 업무 API를 호출하지 않습니다. 업무 데이터는 Spring Boot Research / Overview / AI Trade Analysis GET API만 조회합니다. 브라우저가 PostgreSQL, Upbit, OpenAI, Telegram을 직접 호출하지 않습니다.

## 기술 스택

| 영역          | 구현                                                                             |
| ------------- | -------------------------------------------------------------------------------- |
| UI            | React 19, TypeScript strict mode, Vite 8                                         |
| 디자인        | Tailwind CSS v4의 Vite plugin, CSS theme tokens, shadcn/ui Button·Dialog·Tooltip |
| 접근성·아이콘 | Radix UI, Lucide React                                                           |
| 시각화        | Recharts                                                                         |
| 라우팅        | React Router 8의 declarative routing                                             |
| 데이터        | TanStack Query, TypeScript read DTO, 교체 가능한 Mock repository                 |
| 검증          | ESLint, Prettier, Vitest, React Testing Library, Playwright                      |

정확한 설치 버전은 `package-lock.json`으로 고정합니다. 추가 UI library를 설치하기보다 기존 공통 컴포넌트와 theme token을 재사용합니다.

## 설치와 실행

**Node.js 24 LTS의 24.15.0 이상 버전**을 권장합니다. `package.json`의 지원 범위는 `>=24.15.0 <25 || >=26.0.0`이며 Node 25는 포함하지 않습니다. npm을 사용합니다.

Repository root에서:

```bash
cd frontend
npm ci
npm run dev
```

개발 서버는 [http://localhost:5173](http://localhost:5173)에서 열 수 있습니다. 서버는 `127.0.0.1`에만 바인딩하며 `strictPort`를 사용하므로 5173 포트가 사용 중이면 다른 포트로 조용히 바뀌지 않습니다. Vite HMR로 파일 수정이 화면에 반영됩니다. 종료는 실행 터미널에서 `Ctrl+C`입니다.

기존 lockfile로 재현할 때는 `npm ci`를 사용합니다. 의도적으로 dependency를 변경하는 경우에만 `npm install`로 `package.json`과 lockfile을 함께 갱신합니다.

### API 연결용 빌드 로컬 확인

[백엔드 실행 절차](../backend/README.md#백엔드-실행)에 따라 계정이 등록된 로컬 DB에 Spring Boot를 연결하고 `127.0.0.1:8080`에서 실행합니다. 로컬 HTTP 미리보기에는 백엔드의 `BACKEND_SESSION_COOKIE_SECURE=false`를 사용합니다. `frontend/`에서 다음 두 명령을 실행합니다.

```bash
npm run build
npm run preview
```

[미리보기](http://127.0.0.1:4173/overview)에서 비로그인 → DEMO, 로그인 → 사용자 데이터, 로그아웃 → DEMO 전환을 확인합니다. `build`는 `dist/`를 생성하며 별도의 `VITE_ENABLE_*_API` 값 없이 Overview, Research, AI Analysis를 활성화합니다. `preview`는 그 산출물을 제공하고 같은 주소의 허용된 `/api` 요청을 로컬 Spring Boot로 전달합니다. 세션을 확인할 수 없으면 오류를 표시합니다. 백엔드를 다른 로컬 포트에서 실행할 때만 `DASHBOARD_PREVIEW_BACKEND_PORT`를 지정합니다.

### 배포 시 동일 출처 구성

프론트·백엔드 컨테이너를 함께 배포하는 기본 절차는 [Web Deployment](../docs/WEB_DEPLOYMENT.md)입니다. 웹 전용 Compose와 수동 Deploy Web을 사용하고, 격리 DB를 통한 실제 HTTPS 로그인 검증을 제공합니다. 아래 설정은 Spring을 같은 호스트에서 직접 실행할 때의 별도 예시입니다.

`dist/`의 **내용**을 같은 호스트의 HTTPS 웹 서버에 제공합니다. [Nginx 설정 예시](deploy/dashboard.locations.conf)는 HTTPS `server` 블록에 포함하는 설정으로, 정적 파일과 화면 경로를 제공하고 `/api/`를 loopback Spring Boot에 전달합니다. 예시의 정적 파일 위치는 `/var/www/crypto-trading-dashboard`입니다. HTTPS 서버 이름과 인증서는 배포 환경에서 설정합니다.

백엔드와 PostgreSQL에는 브라우저가 직접 접근하지 않도록 하고, 백엔드는 DB 읽기 역할과 기본 `Secure` 세션 쿠키를 사용합니다. `/api/`는 공유 캐시에서 제외하고 Spring Security가 세션·CSRF·조회 경로를 검증합니다. 예시 프록시는 클라이언트가 보낸 전달 헤더를 제거합니다. 현재 백엔드 로그인 제한은 연결 주소 기준이므로 이 구성에서는 프록시 주소에 합산됩니다. 공개 배포 전에는 대상 환경의 로그인 제한과 TLS 구성도 확인합니다. 웹 서버는 기존 거래 엔진 배포와 별도로 준비합니다.

## 다섯 화면

| 경로           | 화면          | 주요 내용                                                                                             |
| -------------- | ------------- | ----------------------------------------------------------------------------------------------------- |
| `/overview`    | 대시보드      | 총자산·계좌 성과·bot PnL, 기간별 자산 차트, 보유 자산, 최근 추천, 연구·운영 요약                      |
| `/ai-analysis` | AI 매매 분석  | 마켓·액션·날짜·승인 상태 검색/필터, 정렬/페이지네이션, Multi-Timeframe 지표와 추천/실행 상세          |
| `/portfolio`   | 자산 및 거래  | 자산 추이·구성, 보유 자산 검색/정렬, 체결 내역, FIFO 매칭과 Bot Trading PnL                           |
| `/research`    | 전략 연구     | Runtime 정책, 11단계 lifecycle, 후보 목록, 가중치 비교, Historical/Forward/Shadow evidence, Gate 상세 |
| `/operations`  | 운영 모니터링 | 모의 worker 상태, 분석 일정, Research Lifecycle 처리 현황, 이벤트와 경고                              |

위 표는 DEMO 화면입니다. `/`는 `/overview`로 이동합니다. 없는 경로에는 404 화면을 제공합니다. 공통 레이아웃은 현재 화면의 출처에 따라 **DEMO** 또는 **API · READ ONLY**를 표시합니다. Research, Overview, AI Analysis는 세션 상태에 따라 출처가 함께 전환됩니다. 로그인 후 Portfolio / Operations 및 API flag가 꺼진 화면에는 **실제 데이터 미연동** 안내를 표시합니다. 해당 화면의 데모는 로그아웃 상태에서만 표시합니다. API 모드에는 Mock 데이터 기준 시각과 시나리오 선택을 표시하지 않습니다.

Sidebar는 데스크톱에서 접을 수 있고 모바일에서는 Drawer로 열립니다. 모바일 메뉴를 선택하면 Drawer가 닫힙니다. 다크 테마가 기본이며 라이트 테마로 전환할 수 있습니다. 선택한 테마만 브라우저 localStorage에 저장합니다.

## 화면에서 시험할 수 있는 상태

페이지 하단의 **데모 데이터 상태** 선택으로 전체 query의 시나리오를 바꿀 수 있습니다.

| 선택                | 동작                                                                        |
| ------------------- | --------------------------------------------------------------------------- |
| 정상 데이터         | 고정된 합성 dataset의 다섯 화면                                             |
| 부분 평가 · PARTIAL | 가격이 없는 held asset을 포함한 불완전 NAV; 총자산·수익률·비중을 `—`로 표시 |
| 빈 데이터           | 추천·자산·연구 후보·이벤트 목록이 없는 상태                                 |
| 로딩                | Skeleton을 유지하여 로딩 화면 확인                                          |
| 오류                | Mock repository 오류와 다시 시도 UI 확인                                    |

로딩/오류 시나리오에서 정상 화면으로 돌아가려면 하단에서 **정상 데이터**를 선택합니다. 오류 시나리오를 유지한 채 다시 시도하면 동일한 모의 오류가 발생합니다. 어떤 시나리오도 실제 API 오류나 운영 상태를 뜻하지 않습니다.

차트의 7일/30일/90일/전체 선택은 실제 fixture 시계열을 필터링합니다. 테이블 검색·필터·정렬·페이지 이동과 상세 Drawer/탭도 브라우저 상태로 동작합니다. 정책 등록·승인·활성화 또는 주문을 성공 처리하는 가짜 동작은 없습니다.

## 데이터 의미와 경계

- Mock 기준 시각은 `src/data/mock/dashboard.ts`의 `DEMO_AS_OF`입니다. 매번 임의의 성과를 만들거나 현재 시각으로 운영 상태를 추정하지 않습니다.
- 금액·가격·수량은 DTO에서 decimal **문자열**로 유지합니다. `src/lib/format.ts`의 숫자 변환은 차트/표시 목적이며 거래 sizing이나 회계 기록을 만들지 않습니다.
- `null`과 숫자 0을 구별합니다. `PARTIAL`, `UNPRICED`, `UNKNOWN`, 매칭 불가 상태를 완전한 0원 결과로 바꾸지 않습니다.
- UTC timestamp는 `Intl.DateTimeFormat`의 `Asia/Seoul`로 표시합니다. 금액은 KRW, 성과는 percentage point, confidence와 trade ratio는 0–1 비율 DTO에서 변환합니다.
- 계좌 Portfolio Performance와 bot fill 기반 FIFO realized PnL은 별도 지표입니다. 원가가 입증되지 않은 매도를 수익으로 계산하지 않습니다.
- BUY/SELL 추천과 승인, 실제 주문 체결은 분리합니다. HOLD는 정상 판단이며 승인 요청이 필요하지 않습니다. 모델 confidence는 검증된 성공 확률이 아닙니다.
- Ranking gross/cost-adjusted evidence는 실제 체결 손익이 아닙니다. Gate eligibility, Shadow, 사람 승인과 Full LIVE activation은 서로 다른 단계입니다.
- Gate 상태·비교 연산자·기준값은 Mock read model이 제공합니다. 화면은 기준값을 운영 정책으로 실행하거나 후보의 승격 여부를 새로 판정하지 않습니다.
- Research Lifecycle Phase 1의 자동화 경계는 기존 후보의 Gate 검증과 조건부 Shadow Enrollment입니다. 신규 후보 자동 생성과 Telegram 전략 승인은 현재 UI의 실행 기능이 아닙니다.

## 구조와 API 확장 지점

```text
frontend/
├── public/                 # 로컬 SVG favicon
├── src/
│   ├── app/                # 지연 로드하는 page routes
│   ├── components/
│   │   ├── ui/             # shadcn/Radix primitives
│   │   ├── common/         # metric, panel, status, drawer, states, pagination
│   │   ├── layout/         # Sidebar, Header, AppShell
│   │   └── charts/         # 공통 자산 차트와 기간 필터
│   ├── pages/              # overview, ai-analysis, portfolio, research, operations
│   ├── types/              # Mock DTO와 Research / Overview read DTO 분리
│   ├── data/mock/          # 결정적인 공통 fixture와 연구 데이터
│   ├── services/           # Mock repository, Research / Overview GET client
│   ├── hooks/              # Mock과 독립 Research / Overview Query/source provider
│   ├── lib/                # formatter, lossless API 응답 검증
│   ├── styles/             # 공통 tokens와 반응형 스타일
│   └── test/               # jsdom test setup
├── e2e/                    # 로컬 브라우저 회귀 검증
├── playwright.config.ts
├── package.json
├── package-lock.json
└── vite.config.ts
```

`@/`는 `src/` alias입니다. 데이터 흐름은 다음과 같습니다.

```text
Page → useDashboardData → DashboardRepository.read → Mock fixture
```

TanStack Query는 동일 dataset을 화면 간 공유하고 시나리오별 query key와 취소 signal을 사용합니다. Fixture는 repository에서 복사해 반환하므로 화면의 표시 처리로 원본 데이터가 바뀌지 않습니다.

Research API는 전체 `DashboardData`를 억지로 채우지 않고 독립 타입·화면·Query를 사용합니다. 기존 Research DEMO 차트·lifecycle·상세 탭은 `ResearchDemoView`에 유지합니다.

```text
ResearchPage → ResearchApiView → useResearch* Query
  → research-api-client (GET /api/v1/research/…)
  → response.text → lossless-json → DTO validation → read model
  → Vite loopback proxy → existing Spring Boot → SELECT-only PostgreSQL
```

## LOCAL API 실행과 인증

세 화면을 함께 확인하려면 로컬 Backend를 실행한 뒤 repository root의 PowerShell에서 다음을 실행합니다. 파일이나 운영 설정을 변경하지 않습니다.

```powershell
cd frontend
$env:VITE_ENABLE_OVERVIEW_API = 'true'
$env:VITE_ENABLE_RESEARCH_API = 'true'
$env:VITE_ENABLE_AI_ANALYSIS_API = 'true'
npm run dev
```

[대시보드](http://127.0.0.1:5173/overview)에서 비로그인 DEMO → 관리자 로그인 → 사용자 데이터 → 로그아웃 → DEMO 순서로 확인합니다. 세션 확인 오류나 로그아웃 실패에는 데이터를 숨기고 재확인 안내를 표시합니다. 사용자 범위는 기존 Backend가 `auth.accounts.trading_user_id`로 결정하며 브라우저에서 사용자 ID를 지정하지 않습니다. Spring Security 세션·CSRF와 API 쓰기 차단은 유지합니다.

먼저 [Backend README](../backend/README.md)의 절차로 **별도 로컬 PostgreSQL**, 기존 Alembic schema, SELECT 전용 DB 계정, active Trading User scope와 관리자 BCrypt 설정을 준비하고 Spring Boot를 `127.0.0.1:8080`에서 실행합니다. 이 frontend는 서버를 자동 시작하거나 관리자 자격 증명을 생성하지 않습니다. Production DB/자격 증명은 개발·테스트에 사용하지 않습니다.

Frontend 터미널에서 파일 수정 없이 일회성 환경변수로 허용합니다.

```bash
cd frontend
VITE_ENABLE_RESEARCH_API=true npm run dev
```

PowerShell에서는:

```powershell
cd frontend
$env:VITE_ENABLE_RESEARCH_API = "true"
try { npm run dev } finally { Remove-Item Env:VITE_ENABLE_RESEARCH_API }
```

`VITE_ENABLE_RESEARCH_API`는 비밀이 아닌 opt-in flag이며 기본 비활성입니다. `import.meta.env.DEV`도 참이어야 합니다. 데이터 출처는 서버 세션 확인 결과로 결정합니다. 새로고침 시에도 유효한 세션이 있으면 해당 API 화면을 자동으로 복원합니다. localStorage, sessionStorage, URL에 mode나 credential을 저장하지 않습니다. 기존 테마 저장 기능은 유지합니다.

1. Backend README의 관리자 설정과 로컬 HTTP용 `BACKEND_SESSION_COOKIE_SECURE=false`로 서버를 시작합니다.
2. [http://127.0.0.1:5173/research](http://127.0.0.1:5173/research)를 엽니다. 진입 시 세션을 확인하고, 비로그인이면 DEMO를 표시합니다. 확인 중에는 DEMO/API 수치를 표시하지 않습니다.
3. 헤더의 **관리자 로그인**을 누르고 계정을 입력하면 원래 페이지의 사용자 데이터로 전환합니다. 실제 추천 상세 주소도 유지합니다. 직접 `/login`을 열어 로그인하면 Overview로 이동합니다.
4. 새로고침 후 유효한 세션이면 사용자 데이터를 다시 조회합니다. 헤더의 **로그아웃**은 서버 세션을 종료하고 진행 중인 조회/캐시를 제거한 뒤 같은 페이지의 DEMO로 돌아갑니다.

로그인 화면만 비밀번호를 일시적으로 입력받고 제출 시 입력값을 지웁니다. URL, localStorage/sessionStorage, 번들에 credential을 보관하지 않습니다. `credentials: same-origin` 세션 쿠키를 사용하고 React는 HttpOnly 쿠키를 읽지 않습니다. API 401 시 조회를 취소하고 캐시를 제거한 뒤 DEMO로 전환하며, 403/서버 장애를 로그아웃 성공이나 DEMO 응답으로 바꾸지 않습니다. 로그아웃 실패 시 실제 데이터를 숨기고 상태 재확인을 안내합니다.

주소의 `localhost`와 `127.0.0.1`을 혼용하지 마세요. 로컬 UI/API는 `127.0.0.1:5173`의 같은 origin으로 사용합니다. 서버 세션 만료는 다음 인증/조회 요청에서 확인됩니다. 다른 탭에서 로그아웃한 경우 현재 탭은 다음 요청 전까지 이미 표시된 데이터를 가지고 있을 수 있습니다.

로컬 Vite 프록시는 활성화된 화면의 GET 요청만 고정 loopback 주소로 전달합니다. `dev`에서는 개발 플래그로 화면을 선택하고 `preview`에서는 연결된 세 화면을 모두 사용할 수 있습니다. 업무 API의 쓰기 메서드와 허용 목록에 없는 `/api/` 경로는 403으로 거부합니다. API가 하나 이상 켜져 있을 때만 인증용 GET `/api/v1/auth/csrf`, `/api/v1/auth/me` 및 POST `/api/v1/auth/login`, `/api/v1/auth/logout`을 추가 허용합니다. Client는 redirect를 오류로 처리하고 프록시도 redirect를 따라가지 않습니다. 401/403 자동 재시도·자동 polling은 하지 않습니다.

## Research API와 화면 계약

기존 [Research Read API v1](../backend/docs/RESEARCH_READ_API_V1.md)을 변경하지 않고 사용합니다.

| GET endpoint (`/api/v1/research` 기준) | 화면 / 데이터                                                                            |
| -------------------------------------- | ---------------------------------------------------------------------------------------- |
| `/summary`                             | 서버 user/exchange/quote scope, 후보·Shadow·Snapshot 수, 최근 관측 시각과 응답 생성 시각 |
| `/candidates`                          | 정책명, 등록기간, Shadow 여부, 허용 정렬, page/size에 따른 서버 목록                     |
| `/candidates/{id}`                     | 등록 기준 snapshot/watermark/signature, 원본 weights, 등록 당시 Shadow/Gate 기록         |
| `/snapshots`                           | 관측 시각·ID 내림차순 서버 페이지, 저장 시각과 후보 수                                   |
| `/automation-status`                   | user/exchange/quote/schema/signature/Top N 복합 context의 전체 저장 체크포인트           |

후보 검색 날짜는 KST 입력을 UTC로 변환합니다. 목록 크기·검색·정렬 변경 시 page는 0으로 초기화하며 `totalElements/totalPages`는 서버 값을 사용합니다. Query key는 API 출처, endpoint, 전체 filter 또는 ID를 포함합니다. 사용하지 않는 요청은 AbortSignal로 취소하며 API Query는 persistence 없이 memory에만 존재합니다. 출처를 DEMO로 바꾸면 API Query 캐시를 해제합니다. 새 요청 중에는 이전 결과를 현재 결과로 표시하지 않습니다.

`response.json()`으로 먼저 Number 변환하지 않습니다. `lossless-json`으로 숫자 literal을 보존한 뒤 Java Long ID/count는 범위를 검증한 **문자열**로 유지합니다. URL과 row key도 문자열 ID를 사용합니다. BigDecimal weights는 0–1 범위를 정밀 비교한 원본 숫자 문자열이며 JSONB의 snake_case key도 유지합니다. 방어적 응답 검증으로 숫자 길이 512자, exponent ±308을 초과한 weight는 오류 처리합니다. 페이지 번호·크기·Top N 같은 bounded int만 Number를 사용합니다. UTC 원문 시각은 보존하고 KST는 표시용 변환입니다.

로딩, 0건, 400 입력 오류, 401 인증 필요, 403 접근 거부, 404 후보 없음, 500 서버 오류, 네트워크 실패, 잘못된 JSON/스키마를 구분합니다. 오류 body/credential을 화면이나 console에 출력하지 않습니다. 실패를 DEMO나 0으로 대체하지 않고 null은 미기록으로 표시합니다. 갱신 실패 시 이전 정상 값을 현재 정상 결과처럼 표시하지 않습니다.

현재 Gate, Historical/Forward 성과, 사람 승인, Full LIVE activation, Runtime 정책, Worker 실행 상태는 현재 API에 없습니다. 화면에 **현재 API 미제공**으로 명시하며 과거 `storedGateStatus`를 현재 적격 판정으로 사용하지 않습니다. Automation row 없음은 미관찰이며 장애나 신규 적격 데이터 0건으로 추정하지 않습니다.

## Overview LOCAL API v1

Overview도 기본 DEMO를 유지합니다. 기존 Mock 화면은 `OverviewDemoView`에 보존하고, 별도 read DTO/parser/client/Query와 `OverviewApiView`에서 실제 저장 데이터를 표시합니다. Backend의 [Overview API 계약](../backend/docs/OVERVIEW_READ_API_V1.md)을 그대로 사용하며 Python/Java 금융 계산이나 DB schema를 변경하지 않습니다.

먼저 로컬 Backend와 SELECT 전용 DB 계정을 준비하세요. 기존 Research 권한만 있는 경우 Portfolio/Position/Performance/Bot PnL/Recommendation 테이블 5개의 SELECT 권한도 필요합니다. 준비 절차는 [Backend README](../backend/README.md)를 따릅니다.

Repository root에서 파일 수정 없이 실행할 수 있습니다.

```powershell
cd frontend
$env:VITE_ENABLE_OVERVIEW_API = "true"
try { npm run dev } finally { Remove-Item Env:VITE_ENABLE_OVERVIEW_API }
```

Unix에서는 `cd frontend` 후 `VITE_ENABLE_OVERVIEW_API=true npm run dev`를 사용합니다. Research도 함께 사용하려면 별도로 `VITE_ENABLE_RESEARCH_API=true`를 지정하세요. 두 플래그는 개발 서버에서만 사용하며 기본값은 OFF입니다. 배포용 빌드는 두 API를 모두 사용할 수 있습니다.

1. [http://127.0.0.1:5173/overview](http://127.0.0.1:5173/overview)를 엽니다. 비로그인이면 DEMO가 표시됩니다.
2. 헤더에서 로그인하면 Overview API 화면으로 자동 전환합니다. 이미 유효한 세션이 있으면 바로 사용자 데이터를 조회합니다.
3. JSON 조회를 확인하고 화면에서 다시 시도합니다. 인증은 브라우저가 관리하며 React는 credential을 수집·저장하지 않습니다.

새로고침하면 서버 세션을 다시 확인합니다. API 화면을 벗어나면 요청을 취소하고 사용하지 않는 Query cache를 제거합니다. 다른 화면으로 이동할 때 그 화면의 출처 표시를 확인하세요.

| GET endpoint                       | 화면 및 기준                                                                        |
| ---------------------------------- | ----------------------------------------------------------------------------------- |
| `/api/v1/overview/summary`         | 최신 계좌 평가, 같은 snapshot의 성과, 독립 FIFO 인식 손익 및 각각의 관측·계산 시각  |
| `/api/v1/overview/positions`       | Summary와 같은 scope/snapshot ID/관측 시각을 확인한 자산 목록                       |
| `/api/v1/overview/performance`     | KST 입력을 UTC로 변환한 periodEndAt 기간 조회와 서버 페이지; 저장된 성과 index와 표 |
| `/api/v1/overview/recommendations` | BUY/SELL/HOLD 필터 및 서버 페이지; 원본 추천 status                                 |

Summary가 정상 조회된 뒤 Positions를 조회합니다. Scope가 다르면 즉시 차단하며, snapshot 불일치만 1회 다시 조회합니다. 계속 다르면 자산을 혼합하지 않고 갱신 필요 상태로 표시합니다. 다시 시도는 계좌 요약을 포함해 새로 조회합니다. Performance/Recommendation은 독립적으로 조회하며 자체 scope와 생성 시각을 표시하고, 사용 가능한 Summary scope와 다르면 표시하지 않습니다. 개별 실패가 다른 정상 영역을 DEMO로 바꾸지 않습니다.

금액·수량·ID는 문자열로 보존합니다. 기존 `lossless-json`으로 count도 손실 없이 읽고 응답 schema·enum·소유권·페이지 개수를 검증합니다. 금액/수량 formatter는 문자열과 BigInt를 사용해 원 단위 미만 및 큰 정수를 보존합니다. Percentage는 저장 단위 그대로이고 confidence/tradeRatio만 문자열 자릿수 이동으로 표시용 %로 바꿉니다. 소수점 뒤 의미 없는 0만 생략합니다. 차트 좌표에만 Number 근사값을 사용하며 tooltip과 표는 원본 decimal을 표시합니다.

PARTIAL의 known total을 전체 총액으로 대체하지 않습니다. BASELINE은 기준점으로 표시하고, PARTIAL·새 BASELINE·periodStartAt 연속성을 확인할 수 없는 구간은 선으로 잇지 않습니다. 차트는 현재 조회 페이지의 기록만 사용하며 90개 row를 90일이나 전체 이력으로 표현하지 않습니다. 페이지 간 임의 누적 수익률 계산이나 보간도 없습니다. 긴 표는 내부 스크롤로 조회합니다.

UNPRICED, EXCLUDED, null 원가·가격을 유지하며 계좌 비중·전일 증감은 재계산하지 않습니다. Bot PnL은 계좌 수익률과 별개인 user/exchange 단위 KRW 회계입니다. 추천 status와 거래 체결 상태를 구분합니다. AI Analysis opt-in이 활성화된 경우 실제 추천 상세로 이동하며 DEMO 상세로 대체하지 않습니다. 현재 Runtime 정책/Gate·worker 상태·주문/체결 상세 등은 **현재 API 미제공**으로 표시합니다.

401은 세션 만료로 처리하여 캐시를 비우고 DEMO로 전환합니다. 403·500·잘못된 JSON/schema·네트워크 실패·snapshot 불일치는 조회 오류로 구분하며 오류 원문을 노출하지 않습니다. 이 경우 과거 성공 값을 현재 정상 데이터로 보여주거나 0/DEMO로 대체하지 않습니다. 자동 polling이나 인증 오류 재시도는 없습니다. Vite는 해당 flag가 켜진 경우 정확한 Overview 4개 GET만 고정 loopback Backend로 전달하며 기존 Research 경계를 유지합니다.

Vitest/Mock E2E 외에 별도 폐기 가능한 PostgreSQL 17에 기존 Alembic 및 Backend 테스트 fixture를 적용하고, SELECT 전용 Spring Boot → Vite → Chromium 조회/화면 표시를 검증했습니다. 미인증 401, 정확한 큰 금액/ID, PARTIAL, HOLD 필터와 기존 Research GET도 확인했습니다. 이 검증은 테스트 credential을 사용하는 브라우저 자동화이며 Production 인증/배포 검증을 대신하지 않습니다.

## AI Analysis LOCAL API v1

기본 DEMO를 유지하며 `AnalysisDemoView`와 실제 조회용 DTO/parser/client/Query/view를 분리합니다. [Backend API 계약](../backend/docs/AI_TRADE_ANALYSIS_READ_API_V1.md)의 GET 3개만 사용합니다. 로컬 Backend의 SELECT 전용 계정에는 [최소 컬럼 권한](../backend/README.md)이 필요하며, 권한이나 운영 DB를 Frontend가 변경하지 않습니다.

Repository root에서 파일 수정 없이 실행:

```powershell
cd frontend
$env:VITE_ENABLE_AI_ANALYSIS_API = "true"
try { npm run dev } finally { Remove-Item Env:VITE_ENABLE_AI_ANALYSIS_API }
```

Unix에서는 `cd frontend` 후 `VITE_ENABLE_AI_ANALYSIS_API=true npm run dev`를 사용합니다. 이 플래그는 개발 서버에서만 사용하며 기본값은 OFF입니다. 배포용 빌드와 로컬 미리보기에도 같은 API 연결이 제공됩니다. `/ai-analysis`에서 비로그인은 DEMO, 로그인한 사용자는 API 화면을 표시합니다. 헤더에서 로그인하면 원래 경로와 추천 상세 query로 돌아옵니다. API 401은 캐시를 제거하고 DEMO로 돌아갑니다. 비밀번호는 로그인 제출에만 사용합니다. 업무 API는 GET으로 제한하며 로그인·로그아웃은 CSRF 검증을 거치는 POST를 사용합니다.

- 목록: 추천 생성 시각의 KST 기간, 정확한 market, BUY/SELL/HOLD, 서버 pagination. 최신순 고정이며 승인 상태 필터·전체 행동별 집계는 제공하지 않습니다. 목록 행마다 상세를 호출하지 않습니다.
- 상세 주소는 `?recommendation=<추천 ID>`입니다. DEMO의 `?analysis=<분석 ID>`와 다릅니다. 직접 주소 입력·reload 시 유효한 세션이 있으면 실제 상세를 읽습니다. 비로그인이면 DEMO에 실제 추천 링크 안내를 표시하고 로그인 후 원래 상세로 돌아옵니다. Overview의 실제 추천 상세 링크는 AI Analysis API를 사용할 수 있을 때 표시됩니다.
- 추천과 분석 실행 ID, 최종 판단 사유, 추천 sizing, 당시 후보, 승인 기록, MOCK/LIVE 시도와 체결 요약을 구분합니다. 최종 판단 사유는 애플리케이션 안전 규칙 반영 후 기록이며 원본 AI 응답이 아닙니다.
- Universe의 VERIFIED / NO_REFERENCE / INVALID_REFERENCE를 구분하고, VERIFIED일 때만 후보 페이지를 조회합니다. 상세·후보 응답의 사용자 범위와 Universe 실행/선택 후보 ID를 대조합니다. 불일치하면 해당 영역을 차단하고 다른 분석으로 대체하지 않습니다. 별도 HTTP 요청이 하나의 DB snapshot이라는 보장은 없습니다.
- 금액·수량·ID는 문자열, 큰 count는 lossless JSON/BigInt로 유지합니다. 지표는 원본 단위이며 저장 당시 가격을 현재가로 표현하지 않습니다. 없는 지표는 미기록, 데이터 품질과 파싱 품질은 별도로 표시합니다. 후보 eligibility는 분석 당시 값입니다.
- HOLD는 정상적인 관망 판단, LIVE_UNKNOWN은 미확정, LIVE_EXECUTED_CANCELLED는 **실거래 체결 확인 (미체결 잔량 취소)**로 표시합니다. 미기록 체결을 0으로 채우거나 추천값으로 보충하지 않습니다. 검증된 관련 기록 부재는 실제 주문 부재의 증명이 아닙니다.
- 401 세션 만료는 캐시를 제거하고 DEMO로 전환합니다. 그 외 API 실패는 DEMO로 대체하지 않습니다. 새 요청 중/갱신 실패에는 이전 성공값을 현재 정상값처럼 표시하지 않습니다. 화면·상세·출처 전환 시 요청을 취소하고 미사용 cache를 제거합니다. 자동 polling/자동 오류 retry는 없습니다.

사용자 조작은 조회·필터·페이지 이동·새로고침뿐입니다. 승인/거절/주문/취소/reconciliation 기능, 개별 fills·FIFO·성과 재계산, Runtime 정책·worker 상태, Production 인증·배포는 포함하지 않습니다.

## 개발 검증

모든 명령은 `frontend/`에서 실행합니다.

```bash
npm run typecheck
npm run lint
npm run format:check
npm test
npm run build
```

작업 중 watch mode는 `npm run test:watch`, 의도적인 포맷 수정은 `npm run format`을 사용합니다. Vitest/React Testing Library는 fixture의 금액·참조·상태 일관성과 검색, 필터, 상세보기, 결측 처리 등을 검사합니다. 외부 API나 실제 주문은 테스트에 사용하지 않습니다.

브라우저 E2E는 Playwright를 사용합니다. 처음 실행하는 환경에는 테스트용 Chromium이 필요합니다.

```bash
npx playwright install chromium
npm run test:e2e
```

Windows에서 로컬에 설치된 Microsoft Edge를 사용할 수도 있습니다.

```powershell
$env:PLAYWRIGHT_CHANNEL = "msedge"
npm run test:e2e
Remove-Item Env:PLAYWRIGHT_CHANNEL
```

테스트용 browser 설치는 개발 도구 다운로드이며 거래 API 연결이 아닙니다. E2E는 Research/Overview/AI Analysis flag가 설정된 `127.0.0.1:5173` Vite를 자체 시작하므로 해당 포트를 비워 두세요. 이미 실행 중인 서버는 재사용하지 않습니다. 로컬 개발 서버를 유지하려면 PowerShell에서 `$env:PLAYWRIGHT_PORT = "5184"`를 지정해 별도 포트에서 E2E를 실행할 수 있습니다. Research/Overview/AI Analysis API는 정밀도 경계를 포함한 mock response로 가로채고 외부 origin 요청은 차단합니다. 기본 E2E에는 실제 Backend·관리자 credential·Production DB가 필요하지 않습니다. 기존 DEMO 및 API 검색·페이지·오류·snapshot 불일치·테마·모바일 검증을 함께 실행합니다. `test-results/`와 `playwright-report/`는 Git ignored입니다.

개발 도구의 transitive `minimatch`는 `package.json`의 override로 `11.0.1`에 고정합니다. [GHSA-g3hv-75wg-m249](https://github.com/advisories/GHSA-g3hv-75wg-m249)의 glob pattern amplification 문제를 수정한 버전을 사용하기 위한 설정이며 runtime application dependency를 추가하지 않습니다. 변경 시 lockfile과 실제 ESLint 실행의 호환성을 함께 검증합니다.

`.github/workflows/ci.yml`의 독립 Frontend job은 Ubuntu 24.04/Node 24.15.0에서 `npm ci`, typecheck, lint, format check, Vitest, 빌드와 Playwright를 실행합니다. 생성된 `dist/`를 로컬 미리보기로 열어 로그인·로그아웃 전환과 허용되지 않은 API 요청의 차단도 검증합니다. 인증 및 조회 응답은 브라우저 테스트용 fixture를 사용합니다.

## 디자인과 접근성

`src/styles/index.css`에서 dark/light surface, border, typography, primary/positive/negative/warning tokens를 관리합니다. 숫자는 tabular-nums를 사용하고 차트·테이블·상태 badge를 공통 레이아웃으로 구성합니다. 모바일 표는 영역 안에서 가로 스크롤할 수 있습니다.

본문 건너뛰기, visible focus, 이름이 있는 icon button, Dialog focus 관리, 키보드 탭 이동, 차트의 텍스트 요약, 상태 텍스트와 `prefers-reduced-motion`을 지원합니다. 색상만으로 판단 상태를 전달하지 않습니다.

## 이번 단계에 포함하지 않은 기능

회원가입·JWT, 직접 PostgreSQL 연결, Upbit 실시간 계좌 조회, 거래·승인/거절, 정책 등록/승인/활성화/종료, worker 시작/중지, 알림 해결 API와 Production 배포는 포함하지 않습니다. Python Trading Engine, root Compose와 LIVE 안전장치는 유지합니다. Backend의 인증만 Spring Security 세션으로 전환합니다.

후속 Backend 확장에서는 현재 Gate와 immutable 평가 evidence, Historical/Forward/Shadow 성과, 승인·activation·Runtime 상태를 구분한 read API가 필요합니다. 제공되기 전에는 Mock을 실제 데이터로 승격하지 않습니다. Portfolio / Operations 화면의 API 연결도 별도 범위입니다.

Production 공개 전 TLS reverse proxy, 관리자 인증/세션 설계, 권한·CSRF 정책, 최소 DB 권한, 접근 로그의 민감정보 처리, 보안 header와 origin 경계를 별도로 검토해야 합니다. 세션 인증이 구현되어도 Vite 개발 proxy는 Production 배포용이 아닙니다. 운영 API 모드를 자동으로 켜지 않습니다. `VITE_*`, bundle, URL, localStorage/sessionStorage에 secret을 넣지 마세요.

## 참고한 공식 가이드

- [Tailwind CSS v4 — Vite plugin 설치](https://tailwindcss.com/docs/installation/using-vite)
- [shadcn/ui — Vite 설치와 alias 구성](https://ui.shadcn.com/docs/installation/vite)
- [React Router — Declarative mode 설치](https://reactrouter.com/start/declarative/installation)
