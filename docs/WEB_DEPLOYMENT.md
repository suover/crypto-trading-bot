# 프론트엔드·백엔드 웹 배포

React와 Spring Boot를 같은 HTTPS 주소로 제공하는 별도 배포 구성입니다. 비로그인은 DEMO, 로그인하면 연결된 거래 사용자의 조회 데이터, 로그아웃하면 DEMO로 전환합니다. 실제 데이터가 연결된 화면은 Overview, Research, AI Trade Analysis입니다. Portfolio와 Operations의 미연동 범위는 그대로입니다.

## 실행 구조

```text
브라우저 ── HTTPS 443 ── web (Nginx + React 정적 파일)
                            │ /api/*
                            ▼ 내부 web_private 네트워크
                         backend (Spring Boot)
                            │ SELECT, backend_reader
                            ▼ 기존 DB Docker 네트워크
                         PostgreSQL

기존 거래 엔진: 기존 Compose / Deploy Production으로 독립 운영
```

- [웹 Compose](../docker-compose.web.yml)는 `web`, `backend` 두 서비스만 정의합니다. 기존 Compose 파일과 합쳐 실행하지 않습니다.
- 프로젝트 이름은 `crypto-trading-web`, 배포 디렉터리는 `$HOME/apps/crypto-trading-web`입니다. 엔진 checkout, 컨테이너, 볼륨, scheduler, migration을 변경하지 않습니다.
- 프론트는 기존 `npm run build` 결과를 사용합니다. 운영에서 Vite dev/preview를 실행하지 않습니다.
- Spring은 포트를 호스트에 공개하지 않고, DB 비밀번호 파일 하나만 마운트합니다. Upbit/OpenAI/Telegram 및 계정 등록용 writer 비밀번호는 제공하지 않습니다.
- DB 스키마는 Alembic 소유이며, Spring은 `ddl-auto=validate`와 읽기 전용 연결을 유지합니다. Deploy Web은 계정 생성, 권한 변경, migration을 실행하지 않습니다.
- 메모리 상한은 backend 768 MiB, web 128 MiB입니다. 서버 전체 여유, PostgreSQL과 엔진의 실제 사용량을 별도로 확인해야 합니다. Docker image 로딩 중 CPU/디스크 I/O도 발생합니다.

## 최초 서버 준비

이 절차는 운영자가 대상 서버와 백업을 확인한 후 별도로 수행합니다. 로컬 검증이나 코드 병합으로 실행되지 않습니다.

1. 서버의 CPU/메모리/디스크와 443 포트 사용 여부를 확인합니다. Docker Compose의 `up --wait`, Python 3, `flock`이 필요합니다. GitHub runner와 동일한 Linux amd64 서버를 대상으로 합니다.
2. 도메인의 DNS와 해당 도메인을 포함하는 유효한 TLS 인증서를 준비합니다. 인증서 발급·갱신은 이 workflow가 수행하지 않습니다. HTTP 80 redirect/ACME endpoint는 제공하지 않으므로 DNS challenge 등 별도 발급 절차를 사용합니다.
3. 기존 PostgreSQL의 Docker network 이름과 서비스 alias를 확인합니다. 예를 들어 `crypto-trading-bot_default`의 `postgres`입니다. DB 포트는 외부에 공개하지 않습니다.
4. 기존 DB에 백엔드가 요구하는 스키마가 준비돼 있는지 확인합니다. 현재 인증 테이블은 `auth.accounts`이며 기존 Alembic head는 `c2f84b7a931e`입니다. 부족한 migration은 백업과 별도 승인 후 기존 엔진 운영 절차로 적용합니다.
5. [backend DB 역할·권한](../backend/README.md#db-역할과-권한-설정)에 따라 `backend_reader`와 대시보드 계정을 준비합니다. `backend_reader`는 superuser/소유자/쓰기 role membership 없이 필요한 SELECT만 가져야 합니다. 로컬 `kms`는 운영에 자동 복사되지 않습니다.
6. 서버에서 `172.30.83.0/28`이 다른 Docker/VPN/호스트 네트워크와 겹치지 않는지 확인합니다. Nginx `.2`, backend `.3`은 신뢰 프록시 경계에 사용합니다. 충돌하면 subnet과 두 주소, `internal-proxies`를 함께 검토·검증한 후 변경해야 합니다.
7. 배포 전용 SSH 접속과 디렉터리를 준비합니다. workflow의 별도 SSH 키와 검증된 known_hosts를 사용합니다. Docker 권한은 호스트에 강한 권한을 제공하므로 웹 디렉터리 분리가 OS 권한 격리 자체를 보장하지는 않습니다.

배포 사용자 홈 아래 `apps/crypto-trading-web/config/web.env`를 준비합니다. 이는 저장소 `.env`와 별개이며, 아래 값은 예시입니다. 비밀번호 원문은 넣지 않습니다.

```dotenv
WEB_DB_URL=jdbc:postgresql://postgres:5432/crypto_trading_bot
WEB_DB_USERNAME=backend_reader
WEB_DB_NETWORK=crypto-trading-bot_default
WEB_DB_PASSWORD_FILE=/home/ubuntu/apps/crypto-trading-web/config/backend_password
WEB_TLS_CERTIFICATE_FILE=/home/ubuntu/apps/crypto-trading-web/config/fullchain.pem
WEB_TLS_KEY_FILE=/home/ubuntu/apps/crypto-trading-web/config/privkey.pem
WEB_BIND_ADDRESS=0.0.0.0
WEB_HTTPS_PORT=443
```

`WEB_IMAGE_TAG`는 workflow가 검증한 40자리 SHA를 실행 시 주입합니다. Compose 기본 publish 주소는 로컬 검증을 위한 `127.0.0.1:8443`이며 공개하려면 위처럼 명시합니다. 방화벽과 AWS 보안 규칙도 HTTPS 443에 맞춰 운영자가 설정합니다.

지원하는 JDBC URL은 위의 `jdbc:postgresql://host:port/database` 형태입니다. 배포 검사는 URL에 별도 사용자/비밀번호 override나 query parameter를 넣는 것을 거부합니다.

DB password는 backend UID `10001`, TLS key/fullchain은 Nginx UID `101`에서 읽을 수 있어야 합니다. Linux Compose의 파일 기반 secret은 bind mount이므로 `uid/mode` 선언만으로 호스트 권한이 변경되지 않습니다. 전용 디렉터리는 배포 사용자만 접근하게 하고, 파일 소유권/읽기 권한을 해당 UID에 맞춰 준비합니다. 비밀 파일을 Git에 추가하거나 권한을 무조건 전체 공개하지 않습니다.

## 수동 Deploy Web

[Deploy Web workflow](../.github/workflows/deploy-web.yml)는 push로 자동 실행되지 않습니다. GitHub Environment `web-production`을 만들고 가능하면 required reviewer와 main-only 배포 제한을 적용합니다.

| 설정 | 위치와 의미 |
| --- | --- |
| `WEB_HOST` | Environment secret, 서버 hostname/IP |
| `WEB_USER` | Environment secret, 웹 배포 SSH 사용자 |
| `WEB_SSH_PORT` | Environment secret, 비어 있으면 22 |
| `WEB_SSH_PRIVATE_KEY` | Environment secret, 웹 배포 전용 SSH 키 |
| `WEB_SSH_KNOWN_HOSTS` | Environment secret, 사전에 확인한 서버 host key. 배포 중 자동 수집하지 않음 |
| `WEB_PUBLIC_URL` | Environment variable, `https://example.com` 형태. 경로와 마지막 `/` 없음 |

운영 DB 비밀번호, TLS private key와 로그인 비밀번호는 Actions에 등록하지 않습니다. DB/TLS 파일은 서버에서 관리하고 최종 smoke test는 비로그인 요청만 수행합니다.

Actions → **Deploy Web** → main 선택 → confirmation에 **DEPLOY WEB** → Run workflow 순서입니다. 정확히 그 SHA의 가장 최근 main push CI가 `completed success`여야 합니다. CI가 아직 진행 중이거나 실패했으면 배포하지 않습니다.

실행 순서:

1. main/명시적 확인/정확한 SHA의 CI 통과를 검사합니다.
2. runner에서 React·Spring 이미지를 빌드하고 revision label을 붙입니다. 서버에서 빌드하지 않습니다.
3. main SHA가 그대로인지 재확인하고, 검증된 SSH로 이미지 archive와 웹 Compose·스크립트를 별도 release 디렉터리에 전달합니다.
4. 서버에서 archive SHA256, 실행 lock, Compose, secret 파일, 이미지 revision과 Nginx 인증서/config를 검증합니다. 사전 검증 실패 시 실행 중 웹을 교체하지 않습니다.
5. `crypto-trading-web`의 backend/web만 `up --no-build --wait`로 교체합니다. 엔진 서비스를 중지하거나 git checkout/pull 하지 않습니다.
6. 공인 인증서/hostname을 검증하는 HTTPS 요청으로 화면 200, CSRF 200, 비로그인 `auth/me` 401, `/release.json`의 정확한 SHA를 확인합니다. 인증서 검증을 생략하지 않습니다.

배포는 무중단/자동 rollback을 보장하지 않습니다. 웹 교체 중 짧은 중단이 있을 수 있고, 메모리 세션은 backend 재시작 시 만료되어 다시 로그인해야 합니다. cutover 후 health/공개 URL 검증 실패는 workflow 실패로 남으며 엔진을 중지하거나 자동으로 이전 이미지를 올리지 않습니다. 실제 로그인은 운영자가 브라우저에서 확인합니다.

release 디렉터리와 SHA 이미지가 서버에 남습니다. 디스크 사용량을 확인하고 현재 사용 중인 이미지/파일을 제외해 운영자가 정리합니다. 시스템 전체 `docker prune`이나 기존 거래 볼륨 삭제를 사용하지 않습니다.

## 보안 경계와 인증서 갱신

Nginx는 `/api/*`를 SPA로 대체하지 않으며 401/403/5xx를 유지하고 `Cache-Control: no-store`를 설정합니다. 업무 쓰기 API 차단, CSRF, 세션 회전, 계정 활성 상태와 거래 사용자 연결 검사는 기존 Spring Security가 담당합니다.

Nginx가 외부 forwarding header를 덮어쓰고 직접 연결 주소만 전달합니다. Spring은 내부 주소 `172.30.83.2`에서 온 정보만 신뢰합니다. 기존 애플리케이션 로그인 한도를 유지하며 Nginx에도 IP별 제한을 둡니다. CDN/다른 proxy를 앞에 추가하면 그 proxy IP로 집계되므로 신뢰 경계와 rate limit을 다시 설계·검증해야 합니다.

쿠키는 Secure/HttpOnly/SameSite=Strict입니다. `/api` 로그에 query/body/cookie를 남기지 않습니다. HTTP URL은 제공하지 않으며 HSTS는 HTTPS 접근에 적용됩니다. Compose health의 self-signed 허용 검사는 컨테이너 내부 liveness 용도이고, workflow의 외부 HTTPS 검증은 인증서 검증을 생략하지 않습니다.

TLS 갱신 후 bind mount가 새 파일을 참조하도록 현재 release 디렉터리에서 올바른 `WEB_IMAGE_TAG`와 `--env-file`을 지정해 `web`만 `up -d --no-deps --force-recreate web`으로 재생성하고 인증서 유효기간/접속을 확인합니다. backend/엔진 재시작은 필요하지 않습니다. 인증서 만료 감시·갱신 hook 설정은 최초 서버 준비에 포함합니다.

## 로컬 검증

기존 로컬 DB나 운영 DB 연결정보를 받지 않는 [검증 runner](../scripts/web/test_web_runtime.py)를 저장소 root에서 실행합니다. Docker, OpenSSL, Python, frontend 의존성과 Playwright Chromium이 필요합니다.

```bash
cd frontend
npm ci
npx playwright install chromium
cd ..
python scripts/web/test_web_runtime.py
# 프로젝트 Python을 쓰는 Windows PowerShell:
uv run --project trading-engine python scripts/web/test_web_runtime.py
```

매 실행마다 새 PostgreSQL 17 컨테이너와 fixture 계정을 만들고 기존 Alembic을 적용합니다. 실제 웹 이미지를 빌드하고 임시 TLS 인증서로 loopback HTTPS를 띄운 뒤, Chromium에서 DEMO → 로그인 → 본인 데이터 → 새로고침 → 로그아웃 → DEMO를 확인합니다. CSRF, 쿠키, 세션 회전, 다른 사용자 접근 거부, 쓰기 차단, rate limit과 API 오류도 검사합니다. Python HTTPS 요청은 생성한 인증서를 신뢰 대상으로 지정해 hostname까지 검증합니다.

종료 시 자신이 만든 테스트 Compose·DB·네트워크·임시 파일만 제거합니다. 실제 API/주문/Telegram을 사용하지 않습니다. CI의 **HTTPS Web Runtime and Deployment Guards** job도 동일한 runner와 mock Docker 기반 배포 실패 테스트를 실행합니다.
