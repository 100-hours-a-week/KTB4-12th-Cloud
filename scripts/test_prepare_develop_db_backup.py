"""백업 요청의 대상·명령 구문·재시작 부재 로컬 검사."""
import ast
import importlib.util
from pathlib import Path
import subprocess
import unittest

spec = importlib.util.spec_from_file_location("backup", Path(__file__).with_name("prepare-develop-db-backup.py"))
backup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(backup)


class BackupTests(unittest.TestCase):
    def test_confirmed_bind_mount_only(self):
        function = next(node for node in ast.parse(backup.REMOTE).body
                        if isinstance(node, ast.FunctionDef) and node.name == "db_mount_safe")
        scope = {}
        exec(compile(ast.Module(body=[function], type_ignores=[]), "<mount-check>", "exec"), scope)
        check = scope["db_mount_safe"]
        mount = {"Destination": "/var/lib/mysql", "Type": "bind",
                 "Source": "/var/lib/seonjalal-develop/mysql", "RW": True}
        self.assertTrue(check([mount]))
        for mounts in ([], [mount, mount], [{**mount, "Type": "volume"}],
                       [{**mount, "Source": "/unexpected"}], [{**mount, "RW": False}]):
            self.assertFalse(check(mounts))

    def test_target_and_backup_only(self):
        request = backup.build_request()
        self.assertEqual(request["InstanceIds"], [backup.runtime.INSTANCE])
        command = request["Parameters"]["commands"][0]
        self.assertIn("--single-transaction", command)
        self.assertIn("LOCK_NB", command)
        for forbidden in ("compose up", "docker restart", "SecretString", "DROP DATABASE", "get-secret-value"):
            self.assertNotIn(forbidden, command)

    def test_inner_shell_syntax(self):
        checked = 0
        for node in ast.walk(ast.parse(backup.REMOTE)):
            if not isinstance(node, ast.List) or len(node.elts) < 6:
                continue
            try:
                command = ast.literal_eval(node.elts[-1])
                prefix = [ast.literal_eval(item) for item in node.elts[-3:-1]]
            except (ValueError, TypeError):
                continue
            if prefix == ["sh", "-c"]:
                result = subprocess.run(["sh", "-n", "-c", command], capture_output=True)
                self.assertEqual(result.returncode, 0)
                checked += 1
        self.assertEqual(checked, 2)


if __name__ == "__main__":
    unittest.main()
