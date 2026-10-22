"""One-time recovery of the exposed production AI migration credential."""

import configparser
import json
import os
import secrets
import sys
import tempfile
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import boto3
import psycopg
from psycopg import sql


DATABASE = "ai_db_production"
ROLE = "ai_migrator"
CA_PATH = "/shared/tls/rds-ca.pem"


def _old_password(dsn, host):
    parsed = urlsplit(dsn)
    query = parse_qs(parsed.query)
    if (parsed.scheme != "postgresql" or parsed.username != ROLE or parsed.hostname != host
            or parsed.port != 5432 or parsed.path != f"/{DATABASE}"
            or query.get("sslmode") != ["verify-full"]
            or query.get("sslrootcert") != [CA_PATH]
            or set(query) != {"sslmode", "sslrootcert"} or not parsed.password):
        raise ValueError("Unexpected migration Secret identity or TLS policy")
    return parsed.password


def _new_dsn(host, password):
    # token_urlsafe has no '%' characters; literal CA path avoids ConfigParser interpolation.
    if not password or not all(c.isalnum() or c in "-_" for c in password):
        raise ValueError("Generated password is not URL/config safe")
    dsn = (f"postgresql://{ROLE}:{password}@{host}:5432/{DATABASE}"
           f"?sslmode=verify-full&sslrootcert={CA_PATH}")
    parser = configparser.ConfigParser()
    parser.add_section("alembic")
    parser.set("alembic", "sqlalchemy.url", dsn)
    if parser.get("alembic", "sqlalchemy.url") != dsn:
        raise ValueError("Migration URL does not round-trip through ConfigParser")
    return dsn


def _scram_verifier(db, password):
    encoding = db.info.encoding
    verifier = db.pgconn.encrypt_password(password.encode(encoding), ROLE.encode(encoding),
                                          b"scram-sha-256")
    if not verifier.startswith(b"SCRAM-SHA-256$") or password.encode(encoding) in verifier:
        raise ValueError("Could not create SCRAM password verifier")
    return verifier.decode("ascii")


def rotate(environ, client, connect=psycopg.connect):
    host = environ["AI_DATABASE_HOST"]
    if (environ["AI_DATABASE_NAME"] != DATABASE or environ["ADMIN_USERNAME"] != "ai_admin"
            or not host.endswith(".rds.amazonaws.com")):
        raise ValueError("Wrong production database identity")
    arn = environ["MIGRATION_SECRET_ARN"]
    response = client.get_secret_value(SecretId=arn, VersionStage="AWSCURRENT")
    values = json.loads(response["SecretString"])
    if set(values) != {"MIGRATION_DATABASE_URL"}:
        raise ValueError("Unexpected migration Secret keys")
    old_password = _old_password(values["MIGRATION_DATABASE_URL"], host)
    new_password = secrets.token_urlsafe(48)
    if new_password == old_password:
        raise ValueError("Generated password matches exposed credential")
    new_dsn = _new_dsn(host, new_password)

    with tempfile.TemporaryDirectory() as directory:
        ca = Path(directory) / "rds-ca.pem"
        ca.write_text(environ["RDS_CA_PEM"])
        admin = dict(host=host, dbname=DATABASE, user="ai_admin", password=environ["ADMIN_PASSWORD"],
                     port=5432, sslmode="verify-full", sslrootcert=str(ca), connect_timeout=10)
        with connect(**admin) as db:
            if db.execute("SELECT current_database(),current_user").fetchone() != (DATABASE, "ai_admin"):
                raise ValueError("Connected to wrong database or administrator")
            attributes = db.execute("SELECT rolcanlogin,rolsuper,rolcreatedb,rolcreaterole FROM pg_roles WHERE rolname=%s",
                                    (ROLE,)).fetchone()
            if attributes != (True, False, False, False):
                raise ValueError("Migration role is missing or has unexpected privileges")
            # Only the SCRAM verifier reaches PostgreSQL SQL/logging, never the plaintext password.
            db.execute(sql.SQL("ALTER ROLE {} PASSWORD {}").format(
                sql.Identifier(ROLE), sql.Literal(_scram_verifier(db, new_password))))
        print("MIGRATION_ROLE_ROTATED", flush=True)

        # The AI service is dormant; update its Secret only after the DB commit succeeds.
        client.put_secret_value(SecretId=arn, SecretString=json.dumps({"MIGRATION_DATABASE_URL": new_dsn},
                                                                    separators=(",", ":")))
        print("MIGRATION_SECRET_UPDATED", flush=True)
        with connect(**(admin | {"user": ROLE, "password": new_password})) as db:
            if db.execute("SELECT current_user").fetchone()[0] != ROLE:
                raise ValueError("New migration login verification failed")
    return {"migration_role_rotated": True, "secret_updated": True, "new_login_verified": True}


if __name__ == "__main__":
    try:
        print(json.dumps(rotate(os.environ, boto3.client("secretsmanager", region_name="ap-northeast-2"))))
    except Exception as exc:
        # Never print driver/AWS exception text: it can contain a DSN or password.
        print("AI_MIGRATION_ROTATION_FAILED:" + type(exc).__name__, file=sys.stderr)
        raise SystemExit(1) from None
