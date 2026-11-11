"""관측 서버 전용 Prometheus 교체·복구 로직. import만으로 원격 작업 실행 없음."""
import copy
import datetime
import fcntl
import hashlib
import http.client
import json
import os
from pathlib import Path
import socket
import tempfile
import time
import urllib.parse
import urllib.request

NAME = "seonjalal-monitor-prometheus"
LABEL = "seonjalal.monitoring.transition"
ROOT = Path("/opt/seonjalal-observability")
LIVE = ROOT / "config/prometheus.yml"
RULE = ROOT / "config/rules/payment-rabbitmq-alerts.yml"
DESTINATION = "/etc/prometheus/rules/payment-rabbitmq-alerts.yml"


def require(ok, reason):
    if not ok:
        raise RuntimeError(reason)


def utc():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def digest(content):
    return hashlib.sha256(content).hexdigest()


def atomic_write(path, content, mode=0o600):
    require(not path.is_symlink(), "symlink_target")
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".transition-")
    try:
        with os.fdopen(fd, "wb") as output:
            output.write(content)
            output.flush()
            os.fchmod(output.fileno(), mode)
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class UnixConnection(http.client.HTTPConnection):
    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect("/var/run/docker.sock")


class Engine:
    def request(self, method, path, body=None, missing=False):
        connection = UnixConnection("localhost", timeout=60)
        try:
            content = json.dumps(body).encode() if body is not None else None
            connection.request(method, path, body=content,
                               headers={"Content-Type": "application/json"})
            response = connection.getresponse()
            raw = response.read()
            if response.status == 404 and missing:
                return None
            # Docker 오류 원문은 환경변수·인자 등을 포함할 수 있어 출력하지 않음.
            require(response.status < 300, "docker_http_" + str(response.status))
            return json.loads(raw) if raw else None
        finally:
            connection.close()

    def get(self, name):
        return self.request("GET", "/containers/" + urllib.parse.quote(name, safe="") + "/json", missing=True)

    def stop(self, identifier):
        self.request("POST", "/containers/" + identifier + "/stop?t=45")

    def start(self, identifier):
        self.request("POST", "/containers/" + identifier + "/start")

    def rename(self, identifier, name):
        self.request("POST", "/containers/" + identifier + "/rename?" + urllib.parse.urlencode({"name": name}))

    def create(self, name, body):
        result = self.request("POST", "/containers/create?" + urllib.parse.urlencode({"name": name}), body)
        return result["Id"]


def create_body(snapshot, private_ip, rule_source, marker):
    # 일회성 자동 Hostname/컨테이너 ID 대신 기존 명시적 실행 설정과 network alias 보존.
    keys = ("User", "Env", "Cmd", "Entrypoint", "Image", "WorkingDir", "Labels", "Volumes",
            "ExposedPorts", "Healthcheck", "StopSignal", "StopTimeout", "Tty", "OpenStdin", "StdinOnce")
    body = {key: copy.deepcopy(snapshot["Config"][key]) for key in keys if key in snapshot["Config"]}
    body["Cmd"].append("--web.enable-remote-write-receiver")
    body["Labels"] = body.get("Labels") or {}
    body["Labels"][LABEL] = marker
    host = copy.deepcopy(snapshot["HostConfig"])
    host["PortBindings"]["9090/tcp"].append({"HostIp": private_ip, "HostPort": "9090"})
    host.setdefault("Mounts", []).append({"Type": "bind", "Source": str(rule_source),
                                          "Target": DESTINATION, "ReadOnly": True})
    body["HostConfig"] = host
    networks = snapshot["NetworkSettings"]["Networks"]
    require(set(networks) == {"seonjalal-monitor"}, "unexpected_networks")
    aliases = [alias for alias in networks["seonjalal-monitor"].get("Aliases", [])
               if alias and alias != snapshot["Id"][:12]]
    require("prometheus" in aliases, "missing_prometheus_alias")
    body["NetworkingConfig"] = {"EndpointsConfig": {"seonjalal-monitor": {"Aliases": aliases}}}
    return body


def verify_container_snapshot(snapshot, current):
    for key in ("Config", "HostConfig"):
        require(current[key] == snapshot[key], "container_settings_drift_" + key.lower())
    # Mounts는 목적지가 고유한 목록. 순서만 무시하고 Source/Name/RW 등 전체 필드 비교.
    def canonical(rows):
        destinations = [row["Destination"] for row in rows]
        require(len(destinations) == len(set(destinations)), "duplicate_mount_destination")
        return sorted(json.dumps(row, sort_keys=True) for row in rows)
    require(canonical(current["Mounts"]) == canonical(snapshot["Mounts"]), "container_settings_drift_mounts")
    return current["Mounts"] != snapshot["Mounts"]


def verify_loaded_config(before, after):
    # Prometheus API는 생략된 기본값도 출력하므로 원본 YAML 문자열과 직접 비교하지 않음.
    # 동일 버전의 실행 전후 정규화된 설정을 비교해 허용한 두 변경만 검증.
    prior, current = copy.deepcopy(before), copy.deepcopy(after)
    prior_alerting = prior.pop("alerting", {})
    next_alerting = current.pop("alerting", {})
    prior.pop("rule_files", None)
    require(current.pop("rule_files", None) == [DESTINATION], "rule_file_mismatch")
    require(prior == current, "existing_runtime_config_changed")
    old_managers = prior_alerting.pop("alertmanagers", [])
    new_managers = next_alerting.pop("alertmanagers", [])
    require(prior_alerting == next_alerting, "existing_alerting_options_changed")
    require(len(new_managers) == len(old_managers) + 1 and
            new_managers[:len(old_managers)] == old_managers, "existing_alertmanagers_changed")
    targets = [target for config in new_managers[-1].get("static_configs", [])
               for target in config.get("targets", [])]
    require(targets == ["alertmanager:9093"], "alertmanager_target_mismatch")


def rollback(engine, original_id, marker, config_changed, restore_config, ready):
    current = engine.get(NAME)
    if current and current["Id"] != original_id:
        require(current["Config"].get("Labels", {}).get(LABEL) == marker, "foreign_container_do_not_stop")
        if current["State"]["Running"]:
            engine.stop(current["Id"])
        # 실패 컨테이너는 삭제하지 않고 중지 상태로 증거 보존.
        engine.rename(current["Id"], NAME + "-failed-" + marker.rsplit("-", 1)[-1])
    original = engine.get(original_id)
    require(original is not None, "original_container_missing")
    if original["State"]["Running"] and config_changed():
        engine.stop(original_id)
        original = engine.get(original_id)
    restore_config()
    if original["Name"] != "/" + NAME:
        engine.rename(original_id, NAME)
    if not original["State"]["Running"]:
        engine.start(original_id)
    ready()


def apply(engine, snapshot, body, marker, activate, changed, restore, ready, verify, record):
    original_id = snapshot["Id"]
    stop_begin = time.monotonic()
    try:
        record("stop_requested")
        engine.stop(original_id)
        require(not engine.get(original_id)["State"]["Running"], "original_still_running")
        engine.rename(original_id, NAME + "-before-v3-" + marker.rsplit("-", 1)[-1])
        record("original_stopped_and_preserved")
        activate()
        identifier = engine.create(NAME, body)
        record("candidate_created", newContainerId=identifier)
        engine.start(identifier)
        ready()
        record("ready_observed", stopRequestToReadySeconds=round(time.monotonic() - stop_begin, 3))
        verify(identifier)
        record("applied")
    except Exception as error:
        try:
            record("apply_failed", errorType=type(error).__name__)
        except Exception:
            pass  # 기록 디스크 오류가 서비스 복구를 가로막지 않도록 분리.
        rollback(engine, original_id, marker, changed, restore, ready)
        record("rolled_back")
        raise RuntimeError("apply_failed_original_restored") from None


def main(instance_id, directory, candidate_sha, rule_sha, mode):
    require(os.geteuid() == 0, "root_required")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    request = urllib.request.Request("http://169.254.169.254/latest/api/token", method="PUT",
        headers={"X-aws-ec2-metadata-token-ttl-seconds": "60"})
    with opener.open(request, timeout=3) as response:
        token = response.read().decode()
    request = urllib.request.Request("http://169.254.169.254/latest/dynamic/instance-identity/document",
        headers={"X-aws-ec2-metadata-token": token})
    with opener.open(request, timeout=3) as response:
        identity = json.load(response)
    require(identity.get("instanceId") == instance_id and identity.get("region") == "ap-northeast-2",
            "wrong_instance")
    directory = Path(directory)
    require(directory.parent == ROOT / "changes" and directory.name.startswith("prometheus-v3-"), "wrong_plan_path")
    for parent in (ROOT, ROOT / "config", ROOT / "changes", directory, LIVE, RULE.parent, RULE):
        require(not parent.is_symlink(), "symlink_path")
    lock = os.open("/run/lock/seonjalal-prometheus-transition.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    plan = json.loads((directory / "plan.json").read_text())
    snapshot = json.loads((directory / "container-before.json").read_text())
    original = (directory / "prometheus-before.yml").read_bytes()
    require(digest(original) == plan["beforeConfigSha256"], "original_config_hash_drift")
    require(snapshot["Id"] == plan["beforeContainerId"], "snapshot_id_drift")
    private_ip = identity["privateIp"]
    engine = Engine()
    marker = directory.name
    journal = {"mode": mode, "events": []}

    def record(phase, **extra):
        journal["events"].append({"phase": phase, "at": utc(), **extra})
        atomic_write(directory / ("transition-" + mode + ".json"),
                     (json.dumps(journal, ensure_ascii=False, indent=2) + "\n").encode())

    def http(path):
        with opener.open("http://127.0.0.1:9090" + path, timeout=4) as response:
            return json.load(response)["data"]

    def ready():
        deadline = time.monotonic() + 90
        while True:
            try:
                with opener.open("http://127.0.0.1:9090/-/ready", timeout=2) as response:
                    if response.status == 200:
                        return
            except OSError:
                pass
            require(time.monotonic() < deadline, "readiness_timeout")
            time.sleep(0.5)

    def changed():
        return LIVE.read_bytes() != original

    def restore():
        if changed():
            info = LIVE.stat()
            atomic_write(LIVE, original, info.st_mode & 0o777)
            os.chown(LIVE, info.st_uid, info.st_gid)

    if mode == "rollback":
        # 실패 후보가 손상돼도 검증된 원본 복구는 가로막지 않음.
        rollback(engine, snapshot["Id"], marker, changed, restore, ready)
        record("rolled_back")
        print("PROMETHEUS_ROLLBACK_FINISHED")
        return

    candidate = (directory / "prometheus-candidate.yml").read_bytes()
    require(digest(candidate) == plan["candidateConfigSha256"] == candidate_sha, "candidate_config_hash_drift")
    require(digest((directory / RULE.name).read_bytes()) == rule_sha, "rule_file_hash_drift")
    require(plan["proposedPrivateBinding"] == private_ip + ":9090", "private_ip_drift")
    live = engine.get(NAME)
    require(live and live["Id"] == snapshot["Id"] and live["State"]["Running"], "live_container_drift")
    require(not changed(), "live_config_drift")
    mount_order_only_difference = verify_container_snapshot(snapshot, live)
    body = create_body(snapshot, private_ip, RULE, marker)
    atomic_write(directory / "container-candidate.json", (json.dumps(body) + "\n").encode())
    summary = {"mode": mode, "receiver": private_ip + ":9090", "oldContainerPreserved": True,
               "sameTsdbVolume": True, "memoryMiB": body["HostConfig"]["Memory"] // 1024**2,
               "cpuLimit": body["HostConfig"]["NanoCpus"] / 10**9,
               "readinessLimitSeconds": 90, "pollIntervalSeconds": 0.5,
               "autoRollback": True, "securityGroupChanged": False,
               "mountOrderOnlyDifference": mount_order_only_difference}
    if mode == "check":
        print("PROMETHEUS_TRANSITION_PLAN_CHECK_FINISHED")
        print(json.dumps(summary))
        return

    ready()
    import yaml
    baseline_config = yaml.safe_load(http("/api/v1/status/config")["yaml"])
    # 같은 고정 시각의 up 시계열 전후 비교. 전체 보존 이력의 완전성 보장과 구분.
    history_time = str(time.time() - 30)
    history_path = "/api/v1/query?" + urllib.parse.urlencode({"query": "up", "time": history_time})
    baseline = http(history_path)["result"]
    require(baseline, "historical_baseline_empty")
    journal["historicalQueryTime"] = history_time
    begin = time.monotonic()
    info = LIVE.stat()

    def activate():
        RULE.parent.mkdir(mode=0o755, exist_ok=True)
        if RULE.exists():
            require(RULE.read_bytes() == (directory / RULE.name).read_bytes(), "existing_rule_conflict")
        else:
            atomic_write(RULE, (directory / RULE.name).read_bytes(), 0o644)
        atomic_write(LIVE, candidate, info.st_mode & 0o777)
        os.chown(LIVE, info.st_uid, info.st_gid)

    def verify(identifier):
        require(LIVE.read_bytes() == candidate, "active_file_mismatch")
        verify_loaded_config(baseline_config, yaml.safe_load(http("/api/v1/status/config")["yaml"]))
        require(http("/api/v1/status/flags").get("web.enable-remote-write-receiver") == "true", "receiver_not_enabled")
        groups = http("/api/v1/rules")["groups"]
        matching = [group for group in groups if group["name"] == "seonjalal-v3-payment-rabbitmq"]
        require(len(matching) == 1 and len(matching[0]["rules"]) == 5, "rules_not_loaded")
        after = http(history_path)["result"]
        key = lambda item: json.dumps(item, sort_keys=True)
        require(sorted(baseline, key=key) == sorted(after, key=key), "historical_up_mismatch")
        created = engine.get(identifier)
        require(created["HostConfig"]["Memory"] == snapshot["HostConfig"]["Memory"] and
                created["HostConfig"]["NanoCpus"] == snapshot["HostConfig"]["NanoCpus"], "resource_limit_mismatch")
        require(any(m["Type"] == "volume" and m.get("Name") == "seonjalal-monitor-prometheus-data"
                    and m["Destination"] == "/prometheus" and m["RW"] for m in created["Mounts"]), "tsdb_volume_mismatch")
        require(created["HostConfig"]["PortBindings"] == body["HostConfig"]["PortBindings"], "published_ports_mismatch")
        record("verified", historicalUpSeries=len(after), rulesLoaded=5,
               newRestartCount=created["RestartCount"],
               stopRequestToVerificationSeconds=round(time.monotonic() - begin, 3))

    apply(engine, snapshot, body, marker, activate, changed, restore, ready, verify, record)
    print("PROMETHEUS_TRANSITION_FINISHED")
    print(json.dumps({**summary, "events": journal["events"]}, ensure_ascii=False))
