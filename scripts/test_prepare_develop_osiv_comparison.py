"""OSIV 비교 도구의 로컬 합성 검사. 원격 호출 없음."""
import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location(
    "osiv", Path(__file__).with_name("prepare-develop-osiv-comparison.py"))
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class OsivTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.n = n = {}
        exec(module.REMOTE.rsplit("\ntry:\n", 1)[0], n)
        n["BASE"] = Path(self.temp.name)
        n["SOURCE"] = n["BASE"] / "baseline"
        n["SOURCE"].mkdir()
        self.config = {"services": {"main-backend": {
            "image": n["IMAGE"], "environment": {n["KEY"]: "20000"}}}}
        (n["SOURCE"] / "diagnostic.compose.json").write_text(json.dumps(self.config))
        (n["BASE"] / "pool-diagnostic-active.json").write_text(json.dumps({
            "directory": str(n["SOURCE"]), "main_image": n["IMAGE"]}))
        self.old = {"Image": n["IMAGE"], "State": {"Running": True}, "Config": {
            "Env": ["PATH=/bin", n["KEY"]+"=20000"], "Labels": {
                "com.docker.compose.project": "seonjalal-v2-develop",
                "com.docker.compose.project.config_files": str(n["SOURCE"] / "diagnostic.compose.json"),
                "com.docker.compose.config-hash": "same"}}}
        self.calls = []
        self.applied = False
        self.hash = "same"

        def inspect(name):
            item = copy.deepcopy(self.old)
            if self.applied:
                item["Config"]["Env"].append(n["OSIV"]+"=false")
            return item

        def run(args, env=None):
            self.calls.append(args)
            if args[:3] == ["docker", "image", "inspect"]:
                return json.dumps([{"Config": {"Env": ["PATH=/bin"]}}])
            if "--hash" in args:
                return "main-backend " + self.hash
            if "--format" in args:
                return Path(args[args.index("-f")+1]).read_text()
            if "up" in args:
                self.applied = True
            return ""

        n["run"] = run
        n["inspect"] = inspect
        n["inventory"] = lambda: {n["MAIN"]: "main", "database": "untouched"}

    def test_single_setting_change_and_private_rollback(self):
        self.n["perform"]()
        marker = json.loads((self.n["BASE"] / "osiv-comparison-active.json").read_text())
        directory = Path(marker["directory"])
        before = json.loads((directory / "rollback.compose.json").read_text())
        after = json.loads((directory / "comparison.compose.json").read_text())
        self.assertEqual(after["services"]["main-backend"]["environment"].pop(self.n["OSIV"]), "false")
        self.assertEqual(after, before)
        up = [c for c in self.calls if "up" in c]
        self.assertEqual(len(up), 1)
        self.assertEqual(up[0][-1], "main-backend")
        self.assertIn("--no-deps", up[0])
        self.assertIn("never", up[0])
        self.assertEqual((directory / "rollback.compose.json").stat().st_mode & 0o777, 0o600)

    def test_changed_image_stops(self):
        self.old["Image"] = "changed"
        with self.assertRaisesRegex(RuntimeError, "CONTAINER_MISMATCH"):
            self.n["perform"]()
        self.assertFalse(self.applied)

    def test_changed_environment_stops(self):
        self.old["Config"]["Env"].append("EXTRA=changed")
        with self.assertRaisesRegex(RuntimeError, "BASELINE_ENV_DIFFERS"):
            self.n["perform"]()
        self.assertFalse(self.applied)

    def test_changed_hash_stops(self):
        self.hash = "changed"
        with self.assertRaisesRegex(RuntimeError, "HASH_CHANGED"):
            self.n["perform"]()
        self.assertFalse(self.applied)

    def test_repeat_stops(self):
        (self.n["BASE"] / "osiv-comparison-active.json").write_text("{}")
        with self.assertRaisesRegex(RuntimeError, "ALREADY_PREPARED"):
            self.n["perform"]()
        self.assertFalse(self.applied)


if __name__ == "__main__":
    unittest.main()
