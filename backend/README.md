# Research Read API Backend

Java 21 / Spring Boot 기반 관리자 전용 Research 조회 API입니다. 현재 Python 엔진이 저장한 PostgreSQL 데이터를 읽습니다. 연구 평가, 등록, Telegram 승인, LIVE 활성화, 주문은 수행하지 않습니다.

## 역할과 구조

```text
HTTP Basic 관리자 → Controller → Service → Repository (JPA / QueryDSL)
                                          ↓ SELECT only
                                 PostgreSQL (Python/Alembic 소유)
src/main/java/io/github/suover/cryptotradingbot/
  global/config/       서버 범위, QueryDSL, UTC Clock
  global/security/     단일 관리자 BCrypt / stateless HTTP Basic
  global/exception/    민감정보 없는 공통 오류
  research/controller/
  research/service/
  research/repository/
  research/entity/     최소 컬럼 @Immutable 매핑, 복합키
  research/dto/        외부 응답, 검색 조건
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

처음 검증할 때는 아래 Testcontainers 테스트를 권장합니다. 별도 DB 계정을 만들거나 기존 DB를 선택할 필요가 없으며 자동 생성한 DB에서만 migration을 실행합니다. 일반 개발용 DB를 준비하는 경우에도 Production DB와 분리된 PostgreSQL 17에 Python locked 환경으로 기존 `alembic upgrade head`를 적용한 후 사용하세요.

운영 계정은 Python 쓰기 계정과 공유하지 않습니다. 아래는 DB 관리자가 대상 DB를 확인하고 별도로 승인한 후 적용할 **예시**입니다. 애플리케이션은 이 SQL을 실행하지 않습니다. 실제 비밀번호를 파일이나 shell history에 넣지 말고 psql의 `\password backend_reader` 같은 보안 입력을 사용하세요.

```sql
CREATE ROLE backend_reader LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT;
GRANT CONNECT ON DATABASE crypto_trading_bot TO backend_reader;
GRANT USAGE ON SCHEMA public TO backend_reader;
GRANT SELECT ON
    public.users,
    public.research_policy_candidates,
    public.strategy_replay_snapshots,
    public.research_candidate_automation_states,
    public.shadow_policy_enrollments
TO backend_reader;
```

공유 DB에서는 위 GRANT만으로 모든 환경의 읽기 전용 권한이 보장되지 않습니다. 다른 role membership 및 PUBLIC에서 상속된 권한, schema/DB 소유권을 확인하세요. PostgreSQL의 기본 PUBLIC TEMP 권한도 고려해야 합니다. 운영 DB의 PUBLIC 권한 변경은 다른 애플리케이션에 영향을 주므로 DBA가 별도로 검토해야 하며 이 작업에서 자동으로 REVOKE하지 않습니다. Testcontainers DB에서는 PUBLIC schema/DB 권한을 제거하고 CONNECT/USAGE 및 위 5개 테이블 SELECT만 부여한 뒤 DML, TRUNCATE, 일반/임시 CREATE, ALTER, DROP, 주문 테이블 SELECT가 모두 거부되는지 테스트합니다.

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

모든 명령은 repository root에서 `cd backend`한 뒤 실행합니다. JDK 21을 `JAVA_HOME`에 지정하세요. Gradle 전역 설치는 필요하지 않습니다.

Unix checkout에서 Wrapper 실행권한이 없다면 `./gradlew` 대신 `bash ./gradlew`를 사용합니다. CI도 이 방식으로 실행하므로 Windows에서 생성한 파일 권한에 의존하지 않습니다.

| 환경변수 | 의미 |
| --- | --- |
| `BACKEND_DB_URL` | 필수 JDBC PostgreSQL URL, 예: `jdbc:postgresql://127.0.0.1:5432/local_research` |
| `BACKEND_DB_USERNAME` | 필수 SELECT 전용 사용자 |
| `BACKEND_DB_PASSWORD` | 해당 계정 비밀번호, 안전하게 환경 주입 |
| `BACKEND_TRADING_USER_ID` | 필수 양의 정수, 기존 active users.id |
| `BACKEND_ADMIN_USERNAME` | 필수 단일 관리자 이름 |
| `BACKEND_ADMIN_PASSWORD_HASH` | BCrypt 해시, cost 10–16, 실제 plaintext 금지 |
| `BACKEND_EXCHANGE` | 기본 `UPBIT`, 대문자 영숫자 |
| `BACKEND_QUOTE_ASSET` | 기본 `KRW`, 대문자 영숫자 |

Python의 `.env`/`.secrets/`를 읽거나 공유하지 않습니다. 필수 설정 누락/잘못된 사용자 범위/잘못된 BCrypt 형식은 시작 실패입니다. 시작 후 해당 Trading User가 없거나 비활성이면 조회도 403으로 거부합니다. DB 장애는 500이며 0건으로 변환하지 않습니다.

```bash
# 환경변수는 운영체제의 안전한 설정 수단으로 준비한 뒤:
./gradlew bootRun
# Windows PowerShell:
.\gradlew.bat bootRun
```

기본 주소는 `127.0.0.1:8080`입니다. HTTP Basic은 localhost 개발용이며 공개 인터넷에 HTTP로 노출하지 마세요. TLS, reverse proxy, 인증정보 주입, 최소 DB 권한과 접근 통제를 별도로 검토하기 전 Production 배포하지 않습니다. Production Compose나 배포 스크립트에 이 서비스를 추가하지 않았습니다.

인증 세션/cookie를 사용하지 않으며 모든 쓰기 메서드 및 미정의 endpoint를 거부하므로 CSRF를 명시적으로 끕니다. CORS를 열거나 React에 인증정보를 내장하지 않습니다.

## API

관리자 인증이 필요한 GET만 제공합니다.

| URL | 역할 |
| --- | --- |
| `/api/v1/research/summary` | scoped count, 최신 snapshot |
| `/api/v1/research/candidates` | 정책명/등록기간/Shadow 여부 검색, 페이지 |
| `/api/v1/research/candidates/{id}` | 후보·등록 당시 기준·과거 Gate 상태 |
| `/api/v1/research/snapshots` | capturedAt/ID 정렬 페이지 |
| `/api/v1/research/automation-status` | 복합키별 저장 체크포인트 전체 |

```bash
# -u에는 사용자 이름만 전달하면 curl이 비밀번호를 대화식으로 요청합니다.
curl -u "$BACKEND_ADMIN_USERNAME" "http://127.0.0.1:8080/api/v1/research/summary"
curl -u "$BACKEND_ADMIN_USERNAME" "http://127.0.0.1:8080/api/v1/research/candidates?keyword=momentum&shadowEnrolled=true&page=0&size=20"
```

파라미터/응답/오류의 전체 계약은 [RESEARCH_READ_API_V1.md](docs/RESEARCH_READ_API_V1.md)를 참고하세요.

## 검증과 CI

Docker daemon, uv 및 Python 3.14가 필요합니다. 먼저 `trading-engine/`에서 `uv sync --dev --locked`를 실행하세요.

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

다음 React 연동에서는 [기존 Mock DashboardRepository](../frontend/src/services/dashboard-repository.ts)에 실제 API adapter를 별도로 추가해야 합니다. 현재 화면의 evidence/approval/activation 전체 DTO를 이 API가 모두 제공한다고 가정하지 마세요. BigDecimal JSON 숫자는 프론트에서 정밀도를 보존하는 파서/표현을 선택하고, ID는 Java Long 범위이므로 JS safe integer 경계를 검토해야 합니다. 관리자 비밀번호를 frontend bundle에 저장하면 안 됩니다.
