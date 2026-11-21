"""Test actual HTTPS web containers against a NEW disposable PostgreSQL database.

Never accepts an existing DB URL. Requires Docker, OpenSSL and frontend Playwright.
"""

import argparse
import json
import os
from pathlib import Path
import shutil
import socket
import ssl
import subprocess
import sys
import tempfile
import time
import urllib.request
import uuid

ROOT = Path(__file__).resolve().parents[2]


def run(*args, env=None, input=None, check=True, cwd=ROOT):
    result = subprocess.run(
        args,
        cwd=cwd,
        env=env,
        input=input,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
    )
    if check and result.returncode:
        raise RuntimeError(f"Failed: {args[:3]}\n{result.stdout}\n{result.stderr}")
    return result.stdout.strip()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skip-build", action="store_true")
    args = parser.parse_args()
    name = "web-test-" + uuid.uuid4().hex[:10]
    (ROOT / "build").mkdir(exist_ok=True)
    temp = Path(tempfile.mkdtemp(prefix=name, dir=ROOT / "build"))
    env = {k: v for k, v in os.environ.items() if not k.startswith("WEB_")}
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    compose = [
        "docker",
        "compose",
        "--env-file",
        str(temp / "web-config"),
        "-p",
        name,
        "-f",
        str(ROOT / "docker-compose.web.yml"),
    ]
    created_network = created_db = False
    clients = []
    try:
        if not args.skip_build:
            for app in ["backend", "frontend"]:
                print(f"Building {app}", flush=True)
                run(
                    "docker",
                    "build",
                    "-t",
                    f"crypto-trading-web-{app}:local-web-test",
                    app,
                )
        openssl = shutil.which("openssl")
        if not openssl and os.name == "nt":
            openssl = "C:/Program Files/Git/usr/bin/openssl.exe"
        if not openssl:
            raise RuntimeError(
                "OpenSSL is required for the disposable test certificate"
            )
        run(
            openssl,
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-days",
            "1",
            "-keyout",
            str(temp / "tls-key"),
            "-out",
            str(temp / "tls-cert"),
            "-subj",
            "/CN=localhost",
            "-addext",
            "subjectAltName=DNS:localhost,IP:127.0.0.1",
        )
        (temp / "reader-password").write_text("reader-fixture-only", encoding="utf-8")
        for file in ["tls-key", "tls-cert", "reader-password"]:
            (temp / file).chmod(0o444)
        values = {
            "WEB_IMAGE_TAG": "local-web-test",
            "WEB_DB_NETWORK": name,
            "WEB_DB_URL": "jdbc:postgresql://fixture-db:5432/backend_fixture",
            "WEB_DB_USERNAME": "backend_reader",
            "WEB_HTTPS_PORT": str(port),
            "WEB_BIND_ADDRESS": "127.0.0.1",
            "WEB_DB_PASSWORD_FILE": (temp / "reader-password").as_posix(),
            "WEB_TLS_CERTIFICATE_FILE": (temp / "tls-cert").as_posix(),
            "WEB_TLS_KEY_FILE": (temp / "tls-key").as_posix(),
        }
        (temp / "web-config").write_text(
            "\n".join(f"{k}={v}" for k, v in values.items()), encoding="utf-8"
        )
        run("docker", "network", "create", name)
        created_network = True
        run(
            "docker",
            "run",
            "-d",
            "--name",
            name,
            "--network",
            name,
            "--network-alias",
            "fixture-db",
            "--tmpfs",
            "/var/lib/postgresql/data",
            "-e",
            "POSTGRES_DB=backend_fixture",
            "-e",
            "POSTGRES_USER=fixture_owner",
            "-e",
            "POSTGRES_PASSWORD=fixture-only",
            "postgres:17",
        )
        created_db = True
        for _ in range(60):
            result = subprocess.run(
                ["docker", "exec", name, "pg_isready", "-U", "fixture_owner"],
                capture_output=True,
            )
            if result.returncode == 0:
                break
            time.sleep(1)
        else:
            raise RuntimeError("Test PostgreSQL did not start")
        print("Applying Alembic and backend fixtures to NEW test DB", flush=True)
        run(
            "docker",
            "run",
            "--rm",
            "--network",
            name,
            "--mount",
            f"type=bind,source={ROOT / 'trading-engine'},target=/workspace/trading-engine,readonly",
            "--mount",
            f"type=bind,source={ROOT / 'backend/src/test/python'},target=/workspace/backend/src/test/python,readonly",
            "--mount",
            f"type=bind,source={ROOT / 'backend/scripts/portfolio_fill_permissions.sql'},target=/workspace/backend/scripts/portfolio_fill_permissions.sql,readonly",
            "-e",
            "BACKEND_TEST_MIGRATION_URL=postgresql+psycopg://fixture_owner:fixture-only@fixture-db:5432/backend_fixture",
            "-e",
            "UV_PROJECT_ENVIRONMENT=/tmp/fixture-venv",
            "-e",
            "PYTHONPATH=/workspace/trading-engine",
            "-e",
            "PYTHONDONTWRITEBYTECODE=1",
            "ghcr.io/astral-sh/uv:python3.14-bookworm-slim",
            "uv",
            "run",
            "--locked",
            "--project",
            "/workspace/trading-engine",
            "python",
            "/workspace/backend/src/test/python/prepare_database.py",
        )
        sql = """
CREATE EXTENSION pgcrypto;
GRANT USAGE ON SCHEMA auth TO backend_reader;
GRANT SELECT ON auth.accounts TO backend_reader;
INSERT INTO auth.accounts(username,password_hash,trading_user_id) VALUES
('fixture-admin',crypt('admin-fixture-only',gen_salt('bf',10)),1),
('fixture-other',crypt('admin-fixture-only',gen_salt('bf',10)),20);
DROP EXTENSION pgcrypto;
"""
        run(
            "docker",
            "exec",
            "-i",
            name,
            "psql",
            "-X",
            "-U",
            "fixture_owner",
            "-d",
            "backend_fixture",
            "-v",
            "ON_ERROR_STOP=1",
            input=sql,
        )
        config = json.loads(run(*compose, "config", "--format", "json", env=env))
        assert set(config["services"]) == {"web", "backend"}
        assert not config["services"]["backend"].get("ports")
        assert len(config["services"]["backend"]["secrets"]) == 1
        # Same network-less TLS/config preflight as deploy_web.sh; works on first deploy.
        run(
            "docker",
            "run",
            "--rm",
            "--network",
            "none",
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges:true",
            "--tmpfs",
            "/tmp:size=32m,mode=1777",
            "--mount",
            f"type=bind,src={temp / 'tls-cert'},dst=/run/secrets/tls_certificate,readonly",
            "--mount",
            f"type=bind,src={temp / 'tls-key'},dst=/run/secrets/tls_key,readonly",
            "crypto-trading-web-frontend:local-web-test",
            "-t",
        )
        print("Starting actual web Compose with loopback HTTPS", flush=True)
        run(
            *compose,
            "up",
            "-d",
            "--no-build",
            "--wait",
            "--wait-timeout",
            "180",
            env=env,
        )
        context = ssl.create_default_context(cafile=str(temp / "tls-cert"))
        with urllib.request.urlopen(
            f"https://127.0.0.1:{port}/healthz", context=context
        ) as response:
            assert response.status == 200
        browser_env = env | {"WEB_TEST_URL": f"https://127.0.0.1:{port}"}
        print("Testing browser and HTTP security through HTTPS", flush=True)
        print(
            run(
                "node",
                "node_modules/@playwright/test/cli.js",
                "test",
                "--config",
                "playwright.web.config.ts",
                env=browser_env,
                cwd=ROOT / "frontend",
            ),
            flush=True,
        )
        denied = run(
            "docker",
            "exec",
            "-e",
            "PGPASSWORD=reader-fixture-only",
            name,
            "psql",
            "-X",
            "-h",
            "127.0.0.1",
            "-U",
            "backend_reader",
            "-d",
            "backend_fixture",
            "-Atc",
            "SELECT has_table_privilege(current_user, 'auth.accounts', 'INSERT,UPDATE,DELETE'), "
            "has_table_privilege(current_user, 'public.order_logs', 'INSERT,UPDATE,DELETE'), "
            "has_table_privilege(current_user, 'public.order_fills', 'INSERT,UPDATE,DELETE'), "
            "has_column_privilege(current_user, 'public.order_fills', 'raw_data', 'SELECT');",
        )
        assert denied == "f|f|f|f", denied
        # Two actual TCP clients prove the backend sees distinct proxy-validated IPs.
        # Forged headers cannot reset the first client's budget or throttle the second.
        for suffix, attempts in [("a", 11), ("b", 1)]:
            client = name + "-" + suffix
            run(
                "docker",
                "run",
                "-d",
                "--name",
                client,
                "--network",
                name + "_web_ingress",
                "--entrypoint",
                "sleep",
                "crypto-trading-web-backend:local-web-test",
                "120",
            )
            clients.append(client)
            curl = [
                "docker",
                "exec",
                client,
                "curl",
                "-ksS",
                "-b",
                "/tmp/cookies",
                "-c",
                "/tmp/cookies",
            ]
            csrf = json.loads(run(*curl, "https://web:8443/api/v1/auth/csrf"))
            for i in range(attempts):
                status = run(
                    *curl,
                    "-o",
                    "/dev/null",
                    "-w",
                    "%{http_code}",
                    "-H",
                    csrf["headerName"] + ": " + csrf["token"],
                    "-H",
                    f"X-Forwarded-For: 198.51.100.{i}",
                    "-d",
                    "username=missing&password=fixture-only",
                    "https://web:8443/api/v1/auth/login",
                )
                assert status == ("429" if i == 10 else "401"), (suffix, i, status)
        print(
            "PASS: distinct proxy client IPs and forged-header throttling", flush=True
        )
        run(*compose, "stop", "backend", env=env)
        print(
            run(
                "node",
                "node_modules/@playwright/test/cli.js",
                "test",
                "--config",
                "playwright.web.config.ts",
                env=browser_env | {"WEB_TEST_OUTAGE": "true"},
                cwd=ROOT / "frontend",
            ),
            flush=True,
        )
        print(
            "PASS: HTTPS certificate, session flow, API security, read-only DB",
            flush=True,
        )
    except Exception:
        if (temp / "web-config").exists():
            print(run(*compose, "logs", "--tail", "40", env=env, check=False))
        raise
    finally:
        for client in clients:
            run("docker", "rm", "-f", client, check=False)
        if (temp / "web-config").exists():
            run(*compose, "down", env=env, check=False)
        if created_db:
            run("docker", "rm", "-f", name, check=False)
        if created_network:
            run("docker", "network", "rm", name, check=False)
        assert temp.resolve().parent == (ROOT / "build").resolve()
        for file in temp.iterdir():
            file.chmod(0o600)
        shutil.rmtree(temp)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
