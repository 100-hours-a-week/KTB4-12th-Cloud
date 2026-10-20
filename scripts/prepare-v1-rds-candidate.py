#!/usr/bin/env python3
"""독립 V1 RDS 후보의 공개 파일·SSM 요청만 로컬 준비. AWS/Docker 실행 없음."""
import base64
import hashlib
import json
import os
from pathlib import Path
import shlex
import tempfile

ROOT = Path(__file__).resolve().parents[1]
TARGET = "/opt/seonjalal-v1-rds-rehearsal"
INSTANCE = "i-0c6f5ea09b5e20859"


def build_script(files):
    payload = {key: base64.b64encode(value).decode() for key, value in files.items()}
    # 공개 코드만 SSM 요청에 포함. 실제 Secret은 대상 EC2 Role로 실행 시 조회.
    bootstrap = """import base64,json,os
from pathlib import Path
files = json.loads(__PAYLOAD__)
source = base64.b64decode(files['start.py'])
scope = {'__name__': 'candidate_preflight'}
exec(compile(source, 'start.py', 'exec'), scope)
scope['require'](os.getuid() == 0, 'ROOT_REQUIRED')
scope['identity_guard']()
target = Path(__TARGET__)
target.mkdir(mode=0o700)
for name, encoded in files.items():
    path = target / name
    path.write_bytes(base64.b64decode(encoded))
    os.chmod(path, 0o644 if name != 'start.py' else 0o600)
print('PUBLIC_CANDIDATE_FILES_INSTALLED', flush=True)
""".replace("__PAYLOAD__", repr(json.dumps(payload))).replace("__TARGET__", repr(TARGET))
    return "set -eu\numask 077\npython3 -c " + shlex.quote(bootstrap) + "\npython3 " + TARGET + "/start.py\n"


def main():
    os.umask(0o077)
    rehearsal = ROOT / "infrastructure/terraform/environments/staging/rehearsal"
    files = {
        "start.py": (ROOT / "operations/staging/v1-candidate/start.py").read_bytes(),
        "tls-bootstrap.sh": (rehearsal / "tls-bootstrap.sh").read_bytes(),
        "rds-ca.pem": (rehearsal / "certs/ap-northeast-2-bundle.pem").read_bytes(),
    }
    script = build_script(files)
    request = {
        "DocumentName": "AWS-RunShellScript", "InstanceIds": [INSTANCE],
        "Comment": "Isolated RDS candidate; preserve existing V1 services and routing",
        "TimeoutSeconds": 600,
        "Parameters": {"commands": ["bash -c " + shlex.quote(script)], "executionTimeout": ["600"]},
    }
    directory = Path(tempfile.mkdtemp(prefix="seonjalal-v1-rds-candidate.", dir="/private/tmp"))
    (directory / "request.json").write_text(json.dumps(request, indent=2) + "\n")
    (directory / "remote-script.sh").write_text(script)
    (directory / "manifest.json").write_text(json.dumps({
        "target_instance": INSTANCE, "target_path": TARGET,
        "script_sha256": hashlib.sha256(script.encode()).hexdigest(),
        "files": {name: hashlib.sha256(value).hexdigest() for name, value in files.items()},
        "aws_executed": False,
    }, indent=2) + "\n")
    print("CANDIDATE_PREPARE_DIR=" + str(directory))
    print("SSM_REQUEST_FILE=" + str(directory / "request.json"))
    print("REMOTE_SCRIPT_SHA256=" + hashlib.sha256(script.encode()).hexdigest())
    print("LOCAL_PREPARE_ONLY_NO_AWS_OR_SERVER_CHANGE")


if __name__ == "__main__":
    main()
