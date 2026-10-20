"""로컬 구성 계약만 검사. 실제 Nginx·Tailnet·API 통합 검증과 구분."""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1] / "operations/staging/frontend-gateway"


class GatewayContractTest(unittest.TestCase):
    def test_independent_project_and_loopback(self):
        text = (ROOT / "compose.yaml").read_text()
        self.assertIn("name: seonjalal-staging-gateway", text)
        self.assertIn('"127.0.0.1:18082:8080"', text)
        self.assertNotIn('network_mode: host', text)

    def test_fixed_image_and_readonly(self):
        text = (ROOT / "compose.yaml").read_text()
        self.assertIn("@sha256:274aee76a923e1eed970bf6bdb65538754b35608110624ba0cc441b330276e61", text)
        self.assertIn("read_only: true", text)
        self.assertIn("no-new-privileges:true", text)

    def test_tls_verified(self):
        text = (ROOT / "nginx.conf").read_text()
        self.assertIn("proxy_ssl_verify on;", text)
        self.assertIn("proxy_ssl_protocols TLSv1.2 TLSv1.3;", text)
        self.assertIn("proxy_ssl_server_name on;", text)

    def test_api_preserves_origin_and_auth(self):
        text = (ROOT / "nginx.conf").read_text().split("location /api/ {", 1)[1].split("location / {", 1)[0]
        self.assertNotIn("proxy_set_header Origin", text)
        self.assertNotIn("proxy_set_header Cookie", text)
        self.assertNotIn("proxy_set_header Authorization", text)
        self.assertIn("proxy_cookie_path /auth /api/auth;", text)
        self.assertIn("rewrite ^/api/(.*)$ /$1 break;", text)

    def test_static_strips_credentials(self):
        text = (ROOT / "nginx.conf").read_text().split("location / {", 1)[1]
        for header in ("Cookie", "Authorization"):
            self.assertIn('proxy_set_header ' + header + ' "";', text)

    def test_logs_do_not_record_query_or_credentials(self):
        text = (ROOT / "nginx.conf").read_text()
        log = next(line for line in text.splitlines() if "log_format" in line)
        self.assertNotIn("$request_uri", log)
        self.assertNotIn("$args", log)
        self.assertNotIn("$http_authorization", log)


if __name__ == "__main__":
    unittest.main()
