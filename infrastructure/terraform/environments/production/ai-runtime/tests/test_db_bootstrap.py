"""Offline checks for the one-time DB credential bootstrap contract."""

import importlib.util
import json
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch


def load_bootstrap():
    boto3 = types.ModuleType("boto3")
    psycopg = types.ModuleType("psycopg")
    psycopg.connect = None
    psycopg.sql = types.SimpleNamespace()
    with patch.dict(sys.modules, {"boto3": boto3, "psycopg": psycopg}):
        spec = importlib.util.spec_from_file_location("ai_db_bootstrap", Path(__file__).resolve().parents[1] / "bootstrap.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    return module


class FakeSecrets:
    def __init__(self, current=None):
        self.current = current
        self.puts = []

    def describe_secret(self, **_):
        return {"VersionIdsToStages": {"v1": ["AWSCURRENT"]} if self.current is not None else {}}

    def get_secret_value(self, **_):
        return {"SecretString": json.dumps(self.current)}

    def put_secret_value(self, **kwargs):
        self.puts.append(kwargs)


class BootstrapContractTests(unittest.TestCase):
    def setUp(self):
        self.bootstrap = load_bootstrap()
        self.host = "ai-db.example.rds.amazonaws.com"

    def test_generated_dsn_has_pinned_database_role_and_tls(self):
        dsn = self.bootstrap._dsn("ai_migrator", "secret:/?", self.host)
        self.assertEqual(self.bootstrap._password(dsn, "ai_migrator", self.host), "secret:/?")
        with self.assertRaises(ValueError):
            self.bootstrap._password(dsn, "ai_chat_service", self.host)

    def test_role_creation_uses_client_side_scram_verifier(self):
        calls = []

        def encrypt_password(password, role, algorithm):
            calls.append((password, role, algorithm))
            return b"SCRAM-SHA-256$4096:salt$stored:server"

        db = types.SimpleNamespace(info=types.SimpleNamespace(encoding="utf-8"),
                                   pgconn=types.SimpleNamespace(encrypt_password=encrypt_password))
        verifier = self.bootstrap._scram_verifier(db, "ai_migrator", "sensitive-password")
        self.assertEqual(calls, [(b"sensitive-password", b"ai_migrator", b"scram-sha-256")])
        self.assertTrue(verifier.startswith("SCRAM-SHA-256$"))
        self.assertNotIn("sensitive-password", verifier)

    def test_non_scram_verifier_is_rejected(self):
        db = types.SimpleNamespace(info=types.SimpleNamespace(encoding="utf-8"),
                                   pgconn=types.SimpleNamespace(encrypt_password=lambda *_: b"plain-password"))
        with self.assertRaises(ValueError):
            self.bootstrap._scram_verifier(db, "ai_migrator", "plain-password")

    def test_new_secret_is_written_once_without_printing_password(self):
        client = FakeSecrets()
        values = self.bootstrap._secret_values(client, "arn:test", ("MIGRATION_DATABASE_URL",), self.host)
        self.assertEqual(set(values), {"MIGRATION_DATABASE_URL"})
        self.assertEqual(len(client.puts), 1)
        self.assertEqual(json.loads(client.puts[0]["SecretString"]), values)

    def test_existing_secret_is_never_overwritten(self):
        value = self.bootstrap._dsn("ai_migrator", "retained", self.host)
        client = FakeSecrets({"MIGRATION_DATABASE_URL": value})
        self.assertEqual(self.bootstrap._secret_values(client, "arn:test", ("MIGRATION_DATABASE_URL",), self.host),
                         {"MIGRATION_DATABASE_URL": value})
        self.assertEqual(client.puts, [])

    def test_existing_secret_with_other_values_is_rejected(self):
        value = self.bootstrap._dsn("ai_migrator", "retained", self.host)
        client = FakeSecrets({"MIGRATION_DATABASE_URL": value, "OTHER_KEY": "must-stay"})
        with self.assertRaises(ValueError):
            self.bootstrap._secret_values(client, "arn:test", ("MIGRATION_DATABASE_URL",), self.host)
        self.assertEqual(client.puts, [])

    def test_wrong_database_aborts_before_network_or_secret_write(self):
        with self.assertRaises(ValueError):
            self.bootstrap.bootstrap({"AI_DATABASE_HOST": self.host, "AI_DATABASE_NAME": "ai_db_staging",
                                      "ADMIN_USERNAME": "ai_admin"}, FakeSecrets(), connect=lambda **_: None)


if __name__ == "__main__":
    unittest.main()
