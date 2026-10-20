#!/usr/bin/env python3
"""쓰기 시험 프록시 제어 코드 설치·허용 요청 로컬 준비. AWS 호출 없음."""
import base64
import hashlib
import json
import os
from pathlib import Path
import shlex
import tempfile

ROOT = Path(__file__).resolve().parents[1]
TARGET = "/opt/seonjalal-v1-rds-rehearsal/write-proxy.py"
INSTANCE = "i-0c6f5ea09b5e20859"


def main():
    os.umask(0o077)
    source = (ROOT / "operations/staging/v1-candidate/write-proxy.py").read_bytes()
    install = """import base64,importlib.util,os
from pathlib import Path
root = Path('/opt/seonjalal-v1-rds-rehearsal')
spec = importlib.util.spec_from_file_location('candidate', root/'start.py')
candidate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(candidate)
candidate.identity_guard()
candidate.require(os.getuid()==0, 'ROOT_REQUIRED')
descriptor=os.open(__TARGET__, os.O_WRONLY|os.O_CREAT|os.O_EXCL, 0o600)
with os.fdopen(descriptor,'wb') as stream:
    stream.write(base64.b64decode(__SOURCE__))
""".replace("__TARGET__", repr(TARGET)).replace("__SOURCE__", repr(base64.b64encode(source).decode()))
    script = "set -eu\numask 077\npython3 -c " + shlex.quote(install) + "\nflock -w 5 /opt/seonjalal-v1-rds-rehearsal/proxy/control.lock python3 " + TARGET + " enable --approved-fixture-writes\n"
    request = {
        "DocumentName": "AWS-RunShellScript", "InstanceIds": [INSTANCE],
        "Comment": "Approved fixture-only gift POST; preserve public entry and existing services",
        "TimeoutSeconds": 120,
        "Parameters": {"commands": ["bash -c " + shlex.quote(script)], "executionTimeout": ["120"]},
    }
    directory = Path(tempfile.mkdtemp(prefix="seonjalal-v1-ecs-write-proxy.", dir="/private/tmp"))
    (directory / "request.json").write_text(json.dumps(request, indent=2) + "\n")
    (directory / "remote-script.sh").write_text(script)
    print("WRITE_PROXY_PREPARE_DIR=" + str(directory))
    print("SSM_REQUEST_FILE=" + str(directory / "request.json"))
    print("REMOTE_SCRIPT_SHA256=" + hashlib.sha256(script.encode()).hexdigest())
    print("LOCAL_PREPARE_ONLY_NO_AWS_OR_SERVER_CHANGE")


if __name__ == "__main__":
    main()
