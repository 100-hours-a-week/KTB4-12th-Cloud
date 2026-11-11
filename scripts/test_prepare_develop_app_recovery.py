"""AWS 없이 앱 복구 범위·셸 구문·원본 변경 거부 검사."""
import importlib.util
from pathlib import Path
import subprocess
import unittest

spec = importlib.util.spec_from_file_location("recovery", Path(__file__).with_name("prepare-develop-app-recovery.py"))
recovery = importlib.util.module_from_spec(spec)
spec.loader.exec_module(recovery)


SKIP_REASON = "2026-10-21 일회성 복구는 완료됨. 도구는 당시 deploy.sh 문자열에 고정되어 있어 Redis 선기동·실패 로그가 추가된 현재 deploy.sh와 호환되지 않음. 다시 쓰려면 현재 deploy.sh에 맞게 갱신 필요."


class RecoveryTests(unittest.TestCase):
    @unittest.skip(SKIP_REASON)
    def test_app_only_and_syntax(self):
        original = (recovery.ROOT / "operations/develop/runtime/scripts/deploy.sh").read_text()
        script = recovery.app_only_script(original)
        actions = [line for line in script.splitlines() if line.startswith("sudo docker compose")]
        self.assertEqual(len(actions), 5)
        self.assertIn("pull main-backend payment-server frontend", actions[1])
        self.assertEqual([line.rsplit(" ", 1)[-1] for line in actions[2:]],
                         ["main-backend", "payment-server", "frontend"])
        self.assertTrue(all("--no-deps" in line for line in actions[2:]))
        self.assertNotIn("--remove-orphans", script)
        result = subprocess.run(["bash", "-n"], input=script, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0)

    def test_original_drift_rejected(self):
        with self.assertRaises(ValueError):
            recovery.app_only_script("changed")

    @unittest.skip(SKIP_REASON)
    def test_request_target_and_backup_guard(self):
        request = recovery.build_request()
        self.assertEqual(request["InstanceIds"], [recovery.runtime.INSTANCE])
        command = request["Parameters"]["commands"][0]
        self.assertIn(recovery.DUMP_SHA, command)
        self.assertIn("MYSQL_CONTAINER_IMAGE_AND_STORAGE_UNCHANGED", command)
        self.assertNotIn("sudo docker compose \\", command)


if __name__ == "__main__":
    unittest.main()
