"""AWS/Docker 호출 없이 후보 격리·Secret 비노출·실패 중지 동작 검증."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location(
    "candidate", Path(__file__).resolve().parents[1] / "operations/staging/v1-candidate/start.py"
)
candidate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(candidate)


class CandidateTest(unittest.TestCase):
    def run_candidate(self, *, products_fail=False, network_exists=False):
        commands = []
        source = {"Image": candidate.IMAGE, "Id": "source-unchanged", "State": {"Running": True},
                  "Config": {"Env": ["DB_URL=jdbc:mysql://database:3306/seonjalal_develop"]}}
        password = "unit-test-secret-not-real"

        def call(args):
            commands.append(args)
            if args[:3] == ["docker", "inspect", "seonjalal-backend"]:
                return json.dumps([source])
            if args[:3] == ["docker", "inspect", candidate.NAME]:
                return json.dumps([{"State": {"Running": True, "Health": {"Status": "healthy"}}}])
            if args[:3] == ["docker", "network", "ls"]:
                return "existing-network" if network_exists else ""
            if args[:2] == ["docker", "create"]:
                self.assertNotIn(password, " ".join(args))
                self.assertEqual((candidate.TARGET / "candidate.env").stat().st_mode & 0o777, 0o600)
            return ""

        def get(url):
            if products_fail and "/products" in url:
                raise RuntimeError("SIMULATED_PRODUCTS_FAILURE")
            return b'{"status":"UP"}'

        original_read = Path.read_text

        def read(path, *args, **kwargs):
            if str(path) == "/proc/meminfo":
                return "MemAvailable: 2500000 kB\n"
            return original_read(path, *args, **kwargs)

        with tempfile.TemporaryDirectory(prefix="candidate-unit-") as directory:
            with patch.object(candidate, "TARGET", Path(directory)), \
                 patch.object(candidate, "identity_guard"), \
                 patch.object(candidate, "call", side_effect=call), \
                 patch.object(candidate, "get", side_effect=get), \
                 patch.object(candidate, "aws", return_value={"SecretString": json.dumps({
                     key: password for key in ("DB_PASSWORD", "JWT_SECRET_BASE64",
                     "LOGIN_RATE_LIMIT_HMAC_SECRET_BASE64", "PROFILING_SERVICE_TOKEN")
                 }), "VersionId": "test-version"}) as aws, \
                 patch.object(Path, "read_text", read), \
                 patch.object(candidate.subprocess, "run") as stop:
                if products_fail or network_exists:
                    with self.assertRaises(RuntimeError):
                        candidate.main()
                else:
                    candidate.main()
                self.assertFalse((Path(directory) / "candidate.env").exists())
                if network_exists:
                    aws.assert_not_called()
                    self.assertFalse(any(args[:2] == ["docker", "create"] for args in commands))
                if products_fail:
                    self.assertEqual(stop.call_args.args[0], ["docker", "stop", "--time", "60", candidate.NAME])
                elif not network_exists:
                    stop.assert_not_called()
        return commands

    def test_success_is_loopback_limited_and_does_not_replace_source(self):
        commands = self.run_candidate()
        create = next(args for args in commands if args[:2] == ["docker", "create"])
        for value in ("127.0.0.1:18083:8080", "1024m", "--read-only", "no-new-privileges", candidate.IMAGE):
            self.assertIn(value, create)
        self.assertEqual(create[create.index("--restart") + 1], "no")
        self.assertNotIn("rm", [part for args in commands for part in args[:2]])

    def test_failed_products_stops_only_new_candidate(self):
        self.run_candidate(products_fail=True)

    def test_existing_network_blocks_new_candidate_and_secret_read(self):
        self.run_candidate(network_exists=True)


if __name__ == "__main__":
    unittest.main()
