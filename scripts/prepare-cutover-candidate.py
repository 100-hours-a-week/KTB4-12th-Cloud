#!/usr/bin/env python3
"""새Target 후보 공개코드·CA·SSM 요청 로컬 준비만 수행. AWS/원격 실행 없음."""
import argparse
import base64
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shlex
import stat
import tempfile

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("app_grant", Path(__file__).with_name("prepare-cutover-app-access.py"))
grant = importlib.util.module_from_spec(spec)
spec.loader.exec_module(grant)
runtime = grant.runtime


def build_request(files):
    # 공개코드/공개CA만 포함. Docker 환경변수·암호·JWT는 원격 메모리에서만 취득.
    payload = {key: base64.b64encode(value).decode() for key, value in files.items()}
    program = """import base64,json,sys
files={key:base64.b64decode(value) for key,value in json.loads(__PAYLOAD__).items()}
scope={'__name__':'cutover_candidate_remote'}
try:
    exec(compile(files['start.py'],'start-cutover.py','exec'),scope)
    scope['main'](files)
except Exception as error:
    detail=str(error) if type(error) is RuntimeError else type(error).__name__
    print('STOP_NEW_CANDIDATE='+detail+'; preserve files/state; do not rerun or full-deploy',file=sys.stderr)
    sys.exit(1)
""".replace("__PAYLOAD__", repr(json.dumps(payload)))
    request = {"DocumentName": "AWS-RunShellScript", "InstanceIds": ["i-0c6f5ea09b5e20859"],
               "Comment": "Approved isolated new-schema V1 candidate; no source/routing/business writes",
               "TimeoutSeconds": 600, "Parameters": {"commands": ["python3 -c " + shlex.quote(program)],
                                                        "executionTimeout": ["600"]}}
    runtime.require(len(json.dumps(request).encode()) < 64000, "SSM 요청 크기 제한 초과")
    return request


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", required=True)
    args = parser.parse_args()
    runtime_directory = runtime.protected_directory(args.directory)
    path = runtime_directory / grant.RECORD
    info = path.lstat()
    runtime.require(not path.is_symlink() and stat.S_IMODE(info.st_mode) == 0o600
                    and info.st_uid == os.getuid(), "권한 적용 기록 소유자/권한 오류")
    record = json.loads(path.read_text())
    runtime.require(record.get("phase") == "scoped_grant_verified" and record.get("user") == grant.access.USER
                    and record.get("host") == grant.access.HOST
                    and record.get("target_scope") in ("`seonjalal\\_cutover\\_20261019`.*", "`seonjalal_cutover_20261019`.*"),
                    "승인된 새Target 권한 적용 기록 필요")
    rehearsal = ROOT / "infrastructure/terraform/environments/staging/rehearsal"
    files = {"start.py": (ROOT / "operations/staging/v1-candidate/start-cutover.py").read_bytes(),
             "tls-bootstrap.sh": (rehearsal / "tls-bootstrap.sh").read_bytes(),
             "rds-ca.pem": (rehearsal / "certs/ap-northeast-2-bundle.pem").read_bytes()}
    request = build_request(files)
    os.umask(0o077)
    directory = Path(tempfile.mkdtemp(prefix="seonjalal-cutover-candidate.", dir="/private/tmp"))
    runtime.secure_write(directory / "request.json", request)
    runtime.secure_write(directory / "manifest.json", {
        "instance": request["InstanceIds"][0], "candidate": "seonjalal-v1-cutover-20261019-candidate",
        "schema": "seonjalal_cutover_20261019", "port": 18085, "aws_executed": False,
        "files_sha256": {key: hashlib.sha256(value).hexdigest() for key, value in files.items()}})
    print("CANDIDATE_SSM_REQUEST_FILE=" + str(directory / "request.json"))
    print("LOCAL_PREPARE_ONLY_NO_AWS_OR_SERVER_CHANGE")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        detail = str(error) if isinstance(error, runtime.SafetyStop) else type(error).__name__
        raise SystemExit("STOP: " + detail)
