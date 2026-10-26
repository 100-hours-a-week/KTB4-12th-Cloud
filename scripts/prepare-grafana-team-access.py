#!/usr/bin/env python3
"""Mac: 모니터링 서버 Grafana 팀 접속 전환용 SSM 요청 생성. AWS 호출·원격 실행 없음.

원격에서 하는 일은 scripts/grafana_team_access_remote.py 참고(컨테이너 재생성, Shared 폴더, 대시보드).
"""
import base64
import json
import os
from pathlib import Path
import tempfile

INSTANCE = "i-0c6a75f630436591c"
HERE = Path(__file__).resolve().parent
REMOTE_SOURCE = HERE / "grafana_team_access_remote.py"
DASHBOARD = HERE.parent / "operations" / "grafana" / "dashboards" / "cloudwatch-load-test.json"


def build_request():
    dashboard = json.loads(DASHBOARD.read_text(encoding="utf-8"))
    encoded_dashboard = base64.b64encode(json.dumps(dashboard).encode()).decode()
    remote = f"DASHBOARD_B64 = {encoded_dashboard!r}\n" + REMOTE_SOURCE.read_text(encoding="utf-8") + "\nmain()\n"
    compile(remote, "<remote-static-validation>", "exec")
    encoded = base64.b64encode(remote.encode()).decode()
    command = "python3 - <<'PY'\nimport base64\nexec(compile(base64.b64decode(" + repr(encoded) + "), '<grafana-team-access>', 'exec'))\nPY"
    return {
        "DocumentName": "AWS-RunShellScript", "InstanceIds": [INSTANCE],
        "Comment": "Enable team access on the staging monitoring Grafana only",
        "TimeoutSeconds": 300,
        "Parameters": {"commands": [command], "executionTimeout": ["300"]},
    }


if __name__ == "__main__":
    request = build_request()
    directory = Path(tempfile.mkdtemp(prefix="seonjalal-grafana-team-access-", dir="/private/tmp"))
    os.chmod(directory, 0o700)
    path = directory / "request.json"
    path.write_text(json.dumps(request))
    os.chmod(path, 0o600)
    print("REQUEST_FILE=" + str(path))
    print("TARGET_INSTANCE=" + INSTANCE)
    print("PREPARED_ONLY_NO_AWS_CALL_OR_SERVER_CHANGE")
