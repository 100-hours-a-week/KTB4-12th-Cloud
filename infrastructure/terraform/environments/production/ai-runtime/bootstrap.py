"""One-time production AI DB role/credential bootstrap; never starts an app."""

import json
import os
import secrets
import sys
import tempfile
from pathlib import Path
from urllib.parse import quote, urlsplit, parse_qs, unquote

import boto3
import psycopg
from psycopg import sql


DATABASE = "ai_db_production"
ROLES = {
    "DATABASE_URL": "ai_chat_service",
    "PROFILING_DATABASE_URL": "ai_profiler_service",
    "SEARCH_CATALOG_DATABASE_URL": "ai_search_service",
    "MIGRATION_DATABASE_URL": "ai_migrator",
    "CATALOG_SYNC_DATABASE_URL": "ai_catalog_sync",
}
SECRET_KEYS = {
    "APP_SECRET_ARN": ("DATABASE_URL", "PROFILING_DATABASE_URL", "SEARCH_CATALOG_DATABASE_URL"),
    "MIGRATION_SECRET_ARN": ("MIGRATION_DATABASE_URL",),
    "CATALOG_SECRET_ARN": ("CATALOG_SYNC_DATABASE_URL",),
}


def _dsn(role, password, host, *, profiler=False):
    scheme = "postgresql+psycopg" if profiler else "postgresql"
    return (f"{scheme}://{role}:{quote(password, safe='')}@{host}:5432/{DATABASE}"
            "?sslmode=verify-full&sslrootcert=%2Fshared%2Ftls%2Frds-ca.pem")


def _password(dsn, role, host):
    parsed = urlsplit(dsn.replace("postgresql+psycopg://", "postgresql://"))
    query = parse_qs(parsed.query)
    if (parsed.scheme != "postgresql" or parsed.username != role or parsed.hostname != host
            or parsed.port != 5432 or parsed.path != f"/{DATABASE}"
            or query.get("sslmode") != ["verify-full"]
            or query.get("sslrootcert") != ["/shared/tls/rds-ca.pem"]):
        raise ValueError("Existing DB secret has an unexpected identity or TLS policy")
    return unquote(parsed.password or "")


def _scram_verifier(db, role, password):
    encoding = db.info.encoding
    verifier = db.pgconn.encrypt_password(password.encode(encoding), role.encode(encoding),
                                          b"scram-sha-256")
    if not verifier.startswith(b"SCRAM-SHA-256$") or password.encode(encoding) in verifier:
        raise ValueError("Could not create a SCRAM password verifier")
    return verifier.decode("ascii")


def _secret_values(client, arn, keys, host):
    versions = client.describe_secret(SecretId=arn).get("VersionIdsToStages", {})
    if any("AWSCURRENT" in stages for stages in versions.values()):
        response = client.get_secret_value(SecretId=arn)
        values = json.loads(response["SecretString"])
        if set(values) != set(keys):
            raise ValueError("Existing DB secret has unexpected keys; refuse to overwrite")
        for key in keys:
            if not _password(values[key], ROLES[key], host):
                raise ValueError("Existing DB secret has an empty password")
        return values
    values = {key: _dsn(ROLES[key], secrets.token_urlsafe(48), host,
                        profiler=key == "PROFILING_DATABASE_URL") for key in keys}
    client.put_secret_value(SecretId=arn, SecretString=json.dumps(values, separators=(",", ":")))
    return values


def bootstrap(environ, client, connect=psycopg.connect):
    host = environ["AI_DATABASE_HOST"]
    if (environ["AI_DATABASE_NAME"] != DATABASE or environ["ADMIN_USERNAME"] != "ai_admin"
            or not host.endswith(".rds.amazonaws.com")):
        raise ValueError("Wrong production database identity")
    # 인증서 파일은 Task 안에서만 생성하고 종료 시 제거한다. 비밀번호는 파일·로그에 기록하지 않는다.
    with tempfile.TemporaryDirectory() as directory:
        ca = Path(directory) / "rds-ca.pem"
        ca.write_text(environ["RDS_CA_PEM"])
        admin = dict(host=host, dbname=DATABASE, user="ai_admin", password=environ["ADMIN_PASSWORD"],
                     port=5432, sslmode="verify-full", sslrootcert=str(ca), connect_timeout=10)
        with connect(**admin) as db:
            name, user = db.execute("SELECT current_database(),current_user").fetchone()
            if (name, user) != (DATABASE, "ai_admin"):
                raise ValueError("Connected to wrong database or role")
            if db.execute("SELECT to_regclass('public.alembic_version')").fetchone()[0] is not None:
                raise ValueError("Database is already migrated; bootstrap must not rerun")
            if db.execute("SELECT count(*) FROM information_schema.tables WHERE table_schema NOT IN ('pg_catalog','information_schema')").fetchone()[0]:
                raise ValueError("Database contains tables; bootstrap must not rerun")
            if db.execute("SELECT count(*) FROM pg_available_extensions WHERE name='vector'").fetchone()[0] != 1:
                raise ValueError("RDS pgvector extension is unavailable")
        values = {}
        for name, keys in SECRET_KEYS.items():
            values.update(_secret_values(client, environ[name], keys, host))
        with connect(**admin) as db:
            # pgvector는 RDS 관리자 권한으로 먼저 설치한다. Alembic의 IF NOT EXISTS는 이를 재사용한다.
            db.execute("CREATE EXTENSION IF NOT EXISTS vector")
            for key, role in ROLES.items():
                password = _password(values[key], role, host)
                existing = db.execute("SELECT rolcanlogin,rolsuper,rolcreatedb,rolcreaterole FROM pg_roles WHERE rolname=%s",
                                      (role,)).fetchone()
                if existing is None:
                    db.execute(sql.SQL("CREATE ROLE {} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE PASSWORD {}").format(
                        sql.Identifier(role), sql.Literal(_scram_verifier(db, role, password))))
                elif existing != (True, False, False, False):
                    raise ValueError("Existing role has unexpected privileges")
                db.execute(sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(
                    sql.Identifier(DATABASE), sql.Identifier(role)))
            db.execute("GRANT CREATE ON DATABASE ai_db_production TO ai_migrator")
            db.execute("GRANT USAGE,CREATE ON SCHEMA public TO ai_migrator")
        for key, role in ROLES.items():
            with connect(**(admin | {"user": role, "password": _password(values[key], role, host)})) as db:
                if db.execute("SELECT current_user").fetchone()[0] != role:
                    raise ValueError("Role login verification failed")
    return {"database": DATABASE, "roles_verified": sorted(ROLES.values()), "vector_ready": True}


if __name__ == "__main__":
    try:
        result = bootstrap(os.environ, boto3.client("secretsmanager", region_name="ap-northeast-2"))
        print(json.dumps(result))
    except Exception as exc:
        # Driver/AWS exception text can contain endpoints or credentials; emit type only.
        print("AI_DB_BOOTSTRAP_FAILED:" + type(exc).__name__, file=sys.stderr)
        raise SystemExit(1) from None
