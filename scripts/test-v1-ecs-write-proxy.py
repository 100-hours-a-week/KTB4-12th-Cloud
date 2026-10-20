"""실제 서버 호출 없이 선물 경로 제한·원래 읽기 설정 보존/복원 검사."""
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("write_proxy", Path(__file__).resolve().parents[1] / "operations/staging/v1-candidate/write-proxy.py")
write = importlib.util.module_from_spec(spec)
spec.loader.exec_module(write)


class WriteProxyTest(unittest.TestCase):
    def test_only_two_exact_post_locations_added(self):
        for target in write.base.TARGETS:
            config = write.write_config(target)
            self.assertEqual(config.count("^POST$"), 2)
            self.assertEqual(config.count("^(GET|HEAD)$"), 2)
            self.assertIn("location = /gifts {", config)
            self.assertIn("location = /gifts/preflight {", config)
            self.assertIn("location / { return 404; }", config)
            self.assertEqual(config.count("proxy_next_upstream off;"), 4)
            self.assertEqual(config.count("proxy_ssl_verify on;"), 4)
            self.assertIn("listen 127.0.0.1:18084;", config)

    def test_enable_requires_approval_before_server_access(self):
        with patch("sys.argv", ["test", "enable"]), patch.object(write.base.candidate, "identity_guard") as guard:
            with self.assertRaises(RuntimeError):
                write.main()
            guard.assert_not_called()

    def test_enable_switch_disable_preserves_original_readonly_files(self):
        with tempfile.TemporaryDirectory(prefix="write-proxy-unit-") as directory:
            proxy = Path(directory).resolve()
            with patch.object(write.base, "PROXY", proxy), \
                 patch.object(write.base.candidate, "identity_guard"), \
                 patch.object(write.os, "getuid", return_value=0), \
                 patch.object(write.base, "request"), patch.object(write.base, "verify"), \
                 patch.object(write.base, "nginx"):
                for target in write.base.TARGETS:
                    (proxy / (target + ".conf")).write_text(write.base.config(target))
                original = (proxy / "v1.conf").read_bytes()
                (proxy / "active.conf").symlink_to(proxy / "v1.conf")
                for arguments, expected in ((["enable", "--approved-fixture-writes"], "v1-write.conf"),
                                            (["switch", "--target", "ecs", "--approved-fixture-writes"], "ecs-write.conf"),
                                            (["disable"], "v1.conf")):
                    with patch("sys.argv", ["test", *arguments]):
                        write.main()
                    self.assertEqual((proxy / "active.conf").resolve().name, expected)
                self.assertEqual((proxy / "v1.conf").read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
