"""AWS/Nginx를 실행하지 않고 시험 프록시 설정·전환/복귀 범위 검사."""
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location(
    "proxy", Path(__file__).resolve().parents[1] / "operations/staging/v1-candidate/proxy.py"
)
proxy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(proxy)


class ProxyTest(unittest.TestCase):
    def test_config_is_loopback_readonly_verified_tls_and_no_retry(self):
        for target in proxy.TARGETS:
            config = proxy.config(target)
            for text in ("listen 127.0.0.1:18084;", "proxy_ssl_verify on;",
                         "proxy_next_upstream off;", "keepalive_timeout 0;",
                         "^(GET|HEAD)$", "return 405;", "location / { return 404; }"):
                self.assertIn(text, config)
            self.assertIn("proxy_pass " + proxy.TARGETS[target] + ";", config)
            self.assertIn("X-Rehearsal-Upstream " + target + " always", config)
            self.assertNotIn("listen 443", config)
            self.assertNotIn("include /etc/nginx", config)

    def exercise(self, fail_switch=False):
        with tempfile.TemporaryDirectory(prefix="isolated-proxy-unit-") as directory:
            path = Path(directory).resolve() / "proxy"
            with patch.object(proxy, "PROXY", path), \
                 patch.object(proxy.candidate, "identity_guard"), \
                 patch.object(proxy.os, "getuid", return_value=0), \
                 patch.object(proxy.candidate.socket, "socket"), \
                 patch.object(proxy.candidate.time, "sleep"), \
                 patch.object(proxy, "nginx") as nginx, \
                 patch.object(proxy, "request", return_value=(None, b'{"status":"UP"}')), \
                 patch.object(proxy, "verify") as verify:
                with patch("sys.argv", ["proxy", "start"]):
                    proxy.main()
                self.assertEqual((path / "active.conf").resolve(), path / "v1.conf")
                nginx.assert_any_call(path / "active.conf")
                if fail_switch:
                    verify.side_effect = RuntimeError("SIMULATED_BAD_TARGET")
                with patch("sys.argv", ["proxy", "switch", "--target", "ecs"]):
                    if fail_switch:
                        with self.assertRaises(RuntimeError):
                            proxy.main()
                        self.assertEqual((path / "active.conf").resolve(), path / "v1.conf")
                    else:
                        proxy.main()
                        self.assertEqual((path / "active.conf").resolve(), path / "ecs.conf")
                nginx.assert_any_call(path / "active.conf", "-s", "reload")
                for args in nginx.call_args_list:
                    self.assertTrue(str(args.args[0]).startswith(str(path) + "/"))

    def test_start_and_switch_use_only_isolated_config(self):
        self.exercise()

    def test_failed_switch_restores_previous_target(self):
        self.exercise(fail_switch=True)


if __name__ == "__main__":
    unittest.main()
