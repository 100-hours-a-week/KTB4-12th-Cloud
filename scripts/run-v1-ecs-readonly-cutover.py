#!/usr/bin/env python3
"""승인 후 5분 읽기 부하와 독립 프록시 v1→ecs→v1 전환/복귀 기록."""
import argparse
import importlib.util
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import signal
import shutil
import subprocess
import sys
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
INSTANCE = "i-0c6f5ea09b5e20859"
BASE = "http://127.0.0.1:18084"
REMOTE = "/opt/seonjalal-v1-rds-rehearsal/proxy.py"
OUTSTANDING_COMMANDS = set()
spec = importlib.util.spec_from_file_location("read_analysis", ROOT / "scripts/analyze-ecs-readonly-rollout.py")
read_analysis = importlib.util.module_from_spec(spec)
spec.loader.exec_module(read_analysis)


def require(ok, message):
    if not ok:
        raise RuntimeError(message)


def utc():
    return datetime.now(timezone.utc).isoformat()


def aws(*args):
    result = subprocess.run(["aws", "--profile", "seonjalal-production", "--region", "ap-northeast-2",
                             "--no-cli-pager", "--output", "json", "--cli-connect-timeout", "5",
                             "--cli-read-timeout", "15", *args], capture_output=True, text=True, timeout=25)
    require(result.returncode == 0, "AWS_CALL_FAILED")
    return json.loads(result.stdout) if result.stdout.strip() else {}


def check_reads(path, run_id, minimum=30, allow_empty=False):
    # 기록 중인 마지막 불완전 줄은 다음 검사로 넘기며 완료 로그의 오류는 숨기지 않음.
    text = "".join(line for line in path.read_text().splitlines(keepends=True) if line.endswith("\n"))
    if allow_empty and 'ecs_read_request' not in text:
        return
    requests = read_analysis.requests_from_log(text, run_id)
    require(len(requests) >= minimum, "INSUFFICIENT_INITIAL_READ_SAMPLES")
    require(all(item["valid"] is True and item["status"] == 200 and not item.get("transportErrorCode")
                for item in requests), "READ_ERROR_DETECTED_BEFORE_NEXT_SWITCH")


def probe(expected):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(BASE + "/actuator/health", timeout=5) as response:
        require(response.status == 200 and response.headers.get("X-Rehearsal-Upstream") == expected
                and json.loads(response.read())["status"] == "UP", "LOCAL_PROXY_TARGET_CHECK_FAILED")


def wait_load(load, seconds, check=None):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        require(load.poll() is None, "LOAD_ENDED_BEFORE_SWITCH_SEQUENCE_COMPLETED")
        if check:
            check()
        time.sleep(min(1, max(0, deadline - time.monotonic())))


def switch(target, record):
    # 고정된 시험 프록시만 전환. 공개 진입점/DB/ECS 서비스 변경 명령 없음.
    event = {"target": target, "requested_utc": utc()}
    record(event)
    command = aws("ssm", "send-command", "--document-name", "AWS-RunShellScript",
                  "--instance-ids", INSTANCE,
                  "--parameters", json.dumps({"commands": ["flock -w 5 /opt/seonjalal-v1-rds-rehearsal/proxy/control.lock python3 " + REMOTE + " switch --target " + target],
                                               "executionTimeout": ["120"]}))
    command_id = command["Command"]["CommandId"]
    OUTSTANDING_COMMANDS.add(command_id)
    record({"target": target, "command_id": command_id, "submitted_utc": utc()})
    print("SWITCH_REQUESTED=" + target + " COMMAND_ID=" + command_id, flush=True)
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        # SSM 요청 접수 직후 Invocation 생성 지연은 오류 원문 대신 상태로 구분.
        try:
            result = aws("ssm", "get-command-invocation", "--command-id", command_id,
                         "--instance-id", INSTANCE)
        except RuntimeError:
            time.sleep(2)
            continue
        status = result["Status"]
        if status in ("Success", "Failed", "TimedOut", "Cancelled"):
            OUTSTANDING_COMMANDS.discard(command_id)
        if status == "Success":
            require(result["ResponseCode"] == 0 and
                    "PROXY_TARGET=" + target + "\n" in result.get("StandardOutputContent", ""),
                    "REMOTE_PROXY_RESULT_MISMATCH")
            probe(target)
            record({"target": target, "verified_utc": utc(), "command_id": command_id,
                    "remote_output": result.get("StandardOutputContent", "")})
            print("SWITCH_VERIFIED=" + target + " UTC=" + utc(), flush=True)
            return
        require(status in ("Pending", "InProgress", "Delayed"), "REMOTE_SWITCH_FAILED_" + status)
        time.sleep(2)
    raise RuntimeError("SWITCH_RESULT_TIMEOUT_RESTORE_REQUIRED")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--approved-readonly-cutover", action="store_true")
    args = parser.parse_args()
    require(args.approved_readonly_cutover, "EXPLICIT_APPROVAL_REQUIRED")
    require(sys.platform == "darwin", "MAC_ONLY")
    require(aws("sts", "get-caller-identity")["Account"] == "721744297924", "WRONG_ACCOUNT")
    require(subprocess.run(["pgrep", "-x", "k6"], capture_output=True).returncode == 1, "K6_ALREADY_RUNNING_OR_CHECK_FAILED")
    subprocess.run(["k6", "version"], check=True, capture_output=True)
    probe("v1")
    os.umask(0o077)
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-v1-ecs-readonly-baseline-" + str(os.getpid())
    directory = ROOT / "load-tests/results" / run_id
    directory.mkdir(mode=0o700)
    event_path = directory / "cutover-events.jsonl"

    def record(event):
        with event_path.open("a") as stream:
            stream.write(json.dumps(event, ensure_ascii=False) + "\n")
            stream.flush()
            os.fsync(stream.fileno())

    environment = dict(os.environ, BASE_URL=BASE, ECS_RUN_ID=run_id)
    for key in ("ACCESS_TOKEN", "ACCESS_TOKENS", "ACCESS_TOKENS_FILE", "JWT_SECRET_BASE64"):
        environment.pop(key, None)
    log_path = Path("/private/tmp") / (run_id + ".log")
    print("RUN_ID=" + run_id + "\nREQUEST_LOG=" + str(log_path), flush=True)
    with log_path.open("x") as log:
        load = subprocess.Popen(["bash", "scripts/run-k6-with-artifacts.sh", run_id, "baseline",
                                 "load-tests/k6/workloads/v1-ecs-cutover-readonly.js"],
                                cwd=ROOT, env=environment, stdout=log, stderr=subprocess.STDOUT,
                                start_new_session=True)
        # 중단 시에도 복귀 시도를 수행하도록 signal을 일반 예외 경로로 전달.
        def interrupted(signum, frame):
            raise RuntimeError("USER_INTERRUPTED")
        signal.signal(signal.SIGINT, interrupted)
        signal.signal(signal.SIGTERM, interrupted)
        recovery_needed = False
        try:
            record({"load_started_utc": utc(), "pid": load.pid, "run_id": run_id})
            print("OBSERVING_V1_45_SECONDS", flush=True)
            monitor = lambda: check_reads(log_path, run_id, minimum=0, allow_empty=True)
            wait_load(load, 45, monitor)
            check_reads(log_path, run_id)
            recovery_needed = True
            switch("ecs", record)
            print("OBSERVING_ECS_45_SECONDS", flush=True)
            wait_load(load, 45, monitor)
            check_reads(log_path, run_id)
            switch("v1", record)
            recovery_needed = False
            print("V1_RESTORED_WAITING_FOR_FIVE_MINUTE_LOAD_TO_FINISH", flush=True)
            end_deadline = time.monotonic() + 320
            while load.poll() is None:
                monitor()
                require(time.monotonic() < end_deadline, "LOAD_END_TIMEOUT")
                time.sleep(1)
            exit_code = load.wait()
            require(exit_code == 0, "K6_THRESHOLDS_FAILED")
            probe("v1")
            record({"sequence_completed_utc": utc(), "k6_exit_code": exit_code})
            print("READONLY_SEQUENCE_COMPLETED_ANALYSIS_REQUIRED", flush=True)
        except Exception as error:
            record({"sequence_stopped_utc": utc(), "reason": str(error) if type(error) is RuntimeError else type(error).__name__})
            raise
        finally:
            if recovery_needed:
                # 종료되지 않은 전환 요청을 취소하고 종결 여부 확인 후 복귀 시도.
                for command_id in list(OUTSTANDING_COMMANDS):
                    try:
                        aws("ssm", "cancel-command", "--command-id", command_id, "--instance-ids", INSTANCE)
                        for attempt in range(10):
                            result = aws("ssm", "get-command-invocation", "--command-id", command_id,
                                         "--instance-id", INSTANCE)
                            if result["Status"] in ("Success", "Failed", "TimedOut", "Cancelled"):
                                OUTSTANDING_COMMANDS.discard(command_id)
                                break
                            time.sleep(1)
                    except Exception:
                        record({"cancel_result_unknown_utc": utc(), "command_id": command_id})
                try:
                    switch("v1", record)
                    if OUTSTANDING_COMMANDS:
                        record({"outstanding_commands": sorted(OUTSTANDING_COMMANDS), "checked_utc": utc()})
                        print("STOP: 현재 V1 응답은 확인됐으나 이전 명령 종료 미확인. 수동 점검 필요.", flush=True)
                    else:
                        print("RECOVERY_V1_VERIFIED", flush=True)
                except Exception:
                    record({"recovery_failed_utc": utc()})
                    print("STOP: V1 복귀 확인 실패. 시험 프록시를 수동 점검해주세요.", flush=True)
            if load.poll() is None:
                # 이번 실행이 만든 별도 프로세스 그룹만 종료. 다른 k6/서버 영향 없음.
                os.killpg(load.pid, signal.SIGINT)
                try:
                    load.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    os.killpg(load.pid, signal.SIGTERM)
                    load.wait(timeout=15)
            # 임시 폴더가 사라져도 요청 원문 기록을 결과 폴더에 보존.
            saved_log = directory / "request-console.log"
            if not saved_log.exists():
                shutil.copyfile(log_path, saved_log)
            print("CONTROL_EVENTS=" + str(event_path), flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        # AWS 응답/환경변수 원문은 출력하지 않음.
        message = str(error) if type(error) is RuntimeError else type(error).__name__
        print("STOP=" + message, file=sys.stderr)
        sys.exit(1)
