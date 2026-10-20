#!/usr/bin/env python3
"""승인한 FE 백업·비활성 설정안 준비의 SSM 요청을 Mac에 저장. AWS 실행 없음."""
import base64
import hashlib
import json
import os
from pathlib import Path
import shlex
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def build_request(code, original):
    program = '''import base64,sys
scope={'__name__':'fe_cutover_prepare'}
try:
    exec(compile(base64.b64decode(__CODE__),'prepare-fe-cutover.py','exec'),scope)
    scope['main'](base64.b64decode(__CONFIG__))
except Exception as error:
    detail=str(error) if type(error) is RuntimeError else type(error).__name__
    print('STOP_FE_PREPARE='+detail+'; preserve any prepared files; do not apply/reload',file=sys.stderr)
    sys.exit(1)
'''.replace("__CODE__", repr(base64.b64encode(code).decode())).replace(
        "__CONFIG__", repr(base64.b64encode(original).decode()))
    return {"DocumentName": "AWS-RunShellScript", "InstanceIds": ["i-0c6f5ea09b5e20859"],
            "Comment": "Approved FE backup and inactive configs only; no reload/freeze/traffic switch",
            "TimeoutSeconds": 120, "Parameters": {"commands": ["python3 -c " + shlex.quote(program)],
                                                    "executionTimeout": ["120"]}}


def main():
    code = (ROOT / "operations/staging/v1-candidate/prepare-fe-cutover.py").read_bytes()
    original = (ROOT / "nginx/conf.d/default.conf").read_bytes()
    request = build_request(code, original)
    if len(json.dumps(request).encode()) >= 64000:
        raise SystemExit("STOP: SSM_REQUEST_TOO_LARGE")
    os.umask(0o077)
    directory = Path(tempfile.mkdtemp(prefix="seonjalal-cutover-fe.", dir="/private/tmp"))
    for name, value in {
        "request.json": request,
        "manifest.json": {"aws_executed": False, "code_sha256": hashlib.sha256(code).hexdigest(),
                          "expected_original_sha256": hashlib.sha256(original).hexdigest()},
    }.items():
        with (directory / name).open("x") as stream:
            json.dump(value, stream, indent=2)
            stream.write("\n")
    print("FE_PREPARE_REQUEST_FILE=" + str(directory / "request.json"))
    print("LOCAL_FILES_ONLY_NO_AWS_EXECUTION")


if __name__ == "__main__":
    main()
