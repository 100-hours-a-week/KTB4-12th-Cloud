#!/usr/bin/env python3
"""정리 전 자료 수집의 로컬 안전 검사. AWS 실행 없음."""
import base64
import hashlib
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace

spec = importlib.util.spec_from_file_location("closeout", Path(__file__).with_name("collect-rehearsal-closeout.py"))
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class Tests(unittest.TestCase):
    def test_empty_successful_cli_response(self):
        with patch.object(module.subprocess, "run", return_value=SimpleNamespace(returncode=0, stdout="", stderr="")):
            self.assertEqual(module.aws("autoscaling", "suspend-processes"), {})

    def test_artifacts_saved_with_hash(self):
        value = b"example"
        bundle = {"files": {name: {"base64": base64.b64encode(value).decode(),
                   "sha256": hashlib.sha256(value).hexdigest()} for name in module.FILES}}
        with tempfile.TemporaryDirectory() as directory:
            module.write_artifacts(Path(directory), bundle)
            self.assertEqual((Path(directory) / "events.jsonl").read_bytes(), value)

    def test_unexpected_path_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(RuntimeError):
                module.write_artifacts(Path(directory), {"files": {"../secret": {}}})

    def test_hash_mismatch_rejected(self):
        bundle = {"files": {name: {"base64": "eA==", "sha256": "invalid"} for name in module.FILES}}
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(RuntimeError):
                module.write_artifacts(Path(directory), bundle)

    def test_remote_programs_compile_and_no_mutations(self):
        for source in (module.evidence_program("0"*64), module.production_program()):
            compile(source, "remote-read", "exec")
            for forbidden in ("get-secret-value", "docker stop", "SET GLOBAL", "terminate-instances", "delete-db-instance"):
                self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
