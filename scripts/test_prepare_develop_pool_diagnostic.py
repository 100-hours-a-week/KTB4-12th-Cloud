"""합성 Docker 응답으로만 안전 경계 검사. 실제 AWS/Docker 호출 없음."""
import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location(
    "diagnostic", Path(__file__).with_name("prepare-develop-pool-diagnostic.py"))
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class DiagnosticTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.ns = {}
        exec(module.REMOTE.rsplit("\ntry:\n", 1)[0], self.ns)
        n = self.ns
        n["BASE"] = Path(self.temp.name)
        n["identity"] = lambda: None
        # 테스트에서는 /run 대신 임시 경로에 잠금 파일 보관.
        real_open = open
        n["open"] = lambda p, *a, **kw: real_open(
            str(n["BASE"] / "test.lock") if p == "/run/seonjalal-develop-pool-diagnostic.lock" else p,
            *a, **kw)
        self.env = {"PATH": "/bin", "DB_PASSWORD": "synthetic-only"}
        self.old = {"Image": n["IMAGE"], "State": {"Running": True}, "Config": {
            "Image": "example:existing", "Env": [k+"="+v for k,v in self.env.items()],
            "Labels": {"com.docker.compose.project": "seonjalal-v2-develop",
                       "com.docker.compose.project.config_files": str(n["BASE"] / "compose.yaml"),
                       "com.docker.compose.config-hash": "matching"}}}
        self.db = {"Config": {"Env": ["MYSQL_DATABASE=test", "MYSQL_USER=test",
                                      "MYSQL_ROOT_PASSWORD=synthetic-only"]}}
        self.config = {"services": {"main-backend": {
            "image": "example:existing", "environment": {"DB_PASSWORD": "synthetic-only"}}}}
        self.applied = False
        self.calls = []
        self.hash = "matching"

        def inspect(name):
            if name == n["MAIN"]:
                result = copy.deepcopy(self.old)
                if self.applied:
                    result["Config"]["Env"].append(n["KEY"]+"=20000")
                return result
            if name == n["DB"]:
                return self.db
            return {"Config": {"Image": "example:untouched"}}

        def run(args, env=None):
            self.calls.append(args)
            if "--format" in args:
                return json.dumps(self.config)
            if args[:3] == ["docker", "image", "inspect"]:
                return json.dumps([{"Config": {"Env": ["PATH=/bin"]}}])
            if "--hash" in args:
                return "main-backend " + self.hash
            if "up" in args:
                self.applied = True
            return ""

        n["inspect"] = inspect
        n["run"] = run
        n["inventory"] = lambda: {"main": "old", "db": "unchanged"}

    def test_same_image_and_only_leak_setting(self):
        self.ns["main"]()
        marker = json.loads((self.ns["BASE"] / "pool-diagnostic-active.json").read_text())
        directory = Path(marker["directory"])
        before = json.loads((directory / "rollback.compose.json").read_text())
        after = json.loads((directory / "diagnostic.compose.json").read_text())
        self.assertEqual(after["services"]["main-backend"]["image"], self.ns["IMAGE"])
        after["services"]["main-backend"]["environment"].pop(self.ns["KEY"])
        self.assertEqual(before, after)
        up = [c for c in self.calls if "up" in c]
        self.assertEqual(len(up), 1)
        self.assertEqual(up[0][-1], "main-backend")
        self.assertIn("--no-deps", up[0])
        self.assertIn("never", up[0])
        self.assertEqual((directory.stat().st_mode & 0o777), 0o700)
        self.assertEqual(((directory / "rollback.compose.json").stat().st_mode & 0o777), 0o600)

    def test_changed_environment_stops_before_mutation(self):
        self.config["services"]["main-backend"]["environment"]["DB_PASSWORD"] = "different"
        with self.assertRaisesRegex(RuntimeError, "ENV_DIFFERS"):
            self.ns["main"]()
        self.assertFalse(self.applied)

    def test_changed_compose_stops(self):
        self.hash = "changed"
        with self.assertRaisesRegex(RuntimeError, "CONFIG_CHANGED"):
            self.ns["main"]()
        self.assertFalse(self.applied)

    def test_wrong_image_stops(self):
        self.old["Image"] = "different"
        with self.assertRaisesRegex(RuntimeError, "IDENTITY_MISMATCH"):
            self.ns["main"]()
        self.assertFalse(self.applied)

    def test_repeat_request_stops(self):
        (self.ns["BASE"] / "pool-diagnostic-active.json").write_text("{}")
        with self.assertRaisesRegex(RuntimeError, "ALREADY_PREPARED"):
            self.ns["main"]()
        self.assertFalse(self.applied)


if __name__ == "__main__":
    unittest.main()
