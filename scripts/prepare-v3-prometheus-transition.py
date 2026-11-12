#!/usr/bin/env python3
"""Mac: Prometheus 전환/복구 요청 생성. 기본 check, 실제 전환은 별도 승인 필요."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import textwrap


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--instance-id", required=True)
    parser.add_argument("--directory", required=True)
    parser.add_argument("--candidate-sha256", required=True)
    parser.add_argument("--mode", choices=("check", "apply", "rollback"), default="check")
    parser.add_argument("--approved", action="store_true")
    args = parser.parse_args()
    if not re.fullmatch(r"i-(?:[0-9a-f]{8}|[0-9a-f]{17})", args.instance_id):
        parser.error("instance-id 형식 확인 필요")
    if not re.fullmatch(r"/opt/seonjalal-observability/changes/prometheus-v3-[0-9]{8}T[0-9]{6}Z-[0-9a-f]{6}", args.directory):
        parser.error("검사 완료 변경안 경로 필요")
    if not re.fullmatch(r"[0-9a-f]{64}", args.candidate_sha256):
        parser.error("검사 완료 후보 SHA256 필요")
    if args.mode != "check" and not args.approved:
        parser.error("apply/rollback 요청 생성은 해당 범위 사용자 승인 후 --approved 필요")
    source = (Path(__file__).parent / "lib/v3_prometheus_transition.py").read_text()
    rules = Path(__file__).resolve().parents[1] / "operations/monitoring/prometheus/payment-rabbitmq-alerts.yml"
    rule_sha = hashlib.sha256(rules.read_bytes()).hexdigest()
    invocation = "main(" + ", ".join(repr(value) for value in
        (args.instance_id, args.directory, args.candidate_sha256, rule_sha, args.mode)) + ")"
    source += "\n" + invocation + "\n"
    remote = ("try:\n" + textwrap.indent(source, "    ") +
              "\nexcept Exception as error:\n" +
              "    import re\n" +
              "    reason = str(error) if type(error) is RuntimeError and re.fullmatch('[a-z0-9_]+', str(error)) else type(error).__name__\n" +
              "    print('STOP: ' + reason + '; 오류 원문 미출력. 재실행 전 전환 기록 확인 필요')\n" +
              "    raise SystemExit(1)\n")
    compile(remote, "<remote-prometheus-transition>", "exec")
    request = {
        "DocumentName": "AWS-RunShellScript", "InstanceIds": [args.instance_id],
        "Comment": "V3 Prometheus preserved transition mode=" + args.mode,
        "TimeoutSeconds": 360,
        "Parameters": {"executionTimeout": ["360"], "commands": ["python3 - <<'PY'\n" + remote + "\nPY"]},
    }
    directory = Path("/private/tmp/seonjalal-v3-monitoring")
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    target = directory / ("prometheus-transition-" + args.mode + ".json")
    fd, temporary = tempfile.mkstemp(dir=directory, prefix=".transition-")
    try:
        with os.fdopen(fd, "w") as output:
            json.dump(request, output, ensure_ascii=False, indent=2)
            output.write("\n")
        os.replace(temporary, target)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    print("SSM 요청:", target)
    print("mode=" + args.mode + " / AWS 호출 없음 / 실제 서버 변경 없음")


if __name__ == "__main__":
    main()
