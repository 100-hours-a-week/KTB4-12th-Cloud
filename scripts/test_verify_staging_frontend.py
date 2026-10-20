"""합성 파일만 사용하는 검사: AWS 호출 및 자원 변경 없음."""
import hashlib
import importlib.util
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location("frontend_verify", Path(__file__).with_name("verify-staging-frontend.py"))
verify = importlib.util.module_from_spec(spec)
spec.loader.exec_module(verify)


class VerificationTest(unittest.TestCase):
    def test_matching_content(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "index.html"
            path.write_bytes(b"test")
            verify.verify_file(path, {"path": "index.html", "bytes": 4, "sha256": hashlib.sha256(b"test").hexdigest()})

    def test_same_size_wrong_content_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "index.html"
            path.write_bytes(b"evil")
            with self.assertRaises(ValueError):
                verify.verify_file(path, {"path": "index.html", "bytes": 4, "sha256": hashlib.sha256(b"test").hexdigest()})

    def test_invalid_manifest_rejected(self):
        with self.assertRaises(ValueError):
            verify.validate_manifest({"files": []})


if __name__ == "__main__":
    unittest.main()
