"""Offline contract tests for one-time exposed migration credential recovery."""

import configparser
import importlib.util
import json
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch


def load_rotation():
    boto3 = types.ModuleType("boto3")
    psycopg = types.ModuleType("psycopg")
    psycopg.connect = None
    psycopg.sql = types.SimpleNamespace()
    with patch.dict(sys.modules, {"boto3": boto3, "psycopg": psycopg}):
        spec = importlib.util.spec_from_file_location("ai_migration_rotation", Path(__file__).resolve().parents[1] / "rotate_migration.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    return module


class RotationContractTests(unittest.TestCase):
    def setUp(self):
        self.rotation = load_rotation()
        self.host = "ai-db.example.rds.amazonaws.com"

    def test_old_encoded_ca_path_is_recognized_but_new_url_is_interpolation_safe(self):
        old = (f"postgresql://ai_migrator:old-secret@{self.host}:5432/ai_db_production"
               "?sslmode=verify-full&sslrootcert=%2Fshared%2Ftls%2Frds-ca.pem")
        self.assertEqual(self.rotation._old_password(old, self.host), "old-secret")
        parser = configparser.ConfigParser()
        parser.add_section("alembic")
        with self.assertRaises(ValueError):
            parser.set("alembic", "sqlalchemy.url", old)
        new = self.rotation._new_dsn(self.host, "new-URL-safe_password")
        parser.set("alembic", "sqlalchemy.url", new)
        self.assertEqual(parser.get("alembic", "sqlalchemy.url"), new)
        self.assertEqual(self.rotation._old_password(new, self.host), "new-URL-safe_password")

    def test_unexpected_identity_or_secret_keys_are_rejected(self):
        old = self.rotation._new_dsn(self.host, "old-password")
        with self.assertRaises(ValueError):
            self.rotation._old_password(old, "different.rds.amazonaws.com")

        class Client:
            def get_secret_value(self, **_):
                return {"SecretString": json.dumps({"MIGRATION_DATABASE_URL": old, "OTHER": "bad"})}

        environ = {"AI_DATABASE_HOST": self.host, "AI_DATABASE_NAME": "ai_db_production",
                   "ADMIN_USERNAME": "ai_admin", "MIGRATION_SECRET_ARN": "arn:test"}
        with self.assertRaises(ValueError):
            self.rotation.rotate(environ, Client(), connect=lambda **_: self.fail("must not connect"))

    def test_scram_verifier_never_contains_plaintext(self):
        calls = []

        def encrypt(password, role, algorithm):
            calls.append((password, role, algorithm))
            return b"SCRAM-SHA-256$4096:salt$stored:server"

        db = types.SimpleNamespace(info=types.SimpleNamespace(encoding="utf-8"),
                                   pgconn=types.SimpleNamespace(encrypt_password=encrypt))
        verifier = self.rotation._scram_verifier(db, "new-secret")
        self.assertTrue(verifier.startswith("SCRAM-SHA-256$"))
        self.assertNotIn("new-secret", verifier)
        self.assertEqual(calls, [(b"new-secret", b"ai_migrator", b"scram-sha-256")])

    def test_db_role_commits_before_secret_write_and_new_login_is_verified(self):
        rotation = self.rotation
        old = rotation._new_dsn(self.host, "old-password")
        events = []

        class Client:
            def get_secret_value(self, **_):
                return {"SecretString": json.dumps({"MIGRATION_DATABASE_URL": old})}

            def put_secret_value(self, **kwargs):
                new = json.loads(kwargs["SecretString"])["MIGRATION_DATABASE_URL"]
                self_new_password = rotation._old_password(new, self.host)
                self.assertEqual(self_new_password, "new-password")
                events.append("secret_write")

        class Statement:
            def __init__(self, value):
                self.value = value

            def format(self, *args):
                return self.value.format(*args)

        rotation.sql = types.SimpleNamespace(SQL=Statement, Identifier=lambda x: x, Literal=lambda x: x)

        class Db:
            info = types.SimpleNamespace(encoding="utf-8")
            pgconn = types.SimpleNamespace(encrypt_password=lambda *_: b"SCRAM-SHA-256$4096:salt$stored:server")

            def __init__(self, user):
                self.user = user

            def __enter__(self):
                return self

            def __exit__(self, *_):
                if self.user == "ai_admin":
                    events.append("db_commit")

            def execute(self, statement, _params=None):
                if statement == "SELECT current_database(),current_user":
                    result = ("ai_db_production", "ai_admin")
                elif statement.startswith("SELECT rolcanlogin"):
                    result = (True, False, False, False)
                elif statement.startswith("ALTER ROLE"):
                    self.assertNotIn("new-password", statement)
                    events.append("alter_scram")
                    result = None
                elif statement == "SELECT current_user":
                    result = ("ai_migrator",)
                    events.append("new_login")
                else:
                    raise AssertionError("unexpected SQL")
                return types.SimpleNamespace(fetchone=lambda: result)

        # Bind test assertions into the small fakes without exposing either password.
        Client.assertEqual = self.assertEqual
        Client.host = self.host
        Db.assertNotIn = self.assertNotIn

        def connect(**kwargs):
            if kwargs["user"] == "ai_migrator":
                self.assertEqual(kwargs["password"], "new-password")
            return Db(kwargs["user"])

        environ = {"AI_DATABASE_HOST": self.host, "AI_DATABASE_NAME": "ai_db_production",
                   "ADMIN_USERNAME": "ai_admin", "ADMIN_PASSWORD": "admin-test",
                   "MIGRATION_SECRET_ARN": "arn:test", "RDS_CA_PEM": "test-ca"}
        with patch.object(rotation.secrets, "token_urlsafe", return_value="new-password"):
            result = rotation.rotate(environ, Client(), connect=connect)
        self.assertEqual(events, ["alter_scram", "db_commit", "secret_write", "new_login"])
        self.assertTrue(all(result.values()))


if __name__ == "__main__":
    unittest.main()
