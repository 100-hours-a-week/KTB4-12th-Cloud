#!/usr/bin/env python3
"""승인된 비운영 전환 1회 요청을 로컬 생성. AWS 실행 없음."""
import base64
import hashlib
import json
import os
from pathlib import Path
import shlex
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def build_request(files):
    payload = {name: base64.b64encode(value).decode() for name, value in files.items()}
    program = '''import base64,json,types,sys
files={k:base64.b64decode(v) for k,v in json.loads(__PAYLOAD__).items()}
try:
    fe=types.ModuleType('cutover_fe_reviewed')
    exec(compile(files['fe.py'],'fe.py','exec'),fe.__dict__)
    scope={'__name__':'approved_cutover_run'}
    exec(compile(files['run.py'],'run-cutover.py','exec'),scope)
    scope['main'](fe,files['original.conf'])
except Exception as error:
    detail=str(error) if type(error) is RuntimeError else type(error).__name__
    print('STOP_CUTOVER='+detail+'; DO_NOT_RESEND; inspect result directory and events',file=sys.stderr)
    sys.exit(1)
'''.replace("__PAYLOAD__", repr(json.dumps(payload)))
    return {"DocumentName": "AWS-RunShellScript", "InstanceIds": ["i-0c6f5ea09b5e20859"],
            "Comment": "Approved nonprod cutover once: FE gate, source fence, DMS stop, RDS switch and guarded recovery",
            "TimeoutSeconds": 600, "Parameters": {"commands": ["python3 -c " + shlex.quote(program)],
                                                    "executionTimeout": ["600"]}}


def main():
    operations = ROOT / "operations/staging/v1-candidate"
    files = {"fe.py": (operations / "prepare-fe-cutover.py").read_bytes(),
             "run.py": (operations / "run-cutover.py").read_bytes(),
             "original.conf": (ROOT / "nginx/conf.d/default.conf").read_bytes()}
    request = build_request(files)
    if len(request["Comment"]) > 100 or "\n" in request["Comment"]:
        raise SystemExit("STOP: INVALID_SSM_COMMENT")
    if len(json.dumps(request).encode()) >= 64000:
        raise SystemExit("STOP: SSM_REQUEST_TOO_LARGE")
    os.umask(0o077)
    directory = Path(tempfile.mkdtemp(prefix="seonjalal-cutover-run.", dir="/private/tmp"))
    for name, value in {"request.json": request, "manifest.json": {
        "aws_executed": False, "source_instance": request["InstanceIds"][0],
        "source_schema": "seonjalal_develop", "target_schema": "seonjalal_cutover_20261019",
        "files_sha256": {k: hashlib.sha256(v).hexdigest() for k, v in files.items()}}}.items():
        with (directory / name).open("x") as stream:
            json.dump(value, stream, indent=2)
            stream.write("\n")
    print("CUTOVER_RUN_REQUEST_FILE=" + str(directory / "request.json"))
    print("LOCAL_PREPARE_ONLY_ACTUAL_FREEZE_STOP_SWITCH_REQUIRE_USER_EXECUTION")


if __name__ == "__main__":
    main()
