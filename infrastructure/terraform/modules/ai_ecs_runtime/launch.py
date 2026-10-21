"""Preflight injected DSNs without logging values, then exec the pinned image launcher."""
import os
from pathlib import Path
import sys
from urllib.parse import parse_qs, urlsplit


DSNS = {
    "chat": ("DATABASE_URL", "ai_chat_service"),
    "profiler": ("PROFILING_DATABASE_URL", "ai_profiler_service"),
    "search": ("SEARCH_CATALOG_DATABASE_URL", "ai_search_service"),
    "migrate": ("MIGRATION_DATABASE_URL", "ai_migrator"),
    "catalog": ("CATALOG_SYNC_DATABASE_URL", "ai_catalog_sync"),
}


def validate_connection(role, env):
    name, user = DSNS[role]
    parsed = urlsplit(env[name].replace("postgresql+psycopg://", "postgresql://"))
    query = parse_qs(parsed.query, keep_blank_values=True)
    if (parsed.scheme != "postgresql" or parsed.hostname != env["AI_DATABASE_HOST"]
            or parsed.path != "/" + env["AI_DATABASE_NAME"] or parsed.username != user
            or (parsed.port or 5432) != 5432
            or any(key in query for key in ("host", "hostaddr", "port", "dbname", "user", "service"))
            or query.get("sslmode", [env.get("PGSSLMODE")]) != ["verify-full"]
            or query.get("sslrootcert", [env.get("PGSSLROOTCERT")]) != ["/shared/tls/rds-ca.pem"]):
        raise ValueError("AI_DATABASE_TLS_OR_IDENTITY_REJECTED")


def main():
    role = sys.argv[1]
    validate_connection(role, os.environ)
    if not Path("/shared/tls/rds-ca.pem").is_file():
        raise ValueError("AI_RDS_CA_REQUIRED")
    if role == "migrate":
        command = ["python", "-m", "integration.migrate_single_server", "--database", os.environ["AI_DATABASE_NAME"], "--emmet"]
    elif role == "catalog":
        command = ["python", "-m", "integration.search_sync", "--export-url", os.environ["BACKEND_EXPORT_URL"],
                   "--categories-url", os.environ["BACKEND_CATEGORIES_URL"], "--apply"]
    else:
        command = ["python", "-m", "integration.containers.entrypoint", role]
    os.execvp(command[0], command)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print("AI_PREFLIGHT_FAILED:" + type(exc).__name__, file=sys.stderr)
        raise SystemExit(1) from None
