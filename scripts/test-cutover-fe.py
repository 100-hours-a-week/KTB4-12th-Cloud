#!/usr/bin/env python3
"""실환경 호출 없이 비활성 FE 전환안·요청 범위 검사."""
import importlib.util
from pathlib import Path
import stat
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


remote = load("fe_remote", ROOT / "operations/staging/v1-candidate/prepare-fe-cutover.py")
prepare = load("fe_prepare", Path(__file__).with_name("prepare-cutover-fe.py"))


class Tests(unittest.TestCase):
    def setUp(self):
        self.original = (ROOT / "nginx/conf.d/default.conf").read_bytes()
        self.plans = remote.variants(self.original)

    def test_original_exact(self):
        self.assertEqual(self.plans["source-open.conf"], self.original)

    def test_verified_config_metadata(self):
        remote.validate_config_info(SimpleNamespace(
            st_mode=stat.S_IFREG | 0o644, st_uid=1001, st_gid=1001))

    def test_unverified_config_metadata_rejected(self):
        for mode, uid, gid in ((stat.S_IFREG | 0o644, 0, 0),
                               (stat.S_IFREG | 0o666, 1001, 1001),
                               (stat.S_IFLNK | 0o644, 1001, 1001),
                               (stat.S_IFREG | 0o644, 1001, 1002)):
            with self.subTest(mode=mode, uid=uid, gid=gid):
                with self.assertRaisesRegex(RuntimeError, "METADATA"):
                    remote.validate_config_info(SimpleNamespace(
                        st_mode=mode, st_uid=uid, st_gid=gid))

    def test_freeze_allowlist(self):
        value = self.plans["source-freeze.conf"].decode()
        self.assertIn("location = /api/products", value)
        self.assertEqual(value.count("if ($request_method != GET)"), 2)
        self.assertIn("location = /api {", value)
        self.assertIn("location /api/ {\n        add_header Retry-After 5 always;\n        return 503;", value)
        self.assertNotIn("proxy_pass http://backend:8080/;", value)

    def test_target_only_replaces_upstream(self):
        for stage in ("open", "freeze"):
            source = self.plans["source-" + stage + ".conf"]
            target = self.plans["target-" + stage + ".conf"]
            self.assertEqual(target.replace(b"cutover-rds-20261019", b"backend"), source)

    def test_static_preserved(self):
        for value in self.plans.values():
            self.assertIn(b"try_files $uri $uri/ /index.html;", value)

    def test_changed_layout_rejected(self):
        with self.assertRaisesRegex(RuntimeError, "LAYOUT"):
            remote.variants(self.original.replace(b"backend:8080", b"different:8080"))

    def test_inactive_syntax_check(self):
        with patch.object(remote, "run", side_effect=[b"/tmp/cutover-nginx-check.ABC123\n", b"", b""]) as call:
            remote.check_syntax("fixed-id", self.plans["target-freeze.conf"])
        args, data = call.call_args_list[1].args
        self.assertIn("/tmp/cutover-nginx-check.ABC123", args)
        self.assertEqual(call.call_args_list[2].args[0],
            ["docker", "exec", "fixed-id", "nginx", "-t", "-c", "/tmp/cutover-nginx-check.ABC123"])
        self.assertIn(b"events {}", data)
        self.assertNotIn(b"reload", data)

    def test_request_is_prepare_only(self):
        code = (ROOT / "operations/staging/v1-candidate/prepare-fe-cutover.py").read_bytes()
        request = prepare.build_request(code, self.original)
        self.assertEqual(request["InstanceIds"], ["i-0c6f5ea09b5e20859"])
        source = code.decode()
        for forbidden in ('"reload"', '"network", "connect"', '"stop-replication-task"',
                          '"restart"', 'CONFIG.write_bytes', 'CONFIG.open('):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
