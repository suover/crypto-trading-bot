"""Exercise deploy ordering/fail-closed behavior using a fake Docker executable."""

import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "deploy_web.sh"
SHA = "a" * 40


class DeployWebTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        for name in ["docker-compose.web.yml", "images.tar.gz", "web-config", "secret"]:
            (self.root / name).write_text("fixture-only")
        self.log = self.root / "commands"
        self.environment = os.environ | {
            "PATH": str(self.root) + os.pathsep + os.environ["PATH"],
            "COMMAND_LOG": str(self.log),
            "EXPECTED": SHA,
            "FAKE_CONFIG": json.dumps(
                {
                    "services": {
                        "web": {"image": "crypto-trading-web-frontend:" + SHA},
                        "backend": {
                            "environment": {
                                "BACKEND_DB_USERNAME": "backend_reader",
                                "BACKEND_DB_URL": "jdbc:postgresql://postgres:5432/crypto_trading_bot",
                            }
                        },
                    },
                    "secrets": {
                        key: {"file": str(self.root / "secret")}
                        for key in ["backend_password", "tls_certificate", "tls_key"]
                    },
                    "networks": {"database": {"name": "fixture-network"}},
                }
            ),
        }
        docker = self.root / "docker"
        docker.write_text("""#!/usr/bin/env python3
import os, sys
args = sys.argv[1:]
with open(os.environ["COMMAND_LOG"], "a") as f:
    f.write(" ".join(args) + "\\n")
stage = os.environ.get("FAIL_STAGE")
if stage and stage in args:
    sys.exit(1)
if "--format" in args and "json" in args:
    print(os.environ["FAKE_CONFIG"])
if args[:2] == ["image", "inspect"]:
    print(os.environ.get("IMAGE_REVISION", os.environ["EXPECTED"]))
""")
        docker.chmod(0o755)

    def deploy(self, *args):
        return subprocess.run(
            [
                "bash",
                str(SCRIPT),
                *(
                    args
                    or [
                        "--expected-sha",
                        SHA,
                        "--config",
                        str(self.root / "web-config"),
                    ]
                ),
            ],
            cwd=self.root,
            env=self.environment,
            text=True,
            capture_output=True,
        )

    def commands(self):
        return self.log.read_text() if self.log.exists() else ""

    def test_success_only_replaces_web_after_preflight(self):
        result = self.deploy()
        self.assertEqual(result.returncode, 0, result.stderr)
        commands = self.commands()
        self.assertLess(
            commands.index("config --quiet"), commands.index("load --input")
        )
        self.assertLess(commands.index("image inspect"), commands.index(" -t"))
        self.assertLess(commands.index(" -t"), commands.index("up -d"))
        self.assertIn(
            "up -d --no-build --wait --wait-timeout 180 backend web", commands
        )
        self.assertNotIn("down", commands)
        self.assertNotIn("migrate", commands)
        self.assertNotIn("deploy_production", commands)

    def test_invalid_sha_does_not_call_docker(self):
        self.assertNotEqual(
            self.deploy(
                "--expected-sha", "main", "--config", str(self.root / "web-config")
            ).returncode,
            0,
        )
        self.assertEqual(self.commands(), "")

    def test_failed_preflight_never_replaces_running_services(self):
        for stage in ["config", "load", "inspect", "-t"]:
            with self.subTest(stage=stage):
                self.environment["FAIL_STAGE"] = stage
                self.assertNotEqual(self.deploy().returncode, 0)
                self.assertNotIn("up -d", self.commands())

    def test_wrong_image_revision_fails_before_cutover(self):
        self.environment["IMAGE_REVISION"] = "b" * 40
        self.assertNotEqual(self.deploy().returncode, 0)
        self.assertNotIn("up -d", self.commands())

    def test_missing_secret_fails_before_load(self):
        (self.root / "secret").unlink()
        self.assertNotEqual(self.deploy().returncode, 0)
        self.assertNotIn("load --input", self.commands())

    def test_broad_role_or_unexpected_services_fail(self):
        original = self.environment["FAKE_CONFIG"]
        for change in ["role", "engine"]:
            with self.subTest(change=change):
                config = json.loads(original)
                if change == "role":
                    config["services"]["backend"]["environment"][
                        "BACKEND_DB_USERNAME"
                    ] = "trading_user"
                else:
                    config["services"]["migrate"] = {}
                self.environment["FAKE_CONFIG"] = json.dumps(config)
                self.assertNotEqual(self.deploy().returncode, 0)
                self.assertNotIn("load --input", self.commands())

    def test_jdbc_credential_override_is_rejected(self):
        config = json.loads(self.environment["FAKE_CONFIG"])
        config["services"]["backend"]["environment"]["BACKEND_DB_URL"] += (
            "?user=trading_user"
        )
        self.environment["FAKE_CONFIG"] = json.dumps(config)
        self.assertNotEqual(self.deploy().returncode, 0)
        self.assertNotIn("load --input", self.commands())

    def test_unhealthy_runtime_reports_failure_without_automatic_rollback(self):
        self.environment["FAIL_STAGE"] = "up"
        result = self.deploy()
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("containers healthy", result.stdout)
        self.assertNotIn("down", self.commands())


if __name__ == "__main__":
    unittest.main()
