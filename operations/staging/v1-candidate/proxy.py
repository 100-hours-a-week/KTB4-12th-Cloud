#!/usr/bin/env python3
"""기존 Nginx와 별도 PID/설정/포트로 읽기 전환 시험 진입점 관리."""
import argparse
from datetime import datetime, timezone
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import urllib.request

ROOT = Path("/opt/seonjalal-v1-rds-rehearsal")
PROXY = ROOT / "proxy"
spec = importlib.util.spec_from_file_location("candidate", ROOT / "start.py")
# 로컬 검사는 원격 후보 코드 대신 같은 폴더의 파일 사용.
if not (ROOT / "start.py").exists():
    spec = importlib.util.spec_from_file_location("candidate", Path(__file__).with_name("start.py"))
candidate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(candidate)
TARGETS = {"v1": "http://127.0.0.1:18083", "ecs": "https://v2-staging.seonjalal.com"}


def config(target):
    locations = ""
    for endpoint in ("/products", "/actuator/health"):
        locations += """
    location = __ENDPOINT__ {
      if ($request_method !~ ^(GET|HEAD)$) { return 405; }
      proxy_pass __UPSTREAM__;
      proxy_http_version 1.1;
      proxy_set_header Connection "";
      proxy_set_header Host v2-staging.seonjalal.com;
      proxy_ssl_server_name on;
      proxy_ssl_name v2-staging.seonjalal.com;
      proxy_ssl_verify on;
      proxy_ssl_verify_depth 3;
      proxy_ssl_trusted_certificate /etc/pki/tls/certs/ca-bundle.crt;
      proxy_ssl_protocols TLSv1.2 TLSv1.3;
      proxy_connect_timeout 5s;
      proxy_read_timeout 10s;
      proxy_next_upstream off;
      proxy_cache off;
      proxy_buffering off;
      proxy_hide_header X-Rehearsal-Upstream;
      add_header X-Rehearsal-Upstream __TARGET__ always;
    }
""".replace("__ENDPOINT__", endpoint).replace("__UPSTREAM__", TARGETS[target]).replace("__TARGET__", target)
    return """worker_processes 1;
pid __PROXY__/nginx.pid;
error_log __PROXY__/error.log warn;
worker_shutdown_timeout 30s;
events { worker_connections 128; }
http {
  access_log __PROXY__/access.log combined;
  keepalive_timeout 0;
  server {
    listen 127.0.0.1:18084;
    server_name _;
    location / { return 404; }
__LOCATIONS__
  }
}
""".replace("__PROXY__", str(PROXY)).replace("__LOCATIONS__", locations)


def nginx(path, *options):
    result = subprocess.run(["nginx", "-p", str(PROXY) + "/", "-c", str(path), *options],
                            capture_output=True, text=True, timeout=15)
    candidate.require(result.returncode == 0, "ISOLATED_NGINX_COMMAND_FAILED")


def request(url):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(url, timeout=5) as response:
        candidate.require(response.status == 200, "PROBE_HTTP_NOT_200")
        return response.headers.get("X-Rehearsal-Upstream"), response.read()


def verify(target):
    for endpoint in ("/actuator/health", "/products?sort=POPULAR"):
        marker, body = request("http://127.0.0.1:18084" + endpoint)
        candidate.require(marker == target, "PROXY_TARGET_MARKER_MISMATCH")
        if endpoint == "/actuator/health":
            candidate.require(json.loads(body)["status"] == "UP", "PROXY_HEALTH_NOT_UP")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("start", "switch"))
    parser.add_argument("--target", choices=("v1", "ecs"), default="v1")
    args = parser.parse_args()
    os.umask(0o077)
    candidate.identity_guard()
    candidate.require(os.getuid() == 0, "ROOT_REQUIRED")
    request(TARGETS[args.target] + "/actuator/health")
    request(TARGETS[args.target] + "/products?sort=POPULAR")
    active = PROXY / "active.conf"
    if args.action == "start":
        candidate.require(args.target == "v1", "INITIAL_TARGET_MUST_BE_V1")
        with candidate.socket.socket() as probe:
            probe.bind(("127.0.0.1", 18084))
        PROXY.mkdir(mode=0o700)
        for name in TARGETS:
            path = PROXY / (name + ".conf")
            path.write_text(config(name))
            nginx(path, "-t")
        active.symlink_to(PROXY / "v1.conf")
        nginx(active)
        try:
            for attempt in range(5):
                try:
                    verify("v1")
                    break
                except Exception:
                    candidate.time.sleep(0.25)
            else:
                raise RuntimeError("INITIAL_PROXY_VERIFICATION_TIMEOUT")
        except Exception:
            nginx(active, "-s", "quit")
            raise
    else:
        candidate.require(active.is_symlink(), "MISSING_ISOLATED_PROXY_CONFIG")
        previous = active.resolve(strict=True)
        candidate.require(previous in [PROXY / "v1.conf", PROXY / "ecs.conf"], "UNEXPECTED_CONFIG_TARGET")
        next_path = PROXY / (args.target + ".conf")
        candidate.require(next_path.read_text() == config(args.target), "CONFIG_CONTENT_CHANGED")
        nginx(next_path, "-t")
        temporary = PROXY / "active.next"
        candidate.require(not temporary.exists() and not temporary.is_symlink(), "PENDING_SWITCH_EXISTS")
        temporary.symlink_to(next_path)
        os.replace(temporary, active)
        try:
            nginx(active, "-s", "reload")
            # reload은 비동기이므로 새 worker 응답 표식을 제한 시간 내 확인.
            for attempt in range(5):
                try:
                    verify(args.target)
                    break
                except Exception:
                    candidate.time.sleep(0.25)
            else:
                raise RuntimeError("SWITCH_VERIFICATION_TIMEOUT")
        except Exception:
            temporary.symlink_to(previous)
            os.replace(temporary, active)
            nginx(active, "-s", "reload")
            print("PREVIOUS_PROXY_TARGET_RESTORE_REQUESTED", flush=True)
            raise
    request("http://127.0.0.1:8081/api/actuator/health")
    print("PROXY_TARGET=" + args.target)
    print("PROXY_HEALTH_AND_PRODUCTS_HTTP=200")
    print("EXISTING_V1_HEALTH_HTTP=200")
    print("PROXY_ACTION_COMPLETED_UTC=" + datetime.now(timezone.utc).isoformat())
    print("READONLY_PROXY_READY_NO_PUBLIC_ENTRY_OR_DB_CHANGE")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        message = str(error) if type(error) is RuntimeError else type(error).__name__
        print("STOP_ISOLATED_PROXY=" + message, file=sys.stderr)
        sys.exit(1)
