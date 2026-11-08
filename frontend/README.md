# Crypto Trading Bot Dashboard

Python trading/research engine의 주요 개념을 살펴볼 수 있는 React 기반 **조회 전용 Mock 대시보드**입니다. 계좌 가치, AI 판단, 체결, 연구 정책과 운영 상태를 다섯 화면으로 제공합니다.

모든 금액·상태·시각은 합성 데모 데이터입니다. 실제 계좌나 운영 서버를 조회하지 않으며 Spring Boot, PostgreSQL, Upbit, OpenAI, Telegram과 연결하지 않습니다. 로컬 실행에 `.env`나 API key는 필요하지 않습니다.

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

Production 형식의 정적 산출물을 로컬에서 확인하려면:

```bash
npm run build
npm run preview
```

`dist/`가 생성되며 preview도 localhost에만 바인딩합니다. 이 명령은 Production 배포가 아닙니다. 현재 root Docker Compose와 GitHub Actions는 frontend를 빌드하거나 배포하지 않습니다.

## 다섯 화면

| 경로           | 화면          | 주요 내용                                                                                             |
| -------------- | ------------- | ----------------------------------------------------------------------------------------------------- |
| `/overview`    | 대시보드      | 총자산·계좌 성과·bot PnL, 기간별 자산 차트, 보유 자산, 최근 추천, 연구·운영 요약                      |
| `/ai-analysis` | AI 매매 분석  | 마켓·액션·날짜·승인 상태 검색/필터, 정렬/페이지네이션, Multi-Timeframe 지표와 추천/실행 상세          |
| `/portfolio`   | 자산 및 거래  | 자산 추이·구성, 보유 자산 검색/정렬, 체결 내역, FIFO 매칭과 Bot Trading PnL                           |
| `/research`    | 전략 연구     | Runtime 정책, 11단계 lifecycle, 후보 목록, 가중치 비교, Historical/Forward/Shadow evidence, Gate 상세 |
| `/operations`  | 운영 모니터링 | 모의 worker 상태, 분석 일정, Research Lifecycle 처리 현황, 이벤트와 경고                              |

`/`는 `/overview`로 이동합니다. 없는 경로에는 404 화면을 제공합니다. 현재 메뉴, 데이터 기준 시각과 **DEMO** 표시를 공통 레이아웃에서 유지합니다.

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
│   ├── types/              # backend와 합의할 조회 DTO
│   ├── data/mock/          # 결정적인 공통 fixture와 연구 데이터
│   ├── services/           # DashboardRepository와 Mock 구현
│   ├── hooks/              # Query hook, repository/scenario provider
│   ├── lib/                # KST/KRW/비율 formatter, class helper
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

향후 Spring Boot REST API를 연결할 때는 `DashboardRepository`의 읽기 전용 계약을 구현한 adapter를 `DemoScenarioProvider`에 주입합니다. API endpoint, 인증과 DTO validation은 그 단계에서 정의합니다. 현재는 HTTP adapter, URL, 토큰 또는 backend 서버를 만들지 않았습니다. API credential을 `VITE_*` 변수나 frontend bundle에 넣지 않습니다.

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

테스트용 browser 설치는 개발 도구 다운로드이며 거래 API 연결이 아닙니다. E2E 설정은 `127.0.0.1:5173`에 Vite를 실행하며 로컬에서는 이미 실행 중인 서버를 재사용할 수 있습니다. 외부 origin 요청은 테스트에서 차단합니다. 라우팅·상호작용과 375/768/1024/1440px 레이아웃을 확인하고 `test-results/`와 `playwright-report/`는 Git ignored입니다.

개발 도구의 transitive `minimatch`는 `package.json`의 override로 `11.0.1`에 고정합니다. [GHSA-g3hv-75wg-m249](https://github.com/advisories/GHSA-g3hv-75wg-m249)의 glob pattern amplification 문제를 수정한 버전을 사용하기 위한 설정이며 runtime application dependency를 추가하지 않습니다. 변경 시 lockfile과 실제 ESLint 실행의 호환성을 함께 검증합니다.

현재 기존 CI는 Python engine 검증을 수행합니다. 이 단계에서는 CI/CD나 Compose를 변경하지 않았으므로 frontend 검증은 위 명령으로 별도 실행합니다.

## 디자인과 접근성

`src/styles/index.css`에서 dark/light surface, border, typography, primary/positive/negative/warning tokens를 관리합니다. 숫자는 tabular-nums를 사용하고 차트·테이블·상태 badge를 공통 레이아웃으로 구성합니다. 모바일 표는 영역 안에서 가로 스크롤할 수 있습니다.

본문 건너뛰기, visible focus, 이름이 있는 icon button, Dialog focus 관리, 키보드 탭 이동, 차트의 텍스트 요약, 상태 텍스트와 `prefers-reduced-motion`을 지원합니다. 색상만으로 판단 상태를 전달하지 않습니다.

## 이번 단계에 포함하지 않은 기능

실제 로그인·JWT, REST 서버, PostgreSQL 연결, 실계좌 조회, 거래·승인/거절, 정책 등록/승인/활성화/종료, worker 시작/중지, 알림 해결 API와 Production 배포는 포함하지 않습니다. Python Trading Engine, Spring Boot placeholder, root Compose와 기존 LIVE 안전장치는 변경하지 않습니다.

## 참고한 공식 가이드

- [Tailwind CSS v4 — Vite plugin 설치](https://tailwindcss.com/docs/installation/using-vite)
- [shadcn/ui — Vite 설치와 alias 구성](https://ui.shadcn.com/docs/installation/vite)
- [React Router — Declarative mode 설치](https://reactrouter.com/start/declarative/installation)
