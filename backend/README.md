# Research / Overview / AI Trade Analysis Read API Backend

Java 21 / Spring Boot 기반 관리자 전용 Research / Overview / AI Trade Analysis 조회 API입니다. 현재 Python 엔진이 저장한 PostgreSQL 데이터를 읽습니다. 연구 평가, 금융 지표 재계산, 등록, Telegram 승인, LIVE 활성화, 주문은 수행하지 않습니다.

## 역할과 구조

```text
세션 인증 관리자 → Controller → Service → Repository (JPA / QueryDSL)
                                          ↓ SELECT only
                                 PostgreSQL (Python/Alembic 소유)
src/main/java/io/github/suover/cryptotradingbot/
  global/config/       DB 연결, 시장 설정, QueryDSL, UTC Clock
  global/security/     인증 DB 계정, BCrypt, Spring Security 세션 및 거래 사용자 연결
  global/exception/    민감정보 없는 공통 오류
  research/controller/
  research/service/
  research/repository/
  research/entity/     최소 컬럼 @Immutable 매핑, 복합키
  research/dto/        외부 응답, 검색 조건
  overview/            controller/service/repository/entity/dto, 계좌·성과·추천 조회
  tradeanalysis/       추천 상세·당시 후보·승인·주문 시도·체결 요약 조회
src/test/java/         Mockito, MockMvc, PostgreSQL Testcontainers
src/test/python/       기존 Alembic 실행 및 테스트 fixture 준비
```

DB 스키마는 기존 `trading-engine/migrations/`만 관리합니다. Hibernate는 `ddl-auto=validate`로 매핑만 검증하며 Flyway/Liquibase나 별도 Java DDL은 없습니다. Entity를 응답으로 반환하지 않고, Repository에도 save/delete/flush API를 노출하지 않습니다.

## 기술 스택

| 항목 | 버전/구성 |
| --- | --- |
| Java | 21 LTS |
| Spring Boot | 4.1.2 |
| Gradle Wrapper | 8.14.3, 공식 ZIP SHA256 검증 |
| QueryDSL | OpenFeign `io.github.openfeign.querydsl` 7.7, Jakarta JPA |
| PostgreSQL | 17 |
| Web / persistence / security | Boot Web MVC, Data JPA/Hibernate, Security, Validation starter |
| 테스트 | JUnit Jupiter, Mockito, AssertJ, MockMvc, Security Test, Testcontainers |

Boot 라이브러리와 PostgreSQL JDBC/Testcontainers 버전은 Boot dependency management를 따릅니다. QueryDSL의 `jpa` annotation processor가 매 compile마다 Q 클래스를 `build/generated/sources/annotationProcessor/java/main/`에 생성합니다. 이 경로와 모든 빌드 결과는 Git ignored입니다.

## 로컬 PostgreSQL과 DB 계정

브라우저에서 로컬 대시보드를 확인하려면 PostgreSQL과 마이그레이션, DB 권한, 대시보드 계정을 순서대로 준비한 뒤 백엔드와 프론트엔드를 실행합니다.

### PostgreSQL 실행과 마이그레이션

[저장소 Quick Start](../README.md#quick-start)의 1~3단계에 따라 로컬 PostgreSQL 17을 실행하고 `trading-engine/`에서 Alembic head를 적용합니다. Docker Compose를 사용한다면 로컬 override로 호스트 포트 `5432`를 공개해야 IntelliJ나 로컬 Gradle에서 접속할 수 있습니다. 아래 예시는 로컬 `crypto_trading_bot` DB를 기준으로 합니다.

`public`에는 기존 거래 테이블이 있고, `auth.accounts`에는 대시보드 계정이 있습니다. Alembic revision `c2f84b7a931e`는 기존 인증 테이블의 이름을 바꾸면서 계정, 외래 키와 권한을 보존합니다. `auth.accounts.trading_user_id`는 `public.users.id`를 참조합니다.

### DB 역할과 권한 설정

아래 SQL은 DB 관리자가 대상 DB를 확인한 뒤 적용하는 권한 설정 예시입니다. `backend_reader` 역할이 이미 있으면 `CREATE ROLE`을 건너뜁니다. 접속 비밀번호를 준비할 때는 psql에서 `\password backend_reader`로 직접 입력합니다. 백엔드는 이 읽기 전용 역할을 사용합니다.

```sql
CREATE ROLE backend_reader LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT;
GRANT CONNECT ON DATABASE crypto_trading_bot TO backend_reader;
GRANT USAGE ON SCHEMA public TO backend_reader;
GRANT SELECT ON
    public.users,
    public.research_policy_candidates,
    public.strategy_replay_snapshots,
    public.research_candidate_automation_states,
    public.shadow_policy_enrollments,
    public.portfolio_snapshots,
    public.portfolio_position_snapshots,
    public.portfolio_performance_snapshots,
    public.bot_trading_pnl_summaries,
    public.trade_recommendations
TO backend_reader;
```

이미 Research 권한이 설정된 계정에는 DBA가 검토 후 위 목록의 Overview 테이블 5개에 대한 SELECT를 추가합니다. 백엔드의 AI Trade Analysis 매핑을 조회하려면 아래 5개 테이블의 명시된 컬럼 SELECT도 필요합니다. `account_snapshots`, `order_fills`, sequence 권한은 필요하지 않습니다. 운영 권한은 자동 변경하지 않습니다.

```sql
GRANT SELECT (id, user_id, pipeline_run_id, run_type, trading_mode, status, started_at, finished_at)
ON public.analysis_runs TO backend_reader;
GRANT SELECT (id, analysis_run_id, user_id, exchange, market, base_asset, quote_asset,
    rank, score, selection_source, buy_eligible, sell_eligible, quote_trade_value_24h, feature_data, created_at)
ON public.market_universe_candidates TO backend_reader;
GRANT SELECT (id, recommendation_id, user_id, status, expires_at, approved_at, rejected_at, created_at, updated_at)
ON public.approval_requests TO backend_reader;
GRANT SELECT (id, recommendation_id, approval_request_id, order_log_id, user_id, trading_mode,
    attempt_number, status, error_code, attempted_at, next_retry_at)
ON public.order_execution_attempts TO backend_reader;
GRANT SELECT (id, recommendation_id, approval_request_id, user_id, trading_mode, exchange, market,
    side, order_type, amount_krw, quantity, price, status, executed_quantity, executed_funds_krw,
    average_execution_price, paid_fee, remaining_quantity, trades_count, execution_synced_at, created_at, updated_at)
ON public.order_logs TO backend_reader;
```

이는 DB 관리자가 대상 환경과 기존 권한을 검토한 뒤 적용할 예시입니다. 새 테이블의 callback token, Telegram 식별정보, raw 응답·자유형 error message 컬럼은 부여하지 않습니다. 기존 `trade_recommendations`는 전체 SELECT를 유지하지만 API Entity/DTO에서 `ai_response`를 제외합니다. 상세 계약은 [AI Trade Analysis Read API v1](docs/AI_TRADE_ANALYSIS_READ_API_V1.md)을 참고하세요.

계정 등록용 `dashboard_auth_writer`도 준비합니다. 역할이 이미 있으면 `CREATE ROLE`을 건너뜁니다. 새 비밀번호는 psql의 `\password` 프롬프트에 입력합니다.

```sql
CREATE ROLE dashboard_auth_writer NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT;
\password dashboard_auth_writer
ALTER ROLE dashboard_auth_writer LOGIN;
```

두 역할을 준비했으면 DB 관리자가 인증 스키마 권한을 적용합니다. 프로젝트의 로컬 Docker PostgreSQL을 사용하는 경우 저장소의 `backend/` 디렉터리에서 다음 명령을 실행합니다.

```powershell
Get-Content -Raw .\scripts\dashboard_auth_permissions.sql | docker exec -i crypto-trading-postgres psql -X -U trading_user -d crypto_trading_bot -v ON_ERROR_STOP=1
```

`backend_reader`에는 `auth.accounts` 조회 권한이 추가됩니다. `dashboard_auth_writer`에는 계정 등록에 필요한 `username`, `password_hash`, `trading_user_id` 컬럼의 INSERT 권한이 부여됩니다.

공유 DB에서는 위 GRANT만으로 모든 환경의 읽기 전용 권한이 보장되지 않습니다. 다른 role membership 및 PUBLIC에서 상속된 권한, schema/DB 소유권을 확인하세요. PostgreSQL의 기본 PUBLIC TEMP 권한도 고려해야 합니다. 운영 DB에서 PUBLIC의 DB 또는 `public` 스키마 권한을 변경할 때는 다른 애플리케이션에 미치는 영향을 DBA가 검토해야 합니다. Testcontainers DB에서는 PUBLIC의 DB 및 스키마 권한을 제거하고 필요한 CONNECT, USAGE 및 SELECT를 부여합니다. DML, TRUNCATE, 일반/임시 CREATE, ALTER/DROP, 추가 테이블의 비공개 컬럼 및 불필요한 테이블 SELECT 거부를 검증합니다.

권한 확인 예시(읽기 계정으로 연결):

```sql
SELECT current_user;
SELECT has_schema_privilege(current_user, 'public', 'CREATE');
SELECT has_database_privilege(current_user, current_database(), 'TEMP');
SELECT has_table_privilege(current_user, 'research_policy_candidates', 'SELECT');
SELECT has_table_privilege(current_user, 'research_policy_candidates', 'INSERT,UPDATE,DELETE,TRUNCATE');
```

DDL 소유권과 role membership도 `\du`, `\dn+`, `\dp`로 검토하세요. 실제 운영에서 권한 확인을 위해 데이터 변경 SQL을 실행하지 않습니다. Spring `@Transactional(readOnly=true)`, Hikari read-only는 방어 계층이며 DB 권한을 대체하지 않습니다.

## 환경설정과 실행

백엔드 명령은 저장소의 `backend/` 디렉터리에서 실행합니다. JDK 21을 `JAVA_HOME`에 지정하세요. Gradle Wrapper는 저장소에 포함돼 있습니다. Unix에서 Wrapper 실행 권한이 없다면 `bash ./gradlew`를 사용합니다.

### 관리자 계정 등록

대시보드 계정을 연결할 활성 거래 사용자(`public.users.id`)를 먼저 확인합니다. 새 로컬 DB에는 Alembic이 거래 사용자를 생성하지 않습니다. 사용할 거래 사용자가 없으면 Python 설정이 로컬 DB를 가리키는지 확인한 뒤, `trading-engine/`에서 다음 명령을 한 번 실행하고 출력된 `user_id`를 사용합니다.

```bash
uv run python -m scripts.seed_default_user --name "Local Dashboard"
```

저장소의 `backend/` 디렉터리에서 대화식 PowerShell을 열고 다음 명령을 실행합니다. `-TradingUserId 1`은 이미 존재하는 활성 거래 사용자 `1`에 연결하는 예시입니다. 실제 사용자 ID가 다르면 그 ID를 지정합니다.

```powershell
$env:BACKEND_DB_URL = 'jdbc:postgresql://127.0.0.1:5432/crypto_trading_bot'
$env:BACKEND_DB_USERNAME = 'backend_reader'
$env:DASHBOARD_AUTH_WRITER_USERNAME = 'dashboard_auth_writer'
.\scripts\create_dashboard_account.ps1 -TradingUserId 1
```

| 터미널 입력 항목 | 입력할 값 |
| --- | --- |
| `Dashboard username` | 브라우저에서 사용할 대시보드 로그인 아이디 |
| `BACKEND_DB database password` | `backend_reader`의 PostgreSQL 비밀번호. 백엔드의 `BACKEND_DB_PASSWORD`에도 같은 값을 사용합니다. |
| `DASHBOARD_AUTH_WRITER database password` | `dashboard_auth_writer`의 PostgreSQL 비밀번호. 계정 등록 도구에서 입력합니다. |
| `New dashboard password`, `Repeat dashboard password` | 브라우저에서 로그인할 때 쓸 새 비밀번호를 두 번 입력합니다. |

등록 도구는 거래 사용자의 활성 상태를 확인하고 BCrypt 해시와 사용자 연결을 저장합니다. DB는 중복 아이디와 존재하지 않는 거래 사용자 연결을 거부합니다. Unix에서는 위의 환경변수 세 개를 export한 뒤 `java --class-path "$(bash ./gradlew --no-daemon --console=plain -q dashboardAccountClasspath)" tools/CreateDashboardAccount.java 1`을 실행합니다.

### 백엔드 실행

| 환경변수 | 의미 |
| --- | --- |
| `BACKEND_DB_URL` | 필수 JDBC URL. 로컬 예시는 `jdbc:postgresql://127.0.0.1:5432/crypto_trading_bot`입니다. |
| `BACKEND_DB_USERNAME` | 필수 DB 읽기 역할. 위 예시에서는 `backend_reader`입니다. |
| `BACKEND_DB_PASSWORD` | 해당 DB 역할의 비밀번호. 실행 환경에 안전하게 주입합니다. |
| `BACKEND_SESSION_COOKIE_SECURE` | 기본값은 `true`입니다. 로컬 loopback HTTP 개발에서는 `false`로 설정합니다. |
| `BACKEND_EXCHANGE` | 기본값은 `UPBIT`입니다. 대문자 영숫자를 사용합니다. |
| `BACKEND_QUOTE_ASSET` | 기본값은 `KRW`입니다. 대문자 영숫자를 사용합니다. |

파이썬 엔진의 `.env`와 `.secrets/`는 Spring Boot에서 읽지 않습니다. 필수 DB 설정이 없거나 백엔드 역할이 `auth.accounts`를 읽을 수 없으면 시작에 실패합니다. 인증 테이블이 비어 있어도 서버를 시작할 수 있습니다.

실행 환경에 위 값을 설정한 뒤 다음 명령을 사용합니다.

```powershell
.\gradlew.bat bootRun
```

Unix에서는 `bash ./gradlew bootRun`을 실행합니다. IntelliJ를 사용하는 경우 `CryptoTradingBotApplication`의 실행 설정에 같은 환경변수를 입력하고 실행합니다. 기본 주소는 `127.0.0.1:8080`입니다.

기존 설정에서 전환할 때는 `DASHBOARD_AUTH_DB_*`, `BACKEND_TRADING_USER_ID`, `BACKEND_ADMIN_USERNAME`, `BACKEND_ADMIN_PASSWORD_HASH`를 실행 설정에서 제거할 수 있습니다. 기존 HTTP Basic 클라이언트는 아래 세션 인증 절차로 전환합니다.

별도 인증 DB에 저장한 계정이 있다면 기존 비밀번호로 재등록하거나, 거래 사용자 연결과 BCrypt 해시를 확인해 이전합니다.

### 프론트엔드 실행과 로그인

확인할 화면의 API 플래그를 켜고 프론트엔드 개발 서버를 시작합니다. 화면별 설정은 [Research](../frontend/README.md#local-api-실행과-인증), [Overview](../frontend/README.md#overview-local-api-v1), [AI Analysis](../frontend/README.md#ai-analysis-local-api-v1)를 참고하세요. [로컬 로그인 화면](http://127.0.0.1:5173/login)에서 등록한 아이디와 계정 등록 때 정한 대시보드 비밀번호로 로그인합니다. 해당 화면에서 **LOCAL API · READ ONLY**를 선택하면 연결된 거래 사용자의 로컬 DB 데이터를 조회할 수 있습니다.

보호된 조회마다 활성 계정과 로그인 당시의 거래 사용자 연결을 확인합니다. 계정이나 거래 사용자가 비활성이거나 연결이 바뀌면 조회가 거부됩니다. DB 장애는 HTTP `500`으로 반환됩니다.

### 세션과 인증 API

| Method / URL | 의미 |
| --- | --- |
| `GET /api/v1/auth/csrf` | 익명 접근 가능. 세션에 연결된 masked CSRF token/headerName 반환 |
| `POST /api/v1/auth/login` | URL-encoded `username`, `password` + `X-CSRF-TOKEN`. 성공 `204`, 자격 증명 오류 `401`, CSRF 검증 실패 `403`, 시도 한도 초과 `429` |
| `GET /api/v1/auth/me` | 유효한 ADMIN 세션의 `{username}` 반환. 미인증 `401`, 계정·거래 사용자 상태 또는 연결 검증 실패 `403` |
| `POST /api/v1/auth/logout` | `X-CSRF-TOKEN` 필요. 서버 세션 무효화·쿠키 삭제 성공 `204`, CSRF 검증 실패 `403` |

Spring Security 기본 로그인 필터가 인증·세션 ID 교체·SecurityContext 저장을 담당합니다. 로그인/로그아웃 이후 CSRF token을 다시 조회해야 합니다. 리다이렉트나 브라우저 Basic challenge 대신 JSON 오류를 반환합니다. 업무 API의 쓰기 메서드와 미정의 경로는 유효한 ADMIN 세션과 CSRF token이 있어도 거부합니다.

세션은 서버 메모리에 보관하며 유휴 30분, 로그인 후 최대 8시간입니다. 서버 재시작/배포 시 다시 로그인해야 합니다. 쿠키는 `HttpOnly`, `SameSite=Strict`, 기본 `Secure`이며 URL 세션 ID는 사용하지 않습니다. 로그인은 연결 원격 주소별 1분 10회로 제한하며 429/Retry-After를 반환합니다. 클라이언트가 보낸 `X-Forwarded-For`는 신뢰하지 않습니다. 이 제한은 단일 인스턴스용이고 reverse proxy 뒤에서는 여러 사용자가 한 주소로 집계될 수 있습니다.

운영 공개는 별도 승인/배포 단계입니다. HTTPS 동일 출처 reverse proxy, 직접 backend 접근 차단, 신뢰할 proxy/forwarded-header 정책, proxy의 로그인 시도 제한과 민감정보 없는 로그를 검토해야 합니다. 다중 인스턴스에서는 공유 세션 저장소가 필요하며 대시보드는 DB에 세션을 쓰지 않습니다. CORS나 JWT/localStorage 인증은 도입하지 않습니다.

## API

업무 데이터 API는 관리자 세션 인증이 필요한 GET만 제공합니다.

| URL | 역할 |
| --- | --- |
| `/api/v1/research/summary` | scoped count, 최신 snapshot |
| `/api/v1/research/candidates` | 정책명/등록기간/Shadow 여부 검색, 페이지 |
| `/api/v1/research/candidates/{id}` | 후보·등록 당시 기준·과거 Gate 상태 |
| `/api/v1/research/snapshots` | capturedAt/ID 정렬 페이지 |
| `/api/v1/research/automation-status` | 복합키별 저장 체크포인트 전체 |
| `/api/v1/overview/summary` | 최신 계좌 평가, 같은 snapshot의 성과, 독립 Bot PnL |
| `/api/v1/overview/positions` | 최신 계좌 snapshot에 속한 보유 자산 |
| `/api/v1/overview/performance` | 저장된 Modified Dietz 성과 이력 |
| `/api/v1/overview/recommendations` | scoped BUY/SELL/HOLD 추천 이력 |
| `/api/v1/ai-trade-analysis/recommendations` | AI 추천 이력 검색과 페이지 조회 |
| `/api/v1/ai-trade-analysis/recommendations/{id}` | 추천 상세, 분석, 승인·주문 시도·체결 요약 |
| `/api/v1/ai-trade-analysis/recommendations/{id}/candidates` | 추천에 연결된 당시 후보 목록 |

로그인한 브라우저의 frontend origin에서 위 GET API를 조회할 수 있습니다.

파라미터·응답·오류의 전체 계약은 [Research Read API v1](docs/RESEARCH_READ_API_V1.md), [Overview Read API v1](docs/OVERVIEW_READ_API_V1.md), [AI Trade Analysis Read API v1](docs/AI_TRADE_ANALYSIS_READ_API_V1.md)을 참고하세요. Overview는 nullable 지표와 PARTIAL/BASELINE을 보존하며 성과를 재계산하지 않습니다. Bot PnL은 계좌 수익률이 아닌 user/exchange 단위 FIFO 인식 실현손익입니다.

## 검증과 CI

테스트만 실행할 때는 Testcontainers가 전용 DB와 역할을 준비합니다. Docker daemon, uv 및 Python 3.14가 필요합니다. 먼저 `trading-engine/`에서 `uv sync --dev --locked`를 실행하세요.

```bash
./gradlew clean test
./gradlew clean build
# DB 없는 단위/API 테스트만 선택할 경우 (전체 검증을 대체하지 않음):
./gradlew test --tests '*ResearchReadServiceTest' --tests '*ResearchControllerTest'
```

Testcontainers가 PostgreSQL 17을 새로 만들고 uv subprocess가 실제 Alembic head로 올립니다. 테스트 fixture와 SELECT 계정을 준비한 뒤 Spring Boot가 해당 계정으로 validate/QueryDSL 조회를 수행합니다. migration은 전용 테스트 URL만 받으며 `.env`와 secret resolver를 사용하지 않습니다. Docker가 없거나 migration이 실패하면 테스트도 실패합니다.

Windows의 애플리케이션 제어 정책이 psycopg DLL을 차단한다면 코드/테스트를 끄지 말고 다음 테스트 전용 경로를 사용할 수 있습니다.

```powershell
.\gradlew.bat clean test -PmigrationRunner=docker
.\gradlew.bat clean build -PmigrationRunner=docker
```

이 옵션도 Java Testcontainers를 호스트에서 실행합니다. Alembic/fixture 준비만 Python 3.14 uv 컨테이너에서 수행하며 source directory 두 개를 읽기 전용으로 마운트합니다. Docker socket, `.env`, `.secrets/`를 이 컨테이너에 제공하지 않습니다. 별도의 운영 DB URL을 입력받지 않습니다. 기본 CI 경로는 uv입니다.

JUnit XML/HTML은 `build/test-results/test/`, `build/reports/tests/test/`에 생성됩니다. Alembic 준비 로그는 `build/alembic-test.log`이며 테스트 전용 연결정보만 사용합니다.

기존 Python/Ruff/Alembic/Docker CI는 그대로 두고 Ubuntu 24.04의 Java Job을 추가했습니다. Java 21, Gradle cache/Wrapper 검증, Python/uv locked 의존성을 준비한 뒤 `clean build`가 Q 생성과 모든 테스트를 실행합니다.

## 현재 경계와 다음 단계

현재 연구 상태의 read model이며 Python의 서명 검증·Gate 재평가를 Java로 복제하지 않습니다. 저장된 Gate 값은 당시 상태이고, 자동화 상태도 저장 checkpoint 그대로입니다. 신규 snapshot 21개 조건을 Java에서 재계산하지 않습니다.

React Overview는 기본 DEMO이며 개발 환경의 명시적 opt-in으로 별도 Overview read DTO/client를 사용할 수 있습니다. [Frontend 실행 절차](../frontend/README.md#overview-local-api-v1)를 참고하세요. 현재 화면의 evidence/approval/activation 전체 DTO를 이 API가 모두 제공한다고 가정하지 마세요. Overview는 Long ID와 BigDecimal을 JSON 문자열로 반환합니다. 기존 Research API의 숫자 계약과 lossless parsing은 변경하지 않습니다. 관리자 비밀번호를 frontend bundle에 저장하면 안 됩니다.
