"""실제 원격 실행 없이 Develop 공개파일 갱신 범위 회귀 검사."""
import ast
import base64
import hashlib
import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location("update", Path(__file__).with_name("prepare-develop-runtime-update.py"))
update = importlib.util.module_from_spec(spec)
spec.loader.exec_module(update)


class UpdateTest(unittest.TestCase):
    def setUp(self):
        self.request = update.build_request()
        self.command = self.request["Parameters"]["commands"][0]
        self.script = self.command.removeprefix("python3 - <<'PY'\n").removesuffix("\nPY")

    def test_only_develop_instance(self):
        self.assertEqual(self.request["InstanceIds"], [update.INSTANCE])
        self.assertIn('identity.get("instanceId")', self.script)
        self.assertIn('identity.get("accountId")', self.script)

    def test_only_three_public_files_and_hashes(self):
        tree = ast.parse(self.script)
        entry = next(node for node in tree.body if isinstance(node, ast.Assign)
                     and any(isinstance(target, ast.Name) and target.id == "files" for target in node.targets))
        files = ast.literal_eval(entry.value)
        self.assertEqual(set(files), set(update.FILES))
        for value in files.values():
            self.assertEqual(hashlib.sha256(base64.b64decode(value["base64"])).hexdigest(), value["sha256"])
        self.assertNotIn(".env", files)

    def test_no_deployment_or_secret_call(self):
        for forbidden in ("get-secret-value", "put-secret-value", "systemctl", "docker compose", "tailscale serve"):
            self.assertNotIn(forbidden, self.script)

    def test_existing_lock_backup_and_rollback_present(self):
        self.assertIn("fcntl.LOCK_EX | fcntl.LOCK_NB", self.script)
        self.assertIn("lock_path.is_file()", self.script)
        self.assertIn("os.O_NOFOLLOW", self.script)
        self.assertIn("shutil.copy2", self.script)
        self.assertIn("for relative in reversed(changed)", self.script)


if __name__ == "__main__":
    unittest.main()
