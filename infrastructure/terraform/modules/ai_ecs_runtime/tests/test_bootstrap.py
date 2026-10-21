"""No AWS/DB/provider calls: verify asset integrity and DSN isolation locally."""
import copy
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


assets = load("prepare_assets")
launch = load("launch")
CONTENT = b"synthetic-model"
HASH = hashlib.sha256(CONTENT).hexdigest()
FILES = [
    {"path": "assets/models/model.onnx", "key": "release/model", "bytes": len(CONTENT), "sha256": HASH},
    {"path": "media/product.png", "key": "release/media", "bytes": len(CONTENT), "sha256": HASH},
]
MODEL = {"files": [{"path": "model.onnx", "bytes": len(CONTENT), "sha256": HASH}]}


def validate(items):
    raw = json.dumps({"files": items}).encode()
    return assets.validate_manifest(raw, hashlib.sha256(raw).hexdigest(), "release/", MODEL)


class BootstrapTests(unittest.TestCase):
    def test_complete_manifest_matches_model_lock(self):
        self.assertEqual(validate(FILES), FILES)

    def test_model_only_manifest_keeps_empty_media_path(self):
        self.assertEqual(validate(FILES[:1]), FILES[:1])
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            assets.prepare_media_directory(root)
            self.assertTrue((root / "media").is_dir())
            self.assertEqual(list((root / "media").iterdir()), [])

    def test_changed_manifest_is_rejected_before_download(self):
        with self.assertRaisesRegex(ValueError, "HASH"):
            assets.validate_manifest(b"{}", "0" * 64, "release/", MODEL)

    def test_paths_and_s3_prefix_escape_are_rejected(self):
        for key, value in [("path", "../outside"), ("path", "/media/absolute"),
                           ("path", "media/a/../../outside"), ("path", "media//duplicate"),
                           ("key", "other-release/model"), ("key", "release/../other")]:
            with self.subTest(value=value):
                items = copy.deepcopy(FILES)
                items[0][key] = value
                with self.assertRaisesRegex(ValueError, "PATH"):
                    validate(items)

    def test_duplicate_asset_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "PATH"):
            validate(FILES + [FILES[0]])

    def test_manifest_cannot_replace_model_pinned_in_image(self):
        items = copy.deepcopy(FILES)
        items[0]["sha256"] = "a" * 64
        with self.assertRaisesRegex(ValueError, "MODEL_LOCK"):
            validate(items)

    def test_positive_bounded_sizes_are_required(self):
        for size in [0, -1, True, 300 * 1024 * 1024]:
            with self.subTest(size=size):
                items = copy.deepcopy(FILES)
                items[0]["bytes"] = size
                with self.assertRaisesRegex(ValueError, "SIZE"):
                    validate(items)

    def test_streamed_download_checks_content_not_only_metadata(self):
        class Client:
            def __init__(self, content):
                self.content = content

            def get_object(self, **kwargs):
                return {"Body": io.BytesIO(self.content)}

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            assets.download_verified(Client(CONTENT), "test", FILES[0], root)
            self.assertEqual((root / FILES[0]["path"]).read_bytes(), CONTENT)
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "CONTENT"):
                assets.download_verified(Client(b"tampered"), "test", FILES[0], Path(directory))

    def test_all_db_roles_are_distinct_and_tls_verified(self):
        for role, (key, user) in launch.DSNS.items():
            with self.subTest(role=role):
                env = self.env(key, user)
                launch.validate_connection(role, env)

    @staticmethod
    def env(key="DATABASE_URL", user="ai_chat_service"):
        return {
            key: f"postgresql://{user}:synthetic@db.example:5432/ai_db_staging",
            "AI_DATABASE_HOST": "db.example", "AI_DATABASE_NAME": "ai_db_staging",
            "PGSSLMODE": "verify-full", "PGSSLROOTCERT": "/shared/tls/rds-ca.pem",
        }

    def test_dsn_cannot_disable_tls_or_override_identity(self):
        for suffix in ["?sslmode=disable", "?sslmode=prefer", "?sslrootcert=/untrusted.pem",
                       "?host=other", "?hostaddr=127.0.0.1", "?service=other",
                       "?sslmode=verify-full&sslmode=disable"]:
            with self.subTest(suffix=suffix):
                env = self.env()
                env["DATABASE_URL"] += suffix
                with self.assertRaisesRegex(ValueError, "TLS_OR_IDENTITY"):
                    launch.validate_connection("chat", env)

    def test_wrong_db_host_environment_or_role_is_rejected(self):
        for old, new in [("db.example", "other.example"), ("ai_db_staging", "ai_db_production"),
                         ("ai_chat_service", "ai_admin"), (":5432/", ":6432/")]:
            with self.subTest(new=new):
                env = self.env()
                env["DATABASE_URL"] = env["DATABASE_URL"].replace(old, new)
                with self.assertRaisesRegex(ValueError, "TLS_OR_IDENTITY"):
                    launch.validate_connection("chat", env)


if __name__ == "__main__":
    unittest.main()
