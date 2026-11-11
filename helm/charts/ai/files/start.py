"""Search ready 이후 의존 컨테이너 시작. 내부 주소·Secret 원문 출력 금지."""
import json
import os
import sys
import time
import urllib.request

role = sys.argv[1] if len(sys.argv) == 2 else None
if role not in {"search", "profiler", "chat"}:
    raise SystemExit("Unsupported AI role")
if role == "profiler":
    engine = os.environ.get("PROFILING_TAG_ENGINE")
    if engine not in {"v2", "v3"}:
        raise SystemExit("Explicit PROFILING_TAG_ENGINE required")
    if engine == "v3" and any(not os.environ.get(key, "").strip() for key in (
        "PROFILING_V3_URL", "PROFILING_V3_API_KEY", "PROFILING_V3_PROXY_KEY", "PROFILING_V3_PROXY_SECRET",
    )):
        raise SystemExit("Modal v3 configuration incomplete")
if role in {"profiler", "chat"}:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    deadline = time.monotonic() + 240
    while True:
        try:
            with opener.open("http://127.0.0.1:8000/readyz", timeout=3) as response:
                if json.load(response).get("status") == "ready":
                    break
        except Exception:
            pass
        if time.monotonic() >= deadline:
            raise SystemExit("Search readiness wait exceeded 240 seconds")
        time.sleep(2)
os.execvp("python", ["python", "-m", "integration.containers.entrypoint", role])
