"""원격 AWS/Docker 실행 없이 신규후보 격리·Secret 전달·실패 정지 검사."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


remote = load("candidate_remote", ROOT / "operations/staging/v1-candidate/start-cutover.py")
prepare = load("candidate_prepare", ROOT / "scripts/prepare-cutover-candidate.py")


class CutoverCandidateTest(unittest.TestCase):
    def items(self):
        source_env = {"DB_URL": "jdbc:mysql://database:3306/seonjalal_develop", "DB_USERNAME": "source-user",
                      "DB_PASSWORD": "source-private-password", **remote.SAFE_FLAGS,
                      "JWT_SECRET_BASE64": "source-private-jwt", "JWT_ISSUER": "source-issuer",
                      "LOGIN_RATE_LIMIT_HMAC_SECRET_BASE64": "source-private-hmac"}
        old_env = {"DB_URL": "jdbc:mysql://" + remote.DB_HOST + ":3306/seonjalal_develop",
                   "DB_USERNAME": "seonjalal_staging_app", "DB_PASSWORD": "rds-private-password",
                   "JWT_SECRET_BASE64": "old-private-jwt", "JWT_ISSUER": "old-issuer"}
        result = []
        for name in remote.KEEP:
            values = source_env if name == remote.SOURCE else old_env if name == remote.OLD else {}
            result.append({"Name": "/" + name, "Id": name + "-preserved", "Image": remote.IMAGE,
                           "State": {"Running": True},
                           "Config": {"Env": [key + "=" + value for key, value in values.items()]}})
        return result

    def run_main(self, *, products_fail=False, low_memory=False, auth_mismatch=False, already_exists=False):
        items = self.items()
        environment = remote.build_environment(items[0], items[1])
        commands = []
        create_input = []

        def call(args, **kwargs):
            commands.append(args)
            if args == ["docker", "inspect", *remote.KEEP]:
                return json.dumps(items)
            if args == ["docker", "inspect", remote.NAME]:
                actual = dict(environment)
                if auth_mismatch:
                    actual["JWT_ISSUER"] = "unexpected"
                return json.dumps([{"Id": "new-container-id", "Image": remote.IMAGE,
                                    "State": {"Running": True, "Health": {"Status": "healthy"}},
                                    "Config": {"Env": [key + "=" + value for key, value in actual.items()]}}])
            if args[:2] == ["docker", "create"]:
                self.assertIn("--env-file", args)
                self.assertIn("/dev/stdin", args)
                self.assertNotIn("rds-private-password", " ".join(args))
                self.assertNotIn("source-private-jwt", " ".join(args))
                create_input.append(kwargs["input"])
                return "new-container-id"
            return ""

        def get(url):
            if "/products" in url:
                if products_fail:
                    raise RuntimeError("SIMULATED_PRODUCTS_FAILURE")
                return b'{"data":{"products":[]},"error":null}'
            return b'{"status":"UP"}'

        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "new-candidate"
            if already_exists:
                target.mkdir()
            with patch.object(remote, "TARGET", target), patch.object(remote, "identity_guard"), \
                 patch.object(remote, "memory_available", return_value=1000 if low_memory else 1906), \
                 patch.object(remote.socket, "socket"), patch.object(remote, "call", side_effect=call), \
                 patch.object(remote, "get", side_effect=get):
                if products_fail or low_memory or auth_mismatch or already_exists:
                    with self.assertRaises((RuntimeError, FileExistsError)):
                        remote.main({"start.py": b"public", "tls-bootstrap.sh": b"public", "rds-ca.pem": b"public"})
                else:
                    remote.main({"start.py": b"public", "tls-bootstrap.sh": b"public", "rds-ca.pem": b"public"})
                if target.joinpath("verification.json").exists():
                    record = target.joinpath("verification.json").read_text()
                    self.assertNotIn("rds-private-password", record)
                    self.assertNotIn("source-private-jwt", record)
                    self.assertFalse(any("private-password" in path.read_text()
                                         for path in target.iterdir() if path.is_file()))
        return commands, create_input

    def test_success_loopback_limits_and_only_new_container_started(self):
        commands, inputs = self.run_main()
        command = next(args for args in commands if args[:2] == ["docker", "create"])
        for value in ("127.0.0.1:18085:8080", "1024m", "--read-only", "no-new-privileges", remote.IMAGE):
            self.assertIn(value, command)
        self.assertEqual(command[command.index("--restart") + 1], "no")
        self.assertEqual([args for args in commands if args[:2] == ["docker", "start"]],
                         [["docker", "start", remote.NAME]])
        self.assertTrue(any("DB_PASSWORD=rds-private-password\n" in value for value in inputs))
        self.assertTrue(any("JWT_SECRET_BASE64=source-private-jwt\n" in value for value in inputs))
        self.assertFalse(any(args[:2] == ["docker", "stop"] for args in commands))

    def test_failed_products_stops_only_new_candidate(self):
        commands, _ = self.run_main(products_fail=True)
        self.assertEqual([args for args in commands if args[:2] == ["docker", "stop"]],
                         [["docker", "stop", "--time", "30", remote.NAME]])

    def test_post_start_auth_mismatch_stops_only_new_candidate(self):
        commands, _ = self.run_main(auth_mismatch=True)
        self.assertEqual([args[-1] for args in commands if args[:2] == ["docker", "stop"]], [remote.NAME])

    def test_low_memory_blocks_before_docker_changes(self):
        commands, _ = self.run_main(low_memory=True)
        self.assertEqual(commands, [])

    def test_existing_directory_preserved(self):
        commands, _ = self.run_main(already_exists=True)
        self.assertEqual(commands, [])

    def test_environment_preserves_source_auth_and_overrides_both_datasource_names(self):
        items = self.items()
        result = remote.build_environment(items[0], items[1])
        self.assertEqual(result["JWT_ISSUER"], "source-issuer")
        self.assertIn("/" + remote.SCHEMA + "?sslMode=VERIFY_IDENTITY", result["DB_URL"])
        self.assertEqual(result["DB_URL"], result["SPRING_DATASOURCE_URL"])
        self.assertEqual(result["DB_PASSWORD"], "rds-private-password")

    def test_missing_auth_or_scheduler_change_blocks_environment(self):
        items = self.items()
        items[0]["Config"]["Env"] = [value for value in items[0]["Config"]["Env"]
                                      if not value.startswith("JWT_ISSUER=")]
        with self.assertRaises(RuntimeError):
            remote.build_environment(items[0], items[1])
        items = self.items()
        items[0]["Config"]["Env"].append("SCHEDULING_ENABLED=true")
        with self.assertRaises(RuntimeError):
            remote.build_environment(items[0], items[1])

    def test_request_has_only_public_files_and_fixed_nonprod_target(self):
        request = prepare.build_request({"start.py": b"public code", "tls-bootstrap.sh": b"public script", "rds-ca.pem": b"public CA"})
        raw = json.dumps(request)
        self.assertEqual(request["InstanceIds"], [remote.INSTANCE])
        self.assertNotIn("source-private-jwt", raw)
        self.assertNotIn("rds-private-password", raw)
        self.assertEqual(request["DocumentName"], "AWS-RunShellScript")


if __name__ == "__main__":
    unittest.main()
