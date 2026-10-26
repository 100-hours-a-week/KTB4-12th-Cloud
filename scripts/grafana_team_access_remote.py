"""모니터링 서버에서 실행되는 팀 접속 전환 로직(Grafana 컨테이너 재생성).

Mac의 prepare-grafana-team-access.py가 이 파일을 SSM 요청에 실어 보낸다. 서버 접근은 모두 Host 객체를 거치므로
테스트에서 가짜 Host로 교체해 동작(교체·롤백·재실행)을 검증한다.

관리자 API에는 로그인하지 않는다(관리자 계정·비밀번호는 UI에서 바뀔 수 있고 서버에 저장하지 않는다).
검증은 인증이 필요 없는 /api/health와 컨테이너 마운트 비교로 한다. Shared 폴더와 권한은 관리자가 UI에서 설정한다.
"""
import datetime
import fcntl
import json
import os
import pathlib
import subprocess
import time
import urllib.error
import urllib.request

CONTAINER = "seonjalal-monitor-grafana"
SAVED = CONTAINER + "-before-team-access"
ROOT = pathlib.Path("/opt/seonjalal-observability")
EXPECTED = {"accountId": "721744297924", "region": "ap-northeast-2",
            "instanceId": "i-0c6a75f630436591c", "privateIp": "10.20.10.150"}
MEMORY = "1g"
MEMORY_BYTES = 1024 ** 3
MIN_AVAILABLE_MIB = 700
ENV = [
    "GF_SECURITY_ADMIN_USER=admin",
    "GF_SECURITY_ADMIN_PASSWORD__FILE=/run/secrets/grafana-admin-password",
    "GF_AUTH_ANONYMOUS_ENABLED=false",
    "GF_ANALYTICS_REPORTING_ENABLED=false",
    "GF_ANALYTICS_CHECK_FOR_UPDATES=false",
    "GF_ANALYTICS_CHECK_FOR_PLUGIN_UPDATES=false",
    "GF_USERS_ALLOW_SIGN_UP=true",
    "GF_USERS_ALLOW_ORG_CREATE=false",
    "GF_USERS_AUTO_ASSIGN_ORG=true",
    "GF_USERS_AUTO_ASSIGN_ORG_ROLE=Editor",
    "GF_USERS_VIEWERS_CAN_EDIT=false",
]


def require(ok, message):
    if not ok:
        raise SystemExit("STOP: " + message)


class Host:
    """실제 서버 접근. 테스트에서는 같은 메서드를 가진 가짜로 바꾼다."""

    def identity(self):
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        request = urllib.request.Request("http://169.254.169.254/latest/api/token", method="PUT",
                                         headers={"X-aws-ec2-metadata-token-ttl-seconds": "60"})
        with opener.open(request, timeout=3) as response:
            token = response.read().decode()
        request = urllib.request.Request("http://169.254.169.254/latest/dynamic/instance-identity/document",
                                         headers={"X-aws-ec2-metadata-token": token})
        with opener.open(request, timeout=3) as response:
            document = json.load(response)
        return {"accountId": document.get("accountId"), "region": document.get("region"),
                "instanceId": document.get("instanceId"), "privateIp": document.get("privateIp")}

    def lock(self):
        descriptor = os.open("/run/lock/seonjalal-grafana-team-access.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SystemExit("STOP: 다른 팀 접속 전환 실행 중. 재전송 금지")
        return descriptor

    def available_memory_mib(self):
        for line in pathlib.Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) // 1024
        return 0

    def read(self, path):
        return pathlib.Path(path).read_text()

    def write_private(self, path, text):
        path = pathlib.Path(path)
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        path.write_text(text)
        os.chmod(path, 0o600)

    def docker(self, *args, check=True):
        result = subprocess.run(["docker", *args], capture_output=True, text=True)
        self.last_stderr = result.stderr
        if check:
            require(result.returncode == 0, "Docker 단계 실패: " + args[0] + " " + (result.stderr.strip().splitlines() or [""])[0][:160])
        return result.returncode, result.stdout.strip()

    def healthy(self):
        """인증이 필요 없는 /api/health만 확인한다(DB 열림 포함)."""
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            with opener.open("http://127.0.0.1:3000/api/health", timeout=5) as response:
                return response.status == 200 and json.loads(response.read() or "null").get("database") == "ok"
        except (OSError, ValueError, AttributeError, urllib.error.URLError):
            return False

    def logs(self, name, tail):
        """컨테이너 로그(표준 출력과 오류를 합쳐서) 마지막 tail줄. 실패해도 예외 없이 빈 문자열."""
        result = subprocess.run(["docker", "logs", "--tail", str(tail), name], capture_output=True, text=True)
        return result.stdout + result.stderr

    def sleep(self, seconds):
        time.sleep(seconds)


def run_args(image, private_ip, mounts):
    args = ["run", "--detach", "--restart", "unless-stopped", "--network", "seonjalal-monitor",
            "--cap-drop", "ALL", "--security-opt", "no-new-privileges:true", "--pids-limit", "256",
            "--log-driver", "json-file", "--log-opt", "max-size=10m", "--log-opt", "max-file=3",
            "--name", CONTAINER, "--user", "472:472", "--memory", MEMORY, "--memory-swap", MEMORY, "--cpus", "1.0",
            "--publish", "127.0.0.1:3000:3000", "--publish", private_ip + ":3000:3000"]
    for item in ENV:
        args += ["-e", item]
    for mount in mounts:
        args += ["-v", mount]
    return args + [image]


def mounts_of(inspect):
    """기존 컨테이너의 마운트를 그대로 이어받는다(볼륨, 비밀번호 파일, provisioning)."""
    result = []
    for mount in inspect["Mounts"]:
        source = mount["Name"] if mount["Type"] == "volume" else mount["Source"]
        result.append(f"{source}:{mount['Destination']}" + ("" if mount.get("RW", True) else ":ro"))
    return result


def mount_pairs(inspect):
    return {(m["Name"] if m["Type"] == "volume" else m["Source"], m["Destination"]) for m in inspect["Mounts"]}


def is_configured(inspect, private_ip):
    env = set(inspect["Config"]["Env"])
    bindings = inspect["HostConfig"].get("PortBindings", {}).get("3000/tcp") or []
    return (all(item in env for item in ENV)
            and any(b.get("HostIp") == private_ip for b in bindings)
            and inspect["HostConfig"].get("Memory") == MEMORY_BYTES)


HEALTH_ATTEMPTS = 30
HEALTH_INTERVAL = 2
LOG_TAIL = 80
# 이 단어가 들어간 로그 줄은 증거에서 내용을 가린다(비밀이 섞였을 가능성 차단).
SENSITIVE_WORDS = ("password", "passwd", "secret", "token", "authorization", "api_key", "apikey", "cookie")


def wait_healthy(host, attempts=HEALTH_ATTEMPTS):
    for _ in range(attempts):
        if host.healthy():
            return True
        host.sleep(HEALTH_INTERVAL)
    return False


def inspect_of(host, name):
    code, raw = host.docker("inspect", name, check=False)
    return json.loads(raw)[0] if code == 0 else None


def unconfigured_items(inspect, private_ip):
    """설정값 중 반영되지 않은 항목의 이름만 돌려준다(값은 싣지 않는다)."""
    env = set(inspect["Config"]["Env"])
    missing = [item.split("=", 1)[0] for item in ENV if item not in env]
    bindings = inspect["HostConfig"].get("PortBindings", {}).get("3000/tcp") or []
    if not any(b.get("HostIp") == private_ip for b in bindings):
        missing.append("사설 IP 포트 게시")
    if inspect["HostConfig"].get("Memory") != MEMORY_BYTES:
        missing.append("메모리 제한")
    return missing


def verify_new_container(host, old, private_ip, created):
    """검증에 실패한 이유를 모두 모아 돌려준다. 빈 목록이면 통과."""
    if created[0] != 0:
        stderr = (getattr(host, "last_stderr", "") or "").strip().splitlines()
        return ["docker run 실패" + (": " + stderr[0][:200] if stderr else "")]
    new = inspect_of(host, CONTAINER)
    if new is None:
        return ["새 컨테이너를 조회할 수 없음"]
    reasons = []
    if not wait_healthy(host):
        reasons.append(f"/api/health가 {HEALTH_ATTEMPTS * HEALTH_INTERVAL}초 안에 정상 응답하지 않음")
    lost = sorted(destination for _, destination in mount_pairs(old) - mount_pairs(new))
    if lost:
        reasons.append("마운트 누락: " + ", ".join(lost))
    missing = unconfigured_items(new, private_ip)
    if missing:
        reasons.append("설정값 미반영: " + ", ".join(missing))
    return reasons


def redact_lines(text):
    lines = []
    for line in text.splitlines()[-LOG_TAIL:]:
        lines.append("[민감 단어가 있어 가린 줄]" if any(w in line.lower() for w in SENSITIVE_WORDS) else line[:300])
    return lines


def save_evidence(host, reasons, stamp, out):
    """실패한 새 컨테이너를 지우기 전에 원인 진단용 증거를 화면과 서버 파일에 남긴다."""
    lines = ["FAILURE_EVIDENCE", "REASONS:"] + ["- " + reason for reason in reasons]
    state = inspect_of(host, CONTAINER)
    if state is not None:
        info = state.get("State", {})
        lines.append("STATE status={} exit_code={} oom_killed={} error={!r} started={} finished={}".format(
            info.get("Status"), info.get("ExitCode"), info.get("OOMKilled"), info.get("Error"),
            info.get("StartedAt"), info.get("FinishedAt")))
    lines.append(f"HOST_MEMORY_AVAILABLE_MIB={host.available_memory_mib()}")
    lines.append(f"LOG_TAIL (최근 {LOG_TAIL}줄, 민감 단어가 있는 줄은 가림)")
    lines += redact_lines(host.logs(CONTAINER, LOG_TAIL))
    host.write_private(ROOT / f"before-team-access-{stamp}" / "failure-evidence.txt", "\n".join(lines))
    for line in lines:
        out(line)


def swap_container(host, inspect, image, private_ip, stamp, out=print):
    backup = ROOT / f"before-team-access-{stamp}" / "grafana-inspect.json"
    host.write_private(backup, json.dumps(inspect, indent=2))
    mounts = mounts_of(inspect)
    renamed = False
    reasons = []
    try:
        host.docker("stop", CONTAINER)
        host.docker("rename", CONTAINER, SAVED)
        renamed = True
        created = host.docker("run", *run_args(image, private_ip, mounts), check=False)
        reasons = verify_new_container(host, inspect, private_ip, created)
    except SystemExit as error:
        reasons = [str(error)]
        raise
    finally:
        if reasons:
            if renamed:
                save_evidence(host, reasons, stamp, out)
                host.docker("rm", "-f", CONTAINER, check=False)
                host.docker("rename", SAVED, CONTAINER, check=False)
            host.docker("start", CONTAINER, check=False)
            restored = wait_healthy(host)
            out("ROLLBACK_" + ("RESTORED_HEALTHY" if restored else "RESTORED_NEEDS_CHECK"))
    require(not reasons, "새 컨테이너 검증 실패. 이전 컨테이너로 복원했다")
    return str(backup)


def run(host, now=None, out=print):
    identity = host.identity()
    require(identity == EXPECTED, "관측 노드 대상 불일치")
    host.lock()
    available = host.available_memory_mib()
    require(available >= MIN_AVAILABLE_MIB, f"여유 메모리 {available}MiB, {MIN_AVAILABLE_MIB}MiB 필요")
    image = json.loads(host.read(ROOT / "image-lock.json"))["grafana"]["digest"]
    require("@sha256:" in image and ":latest" not in image, "고정된 Grafana 이미지 digest가 없다")
    inspect = inspect_of(host, CONTAINER)
    require(inspect is not None, "기존 Grafana 컨테이너가 없다")
    require(inspect["State"]["Running"], "기존 Grafana 컨테이너가 실행 중이 아니다")
    require(wait_healthy(host, attempts=3), "기존 Grafana가 정상이 아니다. 전환 전에 상태 확인 필요")
    stamp = now or datetime.datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    if is_configured(inspect, identity["privateIp"]):
        out("CONTAINER_ALREADY_CONFIGURED_NOTHING_CHANGED")
        return
    backup = swap_container(host, inspect, image, identity["privateIp"], stamp, out)
    out("CONTAINER_SWAPPED backup=" + backup + " previous_container=" + SAVED + " (stopped, kept for rollback)")
    out("GRAFANA_URL=http://" + identity["privateIp"] + ":3000 SIGN_UP=on DEFAULT_ROLE=Editor")
    out("NEXT=admin sets Shared folder permissions in the UI")
    out("NO_ADMIN_LOGIN_NO_PASSWORD_USE_NO_APP_DB_OR_SECURITY_GROUP_CHANGE")


def main():
    require(os.geteuid() == 0, "root 필요")
    run(Host())
