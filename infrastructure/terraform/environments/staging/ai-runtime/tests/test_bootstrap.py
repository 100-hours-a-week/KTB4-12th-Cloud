"""AWS·DB 호출 없이 스테이징 AI DB 부트스트랩의 안전 계약을 검증한다(가짜 psycopg·boto3 사용)."""
import configparser
import importlib.util
import io
import json
import pathlib
import sys
import types
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]


class Identifier:
    def __init__(self, name):
        self.name = name

    def __str__(self):
        return '"' + self.name + '"'


class Literal:
    def __init__(self, value):
        self.value = value

    def __str__(self):
        return "'" + str(self.value) + "'"


class SQL:
    def __init__(self, text):
        self.text = text

    def format(self, *parts):
        return SQL(self.text.format(*[str(p) for p in parts]))

    def __str__(self):
        return self.text


def _stub_modules():
    psycopg = types.ModuleType("psycopg")
    psycopg.connect = None
    psycopg_sql = types.ModuleType("psycopg.sql")
    psycopg_sql.SQL, psycopg_sql.Identifier, psycopg_sql.Literal = SQL, Identifier, Literal
    psycopg.sql = psycopg_sql
    sys.modules.setdefault("psycopg", psycopg)
    sys.modules.setdefault("psycopg.sql", psycopg_sql)
    sys.modules.setdefault("boto3", types.ModuleType("boto3"))


_stub_modules()
SPEC = importlib.util.spec_from_file_location("staging_bootstrap", ROOT / "bootstrap.py")
bs = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(bs)

HOST = "ai.example.ap-northeast-2.rds.amazonaws.com"
ENV = {
    "AI_DATABASE_HOST": HOST, "AI_DATABASE_NAME": "ai_db_staging", "ADMIN_USERNAME": "ai_admin",
    "ADMIN_PASSWORD": "admin-secret-pw-12345", "RDS_CA_PEM": "-----BEGIN CERTIFICATE-----\nsynthetic\n-----END CERTIFICATE-----",
    "APP_SECRET_ARN": "arn:app", "MIGRATION_SECRET_ARN": "arn:mig", "CATALOG_SECRET_ARN": "arn:cat",
}


class FakeClient:
    def __init__(self, stored=None):
        self.stored = dict(stored or {})
        self.puts = []

    def describe_secret(self, SecretId):
        return {"VersionIdsToStages": {"v1": ["AWSCURRENT"]} if SecretId in self.stored else {}}

    def get_secret_value(self, SecretId):
        return {"SecretString": self.stored[SecretId]}

    def put_secret_value(self, SecretId, SecretString):
        self.puts.append(SecretId)
        self.stored[SecretId] = SecretString


class Cursor:
    def __init__(self, row):
        self.row = row

    def fetchone(self):
        return self.row


class FakeServer:
    def __init__(self, migrated=False, tables=0, vector=1, roles=None, dbname="ai_db_staging"):
        self.migrated, self.tables, self.vector = migrated, tables, vector
        self.roles = dict(roles or {})
        self.dbname = dbname
        self.statements = []
        self.connections = []

    def connect(self, **kwargs):
        self.connections.append(kwargs)
        return FakeDB(self, kwargs)


class FakeDB:
    info = types.SimpleNamespace(encoding="utf-8")
    pgconn = types.SimpleNamespace(encrypt_password=lambda password, role, algorithm: b"SCRAM-SHA-256$4096:c2FsdA==$verifier")

    def __init__(self, server, kwargs):
        self.server, self.kwargs = server, kwargs

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, query, params=None):
        text = str(query)
        self.server.statements.append(text)
        server = self.server
        if text == "SELECT current_database(),current_user":
            return Cursor((server.dbname, self.kwargs["user"]))
        if "to_regclass" in text:
            return Cursor(("alembic_version" if server.migrated else None,))
        if "information_schema.tables" in text:
            return Cursor((server.tables,))
        if "pg_available_extensions" in text:
            return Cursor((server.vector,))
        if "FROM pg_roles" in text:
            return Cursor(server.roles.get(params[0]))
        if text == "SELECT current_user":
            return Cursor((self.kwargs["user"],))
        if text.startswith("CREATE ROLE"):
            role = text.split('"')[1]
            server.roles[role] = (True, False, False, False)
        return Cursor(None)


def run_bootstrap(server=None, client=None, environ=None):
    server = server or FakeServer()
    client = client or FakeClient()
    result = bs.bootstrap(environ or ENV, client, server.connect)
    return result, server, client


def secret_passwords(client):
    values = {}
    for arn in ("arn:app", "arn:mig", "arn:cat"):
        values.update(json.loads(client.stored[arn]))
    return {bs.ROLES[key]: bs._password(dsn, bs.ROLES[key], HOST) for key, dsn in values.items()}


class DsnTests(unittest.TestCase):
    def test_dsn_uses_literal_ca_path_and_round_trips_through_config_parser(self):
        dsn = bs._dsn("ai_migrator", "abcDEF-123_xyz", HOST)
        self.assertEqual(dsn, f"postgresql://ai_migrator:abcDEF-123_xyz@{HOST}:5432/ai_db_staging"
                              "?sslmode=verify-full&sslrootcert=/shared/tls/rds-ca.pem")
        self.assertNotIn("%", dsn)
        parser = configparser.ConfigParser()
        parser.add_section("alembic")
        parser.set("alembic", "sqlalchemy.url", dsn)
        self.assertEqual(parser.get("alembic", "sqlalchemy.url"), dsn)

    def test_profiler_dsn_uses_the_psycopg_scheme(self):
        self.assertTrue(bs._dsn("ai_profiler_service", "pw-1", HOST, profiler=True).startswith("postgresql+psycopg://"))

    def test_unsafe_passwords_are_rejected(self):
        for password in ("", "has%percent", "slash/inside", "space here", "colon:inside", "at@sign"):
            with self.assertRaises(ValueError, msg=repr(password)):
                bs._dsn("ai_migrator", password, HOST)

    def test_password_is_recovered_only_from_the_expected_identity(self):
        dsn = bs._dsn("ai_chat_service", "pw-OK_1", HOST)
        self.assertEqual(bs._password(dsn, "ai_chat_service", HOST), "pw-OK_1")
        bad = {
            "other role": lambda d: d.replace("ai_chat_service:", "ai_migrator:"),
            "other host": lambda d: d.replace(HOST, "other.rds.amazonaws.com"),
            "other database": lambda d: d.replace("ai_db_staging", "ai_db_production"),
            "weaker tls": lambda d: d.replace("verify-full", "require"),
            "extra query": lambda d: d + "&application_name=x",
            "other ca path": lambda d: d.replace("/shared/tls/rds-ca.pem", "/tmp/ca.pem"),
            "percent encoded ca path": lambda d: d.replace("/shared/tls/rds-ca.pem", "%2Fshared%2Ftls%2Frds-ca.pem"),
        }
        for name, change in bad.items():
            with self.assertRaises(ValueError, msg=name):
                bs._password(change(dsn), "ai_chat_service", HOST)


class BootstrapTests(unittest.TestCase):
    def test_fresh_run_creates_roles_secrets_and_extension_in_order(self):
        result, server, client = run_bootstrap()
        self.assertEqual(result, {"database": "ai_db_staging", "vector_ready": True, "roles_verified": sorted(bs.ROLES.values())})
        self.assertEqual(client.puts, ["arn:app", "arn:mig", "arn:cat"])
        self.assertEqual(set(json.loads(client.stored["arn:app"])), set(bs.SECRET_KEYS["APP_SECRET_ARN"]))
        self.assertEqual(set(json.loads(client.stored["arn:mig"])), {"MIGRATION_DATABASE_URL"})
        self.assertEqual(set(json.loads(client.stored["arn:cat"])), {"CATALOG_SYNC_DATABASE_URL"})
        statements = server.statements
        extension = statements.index("CREATE EXTENSION IF NOT EXISTS vector")
        creates = [i for i, s in enumerate(statements) if s.startswith("CREATE ROLE")]
        self.assertEqual(len(creates), 5)
        self.assertTrue(all(extension < i for i in creates))
        self.assertTrue(all("NOSUPERUSER NOCREATEDB NOCREATEROLE" in statements[i] for i in creates))
        self.assertIn('GRANT CREATE ON DATABASE "ai_db_staging" TO "ai_migrator"', statements)
        self.assertIn("GRANT USAGE,CREATE ON SCHEMA public TO ai_migrator", statements)

    def test_every_generated_dsn_is_config_safe_and_role_specific(self):
        _, _, client = run_bootstrap()
        for arn in ("arn:app", "arn:mig", "arn:cat"):
            for key, dsn in json.loads(client.stored[arn]).items():
                self.assertNotIn("%", dsn)
                self.assertTrue(bs._password(dsn, bs.ROLES[key], HOST))
        self.assertEqual(len(set(secret_passwords(client).values())), 5)

    def test_plaintext_passwords_never_reach_sql_and_no_statement_mentions_production(self):
        _, server, client = run_bootstrap()
        for password in secret_passwords(client).values():
            self.assertFalse(any(password in s for s in server.statements))
        self.assertFalse(any("production" in s for s in server.statements))
        self.assertFalse(any(ENV["ADMIN_PASSWORD"] in s for s in server.statements))

    def test_every_role_login_is_verified_with_its_own_password(self):
        _, server, client = run_bootstrap()
        passwords = secret_passwords(client)
        logins = [c for c in server.connections if c["user"] != "ai_admin"]
        self.assertEqual({c["user"] for c in logins}, set(bs.ROLES.values()))
        for connection in logins:
            self.assertEqual(connection["password"], passwords[connection["user"]])
            self.assertEqual((connection["dbname"], connection["sslmode"]), ("ai_db_staging", "verify-full"))

    def test_rerun_is_refused_once_migrated_or_when_tables_exist(self):
        for server in (FakeServer(migrated=True), FakeServer(tables=3)):
            client = FakeClient()
            with self.assertRaises(ValueError):
                run_bootstrap(server, client)
            self.assertEqual(client.puts, [])

    def test_missing_pgvector_is_refused_before_any_secret_is_written(self):
        client = FakeClient()
        with self.assertRaises(ValueError):
            run_bootstrap(FakeServer(vector=0), client)
        self.assertEqual(client.puts, [])

    def test_existing_valid_secrets_are_reused_not_overwritten(self):
        first_client = run_bootstrap()[2]
        client = FakeClient(first_client.stored)
        server = FakeServer()
        run_bootstrap(server, client)
        self.assertEqual(client.puts, [])
        self.assertEqual(secret_passwords(client), secret_passwords(first_client))

    def test_existing_secret_with_unexpected_keys_is_never_overwritten(self):
        client = FakeClient({"arn:app": json.dumps({"DATABASE_URL": bs._dsn("ai_chat_service", "pw-1", HOST), "OTHER": "x"})})
        with self.assertRaises(ValueError):
            run_bootstrap(FakeServer(), client)
        self.assertEqual(client.puts, [])

    def test_existing_percent_encoded_secret_is_refused(self):
        legacy = (f"postgresql://ai_migrator:pw-1@{HOST}:5432/ai_db_staging"
                  "?sslmode=verify-full&sslrootcert=%2Fshared%2Ftls%2Frds-ca.pem")
        client = FakeClient({"arn:mig": json.dumps({"MIGRATION_DATABASE_URL": legacy})})
        with self.assertRaises(ValueError):
            run_bootstrap(FakeServer(), client)

    def test_existing_role_with_unexpected_privileges_is_refused(self):
        with self.assertRaises(ValueError):
            run_bootstrap(FakeServer(roles={"ai_chat_service": (True, True, False, False)}))

    def test_wrong_database_or_administrator_or_host_is_refused(self):
        for key, value in (("AI_DATABASE_NAME", "ai_db_production"), ("ADMIN_USERNAME", "postgres"), ("AI_DATABASE_HOST", "evil.example.com")):
            with self.assertRaises(ValueError, msg=key):
                run_bootstrap(environ={**ENV, key: value})

    def test_connection_to_a_different_database_is_refused(self):
        with self.assertRaises(ValueError):
            run_bootstrap(FakeServer(dbname="ai_db_production"))

    def test_certificate_file_is_removed_after_the_run(self):
        _, server, _ = run_bootstrap()
        paths = {c["sslrootcert"] for c in server.connections}
        self.assertEqual(len(paths), 1)
        self.assertFalse(pathlib.Path(paths.pop()).exists())


class MainTests(unittest.TestCase):
    def test_success_prints_only_non_secret_summary(self):
        out, err = io.StringIO(), io.StringIO()
        server, client = FakeServer(), FakeClient()
        code = bs.main(ENV, lambda: client, server.connect, out, err)
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out.getvalue()), {"database": "ai_db_staging", "vector_ready": True, "roles_verified": sorted(bs.ROLES.values())})
        self.assertNotIn("postgresql://", out.getvalue())
        self.assertEqual(err.getvalue(), "")

    def test_failure_prints_the_exception_type_only(self):
        def leaking_connect(**kwargs):
            raise RuntimeError(f"could not connect password={kwargs['password']} host={kwargs['host']}")
        out, err = io.StringIO(), io.StringIO()
        code = bs.main(ENV, FakeClient, leaking_connect, out, err)
        self.assertEqual(code, 1)
        self.assertEqual(out.getvalue(), "")
        self.assertEqual(err.getvalue(), "AI_DB_BOOTSTRAP_FAILED:RuntimeError\n")
        self.assertNotIn(ENV["ADMIN_PASSWORD"], err.getvalue())
        self.assertNotIn(HOST, err.getvalue())


class SourceContractTests(unittest.TestCase):
    def test_source_has_no_production_database_or_percent_encoded_certificate_path(self):
        text = (ROOT / "bootstrap.py").read_text(encoding="utf-8")
        self.assertNotIn("ai_db_production", text)
        self.assertNotIn("%2F", text.replace("'%2F' ", ""))
        self.assertIn('DATABASE = "ai_db_staging"', text)


if __name__ == "__main__":
    unittest.main()
