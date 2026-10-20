"""실제 AWS/Source 연결 없이 구조 수집의 경계·해시·대상 검사."""
import base64
import gzip
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location(
    "collect_schema", Path(__file__).with_name("collect-cutover-source-schema.py"))
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class SourceSchemaTest(unittest.TestCase):
    def sql(self):
        return "\n".join(f"CREATE TABLE `{name}` (`id` bigint PRIMARY KEY);" for name in module.TABLES)

    def payload(self, raw):
        return json.dumps({"kind": "cutover_source_schema_v1",
                           "sha256": hashlib.sha256(raw).hexdigest(),
                           "gzip_base64": base64.b64encode(gzip.compress(raw)).decode()})

    def test_valid_structure_saved_and_hash_verified(self):
        raw = self.sql().encode()
        with tempfile.TemporaryDirectory() as directory:
            path, digest = module.save_payload(self.payload(raw), Path(directory))
            self.assertEqual(path.read_bytes(), raw)
            self.assertEqual(digest, hashlib.sha256(raw).hexdigest())
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_row_data_and_schema_escape_rejected(self):
        for tail in ("\nINSERT INTO `users` VALUES (1);", "\nUSE `old`;",
                     "\nDROP TABLE `users`;", "\nSELECT * FROM `seonjalal_develop`.`users`;"):
            with self.subTest(tail=tail):
                with self.assertRaises(module.runtime.SafetyStop):
                    module.validate_schema(self.sql() + tail)

    def test_changed_table_inventory_rejected(self):
        with self.assertRaises(module.runtime.SafetyStop):
            module.validate_schema(self.sql().replace("CREATE TABLE `users`", "CREATE TABLE `other`"))

    def test_hash_mismatch_does_not_save(self):
        payload = json.loads(self.payload(self.sql().encode()))
        payload["sha256"] = "0" * 64
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(module.runtime.SafetyStop):
                module.save_payload(json.dumps(payload), Path(directory))
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_remote_program_compiles_and_locks_scope(self):
        program = module.remote_program()
        compile(program, "remote_schema", "exec")
        self.assertIn("--no-data", program)
        self.assertIn("--skip-lock-tables", program)
        self.assertIn("--set-gtid-purged=OFF", program)
        self.assertIn("SOURCE_IMAGE_OR_STATE_CHANGED", program)
        self.assertEqual(module.request()["InstanceIds"], [module.INSTANCE])
        self.assertNotIn("sudo docker restart", program)

    def test_missing_approval_stops_before_aws(self):
        with patch("sys.argv", ["collect"]), patch.object(module.runtime, "aws") as aws:
            with self.assertRaises(module.runtime.SafetyStop):
                module.main()
            aws.assert_not_called()


if __name__ == "__main__":
    unittest.main()
