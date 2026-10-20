#!/usr/bin/env python3
"""승인한 격리 리허설 1회. 기존 FE/Source/후보 사용, 실패 시 Writer 경계별 복구."""
from datetime import datetime, timezone
import fcntl
import hashlib
import http.client
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import threading
import time
from urllib.parse import urlsplit

TASK = "arn:aws:dms:ap-northeast-2:721744297924:task:OU5M6NHIS5BOHGL7KSXRWHCKNY"
SOURCE_ENDPOINT = "arn:aws:dms:ap-northeast-2:721744297924:endpoint:FU25AATK25F45IHKJXYEN67M6U"
TARGET_ENDPOINT = "arn:aws:dms:ap-northeast-2:721744297924:endpoint:DFPTJDIP5VCNRLONAJLELMGEB4"
HOST = "seonjalal-v2-migration-rehearsal.cbqckkusw4ke.ap-northeast-2.rds.amazonaws.com"
SOURCE_SCHEMA = "seonjalal_develop"
TARGET_SCHEMA = "seonjalal_cutover_20261019"
TABLES = sorted("categories flyway_schema_history friends gift_histories login_email_failure_limits "
                "login_ip_rate_limits product_images product_views products recipient_profiles "
                "recipient_recommended_products term_consents terms user_dislike_categories user_sessions users".split())
ROOT = Path("/opt/seonjalal-cutover-20261019-candidate")
MARKER = ROOT / "cutover-attempt.json"


def require(value, message):
    if not value:
        raise RuntimeError(message)


def call(args, data=None, timeout=30, label="COMMAND"):
    try:
        result = subprocess.run(args, input=data, capture_output=True, timeout=timeout,
                                env=dict(os.environ, AWS_MAX_ATTEMPTS="1"))
    except subprocess.TimeoutExpired:
        raise RuntimeError(label + "_TIMEOUT") from None
    if result.returncode:
        # 비밀번호·SQL 오류 원문·환경 전체는 출력 금지. AWS 오류 종류만 분류.
        match = re.search(rb"An error occurred \(([A-Za-z0-9]+)\)", result.stderr)
        suffix = "_" + match.group(1).decode() if match else ""
        raise RuntimeError(label + "_FAILED" + suffix)
    return result.stdout


def aws(*args):
    return json.loads(call(["aws", "--region", "ap-northeast-2", "--no-cli-pager",
                            "--output", "json", "--cli-connect-timeout", "3",
                            "--cli-read-timeout", "10", *args], timeout=18, label="AWS_" + args[1]))


def identity():
    require(os.geteuid() == 0, "ROOT_REQUIRED")
    addresses = json.loads(call(["ip", "-j", "-4", "address", "show"]))
    require(any(a.get("local") == "172.31.6.212" for interface in addresses
                for a in interface.get("addr_info", [])), "WRONG_SOURCE_HOST")
    row = aws("sts", "get-caller-identity")
    require(row["Account"] == "721744297924" and row["Arn"].startswith(
        "arn:aws:sts::721744297924:assumed-role/seonjalal-v1-develop-role/"), "WRONG_SOURCE_ROLE")


def task(statuses):
    rows = aws("dms", "describe-replication-tasks", "--filters",
               "Name=replication-task-arn,Values=" + TASK)["ReplicationTasks"]
    require(len(rows) == 1, "EXACT_TASK_NOT_FOUND")
    row = rows[0]
    require(row["Status"] in statuses, "DMS_STATUS_" + row["Status"])
    require(row["SourceEndpointArn"] == SOURCE_ENDPOINT and row["TargetEndpointArn"] == TARGET_ENDPOINT
            and row["MigrationType"] == "full-load-and-cdc", "DMS_TARGET_CHANGED")
    rules = json.loads(row["TableMappings"])["rules"]
    selections = [r for r in rules if r["rule-type"] == "selection"]
    transforms = [r for r in rules if r["rule-type"] == "transformation"]
    require(len(rules) == 17 and len(selections) == 16 and len(transforms) == 1, "DMS_RULES_CHANGED")
    require(sorted(r["object-locator"]["table-name"] for r in selections) == TABLES
            and all(r["rule-action"] == "include" and not r.get("filters")
                    and r["object-locator"]["schema-name"] == SOURCE_SCHEMA for r in selections),
            "DMS_SELECTION_CHANGED")
    transform = transforms[0]
    require(transform["rule-target"] == "schema" and transform["rule-action"] == "rename"
            and transform["object-locator"] == {"schema-name": SOURCE_SCHEMA}
            and transform["value"] == TARGET_SCHEMA, "DMS_SCHEMA_CHANGED")
    settings = json.loads(row["ReplicationTaskSettings"])
    require(settings["FullLoadSettings"]["TargetTablePrepMode"] == "DO_NOTHING"
            and settings["ValidationSettings"]["EnableValidation"], "DMS_SETTINGS_CHANGED")
    stats = row["ReplicationTaskStats"]
    require(stats["FullLoadProgressPercent"] == 100 and stats["TablesLoaded"] == 16
            and stats["TablesErrored"] == 0, "DMS_LOAD_NOT_COMPLETE")
    return row


def validation():
    rows = aws("dms", "describe-table-statistics", "--replication-task-arn", TASK)["TableStatistics"]
    require(sorted(r["TableName"] for r in rows) == TABLES, "DMS_VALIDATION_TABLES_CHANGED")
    require(all(r["SchemaName"] == SOURCE_SCHEMA and r["ValidationState"] == "Validated"
                and all(r.get(k, 0) == 0 for k in ("ValidationFailedRecords", "ValidationPendingRecords",
                                                   "ValidationSuspendedRecords")) for r in rows),
            "DMS_VALIDATION_NOT_COMPLETE")


def get(path, port=8081, method="GET"):
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        connection.request(method, path, headers={"Connection": "close"})
        result = connection.getresponse()
        body = result.read(2 * 1024 * 1024)
        return result.status, body
    finally:
        connection.close()


def read_check():
    for path in ("/api/health", "/api/products?sort=POPULAR"):
        status, body = get(path)
        require(status == 200, "FE_READ_NOT_200")
        value = json.loads(body)
        require(value.get("status") == "UP" if path.endswith("health") else
                value.get("error") is None and isinstance(value.get("data", {}).get("products"), list),
                "FE_READ_BUSINESS_ERROR")


class Run:
    def __init__(self, fe, expected):
        self.fe, self.expected = fe, expected
        self.work = None
        self.events, self.samples = [], []
        self.halt = threading.Event()
        self.thread = None
        self.changed = False
        self.fence_attempted = False
        self.target_open_attempted = False
        self.freeze_ns = None

    def event(self, name, **details):
        row = dict(event=name, utc=datetime.now(timezone.utc).isoformat(),
                   monotonic_ns=time.monotonic_ns(), **details)
        self.events.append(row)
        with (self.work / "events.jsonl").open("a") as stream:
            stream.write(json.dumps(row) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        print("EVENT=" + name + " UTC=" + row["utc"], flush=True)

    def source(self, sql):
        return call(["docker", "exec", self.db_id, "sh", "-ec",
            'test -n "$MYSQL_ROOT_PASSWORD"; export MYSQL_PWD="$MYSQL_ROOT_PASSWORD"; '
            'exec mysql --user=root --protocol=SOCKET --batch --skip-column-names '
            '--database=seonjalal_develop --execute="$1"', "sh", sql], label="SOURCE_SQL").decode().strip()

    def target(self, sql):
        # 기존 후보 앱 암호는 inspect→메모리→stdin만 전달. argv/파일에는 저장하지 않음.
        return call(["docker", "exec", "-i", self.db_id, "sh", "-ec",
            'IFS= read -r password; export MYSQL_PWD="$password"; '
            'exec mysql --host="$1" --port=3306 --user=seonjalal_staging_app '
            '--ssl-mode=VERIFY_IDENTITY --ssl-ca="$2" --connect-timeout=5 '
            '--batch --skip-column-names --database=seonjalal_cutover_20261019 --execute="$3"',
            "sh", HOST, self.ca_path, sql], data=(self.password + "\n").encode(),
            label="TARGET_SQL").decode().strip()

    def inventory(self, query):
        value = query("SELECT TABLE_NAME,ENGINE FROM information_schema.tables "
                      "WHERE TABLE_SCHEMA=DATABASE() ORDER BY TABLE_NAME;")
        require(value.splitlines() == [n + "\tInnoDB" for n in TABLES], "TABLE_INVENTORY_CHANGED")
        require(query("SELECT (SELECT COUNT(*) FROM information_schema.triggers WHERE TRIGGER_SCHEMA=DATABASE()),"
                      "(SELECT COUNT(*) FROM information_schema.events WHERE EVENT_SCHEMA=DATABASE());") == "0\t0",
                "UNREVIEWED_TRIGGER_OR_EVENT")

    def fingerprints(self, query, schema):
        sql = "CHECKSUM TABLE " + ",".join("`" + schema + "`.`" + n + "`" for n in TABLES) + " EXTENDED;"
        values = {}
        for line in query(sql).splitlines():
            name, value = line.split("\t")
            require(name.startswith(schema + ".") and value.isdigit(), "INVALID_CHECKSUM")
            values[name[len(schema) + 1:]] = value
        require(sorted(values) == TABLES, "CHECKSUM_TABLE_MISSING")
        return values

    def orphans(self):
        sql = ("SELECT CONSTRAINT_NAME,TABLE_NAME,COLUMN_NAME,REFERENCED_TABLE_SCHEMA,"
               "REFERENCED_TABLE_NAME,REFERENCED_COLUMN_NAME FROM information_schema.KEY_COLUMN_USAGE "
               "WHERE TABLE_SCHEMA=DATABASE() AND REFERENCED_TABLE_NAME IS NOT NULL "
               "ORDER BY TABLE_NAME,CONSTRAINT_NAME,ORDINAL_POSITION;")
        groups = {}
        for line in self.target(sql).splitlines():
            constraint, table, column, schema, parent, parent_column = line.split("\t")
            require(schema == TARGET_SCHEMA and table in TABLES and parent in TABLES
                    and all(re.fullmatch(r"[A-Za-z0-9_]+", x) for x in (constraint, column, parent_column)),
                    "FK_METADATA_CHANGED")
            groups.setdefault((constraint, table, parent), []).append((column, parent_column))
        require(len(groups) == 18, "FK_COUNT_CHANGED")
        for (_, table, parent), columns in groups.items():
            join = " AND ".join("c.`" + c + "`=p.`" + p + "`" for c, p in columns)
            nonnull = " AND ".join("c.`" + c + "` IS NOT NULL" for c, _ in columns)
            query = ("SELECT COUNT(*) FROM `" + table + "` c LEFT JOIN `" + parent + "` p ON " + join
                     + " WHERE " + nonnull + " AND p.`" + columns[0][1] + "` IS NULL;")
            require(self.target(query) == "0", "TARGET_FK_ORPHAN_FOUND")

    def workers(self):
        text = call(["docker", "exec", self.fe_id, "ps", "-o", "pid,args"], label="NGINX_PS").decode()
        return {line.split()[0] for line in text.splitlines() if "nginx: worker process" in line}

    def write_active(self, value):
        # 단일 파일 bind mount의 inode·소유권 보존. rename/chown/chmod 없음.
        descriptor = os.open(self.fe.CONFIG, os.O_RDWR | os.O_NOFOLLOW)
        with os.fdopen(descriptor, "r+b") as stream:
            info = os.fstat(stream.fileno())
            self.fe.validate_config_info(info)
            require((info.st_dev, info.st_ino) == self.original_inode, "ACTIVE_INODE_CHANGED")
            stream.seek(0)
            stream.write(value)
            stream.truncate()
            stream.flush()
            os.fsync(stream.fileno())

    def reload(self, value, event):
        old = self.workers()
        require(old, "NGINX_WORKERS_NOT_FOUND")
        self.changed = True
        self.write_active(value)
        call(["docker", "exec", self.fe_id, "nginx", "-t"], label="NGINX_ACTIVE_TEST")
        call(["docker", "exec", self.fe_id, "nginx", "-s", "reload"], label="NGINX_RELOAD")
        self.event(event + "_reload_requested")
        deadline = time.monotonic() + 25
        while old & self.workers():
            require(time.monotonic() < deadline, "NGINX_DRAIN_TIMEOUT")
            time.sleep(0.25)
        self.event(event + "_old_workers_drained")

    def sample(self):
        while not self.halt.is_set():
            start = time.monotonic_ns()
            try:
                read_check()
                ok = True
            except Exception:
                ok = False
            self.samples.append({"monotonic_ns": start, "pair_ok": ok})
            self.halt.wait(1)

    def preflight(self):
        identity()
        require(not MARKER.exists() and not MARKER.is_symlink(), "PREVIOUS_CUTOVER_ATTEMPT_EXISTS")
        before = self.fe.inspect()
        self.fe.validate(before)
        self.before = before
        self.fe_id, self.candidate_id, self.source_id, self.db_id = [x["Id"] for x in before]
        source, target = [dict(v.split("=", 1) for v in x["Config"]["Env"] if "=" in v) for x in before[2:3] + before[1:2]]
        for environment, host, schema in ((source, "database", SOURCE_SCHEMA), (target, HOST, TARGET_SCHEMA)):
            url = urlsplit(environment.get("DB_URL", "").removeprefix("jdbc:"))
            require(url.hostname == host and url.path == "/" + schema, "APP_DB_CHANGED")
            require(all(environment.get(k) == v for k, v in {
                "SPRING_FLYWAY_ENABLED": "false", "SPRING_SQL_INIT_MODE": "never",
                "SPRING_JPA_HIBERNATE_DDL_AUTO": "validate", "SCHEDULING_ENABLED": "false",
                "AI_PROFILE_SCHEDULING_ENABLED": "false"}.items()), "APP_BACKGROUND_WRITER_CHANGED")
        require(all(source.get(k) and source[k] == target.get(k) for k in (
            "JWT_SECRET_BASE64", "JWT_ISSUER", "LOGIN_RATE_LIMIT_HMAC_SECRET_BASE64")), "AUTH_CONFIG_CHANGED")
        require(target.get("DB_USERNAME") == "seonjalal_staging_app", "TARGET_DB_USER_CHANGED")
        self.password = target["DB_PASSWORD"]
        require(self.password and not any(c in self.password for c in "\r\n\x00"), "INVALID_PASSWORD_FORMAT")
        info = self.fe.CONFIG.lstat()
        self.fe.validate_config_info(info)
        self.original_inode = info.st_dev, info.st_ino
        require(self.fe.CONFIG.read_bytes() == self.expected, "ACTIVE_CONFIG_CHANGED")
        require(call(["docker", "exec", self.fe_id, "cat", "/etc/nginx/conf.d/default.conf"])
                == self.expected, "ACTIVE_MOUNT_MISMATCH")
        self.plans = self.fe.variants(self.expected)
        for value in self.plans.values():
            self.fe.check_syntax(self.fe_id, value)
        task({"running"})
        validation()
        require(self.source("SELECT @@GLOBAL.read_only,@@GLOBAL.super_read_only;") == "0\t0", "SOURCE_ALREADY_FENCED")
        read_check()
        require(get("/actuator/health", 18085)[0] == 200, "CANDIDATE_HEALTH_FAILED")
        # 공개 CA만 DB 컨테이너 임시 경로에 전달. 패키지 설치·DB 설정 변경 없음.
        ca = (ROOT / "rds-ca.pem").read_bytes()
        require(ca.count(b"-----BEGIN CERTIFICATE-----") >= 1, "CA_INVALID")
        ca_dir = call(["docker", "exec", self.db_id, "mktemp", "-d", "/tmp/cutover-ca.XXXXXX"]).decode().strip()
        require(re.fullmatch(r"/tmp/cutover-ca\.[A-Za-z0-9]+", ca_dir), "CA_DIRECTORY_INVALID")
        self.ca_path = ca_dir + "/rds-ca.pem"
        call(["docker", "exec", "-i", self.db_id, "sh", "-ec", 'umask 077; cat > "$1"', "sh", self.ca_path], ca)
        self.inventory(self.source)
        self.inventory(self.target)
        self.orphans()
        # 관리 접속·SELECT·TLS·CHECKSUM 권한을 Freeze 전에 확인.
        self.fingerprints(self.source, SOURCE_SCHEMA)
        self.fingerprints(self.target, TARGET_SCHEMA)
        (self.work / "original.conf").write_bytes(self.expected)
        self.event("preflight_passed")

    def recover(self):
        if self.target_open_attempted:
            # 새 Target 쓰기 가능성이 생긴 이후 Source로 단순 복귀 금지.
            self.reload(self.plans["target-freeze.conf"], "recovery_target_freeze")
            self.event("target_frozen_source_fence_preserved_manual_review_required")
            return
        if self.changed:
            self.reload(self.expected, "recovery_source_route")
        if self.fence_attempted:
            self.source("SET GLOBAL super_read_only=OFF; SET GLOBAL read_only=OFF;")
            require(self.source("SELECT @@GLOBAL.read_only,@@GLOBAL.super_read_only;") == "0\t0", "SOURCE_RECOVERY_FENCE_FAILED")
        read_check()
        self.event("source_service_restored_no_target_business_writer")

    def execute(self):
        # 이 표식 이후 중복 요청은 preflight에서 차단. 응답 유실 시에도 재실행 금지.
        with MARKER.open("x") as stream:
            json.dump({"work": str(self.work), "phase": "freeze_intent"}, stream)
            stream.flush()
            os.fsync(stream.fileno())
        self.thread = threading.Thread(target=self.sample, daemon=True)
        self.thread.start()
        try:
            self.event("freeze_requested")
            self.freeze_ns = self.events[-1]["monotonic_ns"]
            self.reload(self.plans["source-freeze.conf"], "source_freeze")
            require(get("/api/__freeze_probe__", method="POST")[0] == 503, "WRITE_GATE_NOT_CLOSED")
            read_check()
            self.fence_attempted = True
            self.source("SET GLOBAL read_only=ON; SET GLOBAL super_read_only=ON;")
            require(self.source("SELECT @@GLOBAL.read_only,@@GLOBAL.super_read_only;") == "1\t1", "SOURCE_FENCE_FAILED")
            deadline = time.monotonic() + 15
            while self.source("SELECT COUNT(*) FROM information_schema.INNODB_TRX WHERE trx_is_read_only=0;") != "0":
                require(time.monotonic() < deadline, "SOURCE_WRITE_TRANSACTION_DRAIN_TIMEOUT")
                time.sleep(0.5)
            self.event("source_fenced_and_write_transactions_drained")
            source = self.fingerprints(self.source, SOURCE_SCHEMA)
            deadline = time.monotonic() + 45
            while source != self.fingerprints(self.target, TARGET_SCHEMA):
                require(time.monotonic() < deadline, "CDC_FINAL_CHECKSUM_TIMEOUT")
                task({"running"})
                time.sleep(1)
            validation()
            self.orphans()
            self.event("final_checksums_16_and_fk_18_verified")
            require(not any(not x["pair_ok"] for x in self.samples), "READ_PROBE_FAILURE_BEFORE_STOP")
            self.event("dms_stop_requested")
            aws("dms", "stop-replication-task", "--replication-task-arn", TASK)
            deadline = time.monotonic() + 90
            while task({"running", "stopping", "stopped"})["Status"] != "stopped":
                require(time.monotonic() < deadline, "DMS_STOP_TIMEOUT")
                time.sleep(1)
            self.event("dms_stopped")
            require(self.fingerprints(self.source, SOURCE_SCHEMA) == source ==
                    self.fingerprints(self.target, TARGET_SCHEMA), "POST_STOP_CHECKSUM_MISMATCH")
            self.reload(self.plans["target-freeze.conf"], "target_read_switch")
            read_check()
            require(get("/api/__freeze_probe__", method="POST")[0] == 503, "TARGET_GATE_NOT_CLOSED")
            require(self.source("SELECT @@GLOBAL.read_only,@@GLOBAL.super_read_only;") == "1\t1", "SOURCE_FENCE_LOST")
            require(not any(not x["pair_ok"] for x in self.samples), "READ_PROBE_FAILURE_BEFORE_REOPEN")
            task({"stopped"})
            # 설정 쓰기·reload 도중 응답이 유실돼도 Target 신규 쓰기 가능 상태로 취급.
            self.target_open_attempted = True
            self.event("target_open_requested")
            self.reload(self.plans["target-open.conf"], "target_open")
            read_check()
            elapsed = (time.monotonic_ns() - self.freeze_ns) / 1e9
            self.event("control_cutover_completed", control_freeze_seconds=elapsed)
            time.sleep(3)
            self.halt.set()
            self.thread.join(timeout=12)
            require(not self.thread.is_alive(), "READ_PROBE_DID_NOT_STOP")
            require(len(self.samples) >= 3, "INSUFFICIENT_READ_SAMPLES")
            require(not any(not x["pair_ok"] for x in self.samples), "READ_PROBE_FAILURE_AFTER_REOPEN")
            self.summary("control_complete_business_write_qa_pending", elapsed)
        except Exception as error:
            self.event("cutover_failed", reason=str(error) if type(error) is RuntimeError else type(error).__name__)
            try:
                self.recover()
            except Exception as recovery_error:
                self.event("recovery_requires_operator", reason=type(recovery_error).__name__)
            self.summary("failed_review_events_before_any_retry", None)
            raise
        finally:
            self.halt.set()
            if self.thread:
                self.thread.join(timeout=12)
            with (self.work / "read-probes.json").open("w") as stream:
                json.dump(self.samples, stream)

    def summary(self, phase, elapsed):
        result = {"phase": phase, "control_freeze_seconds": elapsed, "read_probe_pairs": len(self.samples),
                  "read_probe_failed_pairs": sum(not x["pair_ok"] for x in self.samples),
                  "probe_scope": "FE localhost health and products only; approx 1 pair/sec",
                  "first_business_write_seconds": None, "target_business_write_qa": "pending",
                  "source_target_checksums": "16 tables; non-cryptographic MySQL EXTENDED checksums"
                      if any(x["event"] == "final_checksums_16_and_fk_18_verified" for x in self.events) else "not verified",
                  "source_schema": SOURCE_SCHEMA, "target_schema": TARGET_SCHEMA}
        (self.work / "result.json").write_text(json.dumps(result, indent=2) + "\n")
        print("CUTOVER_RESULT=" + json.dumps(result), flush=True)


def main(fe, expected):
    os.umask(0o077)
    identity()
    # 실행 전체 잠금과 영구 intent로 같은 시험의 중복 실행 방지.
    with open("/run/lock/seonjalal-cutover-20261019.lock", "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        require(not MARKER.exists(), "PREVIOUS_CUTOVER_ATTEMPT_EXISTS")
        import tempfile
        rehearsal = Run(fe, expected)
        rehearsal.work = Path(tempfile.mkdtemp(prefix="seonjalal-cutover-run-20261019-", dir="/opt"))
        print("CUTOVER_RESULT_DIR=" + str(rehearsal.work), flush=True)
        rehearsal.preflight()
        # 일반적인 중단 신호도 경계별 복구 경로로 전달. SIGKILL/호스트 장애는 수동 복구 필요.
        def interrupted(signum, frame):
            raise RuntimeError("EXECUTION_INTERRUPTED")
        previous = {s: signal.signal(s, interrupted) for s in (signal.SIGINT, signal.SIGTERM)}
        try:
            rehearsal.execute()
        finally:
            for signum, handler in previous.items():
                signal.signal(signum, handler)
