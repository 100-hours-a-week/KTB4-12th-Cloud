#!/usr/bin/env python3
"""승인된 시험 선물 경로만 허용. 기존 읽기 설정은 보존하고 별도 설정 전환."""
import argparse
from datetime import datetime, timezone
import importlib.util
import os
from pathlib import Path
import sys

spec = importlib.util.spec_from_file_location("read_proxy", Path(__file__).with_name("proxy.py"))
base = importlib.util.module_from_spec(spec)
spec.loader.exec_module(base)


def write_config(target):
    source = base.config(target)
    first = source.index("    location = /products {")
    end = source.index("    location = /actuator/health {")
    product_location = source[first:end]
    extra = ""
    for endpoint in ("/gifts", "/gifts/preflight"):
        extra += product_location.replace("location = /products", "location = " + endpoint).replace(
            "^(GET|HEAD)$", "^POST$")
    # 작성해 둔 동일한 TLS/무재시도 설정을 재사용하며 기존 읽기 location은 그대로 유지.
    return source[:end] + extra + source[end:]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("enable", "switch", "disable"))
    parser.add_argument("--target", choices=("v1", "ecs"), default="v1")
    parser.add_argument("--approved-fixture-writes", action="store_true")
    args = parser.parse_args()
    base.candidate.require(args.action == "disable" or args.approved_fixture_writes, "WRITE_SCOPE_APPROVAL_REQUIRED")
    base.candidate.identity_guard()
    base.candidate.require(os.getuid() == 0, "ROOT_REQUIRED")
    proxy = base.PROXY
    active = proxy / "active.conf"
    base.candidate.require(active.is_symlink(), "ACTIVE_CONFIG_MISSING")
    previous = active.resolve(strict=True)
    known = {proxy / (target + ".conf"): base.config(target) for target in base.TARGETS}
    known.update({proxy / (target + "-write.conf"): write_config(target) for target in base.TARGETS})
    base.candidate.require(previous in known and previous.read_text() == known[previous], "UNKNOWN_PREVIOUS_CONFIG")
    if args.action == "enable":
        base.candidate.require(args.target == "v1" and previous == proxy / "v1.conf", "ENABLE_REQUIRES_READONLY_V1")
        for target in base.TARGETS:
            path = proxy / (target + "-write.conf")
            if path.exists():
                base.candidate.require(path.read_text() == write_config(target), "EXISTING_WRITE_CONFIG_CHANGED")
            else:
                with path.open("x") as stream:
                    stream.write(write_config(target))
                path.chmod(0o600)
            base.nginx(path, "-t")
    if args.action == "switch":
        base.candidate.require(previous.name in ("v1-write.conf", "ecs-write.conf"), "WRITE_MODE_NOT_ENABLED")
    target = "v1" if args.action == "disable" else args.target
    suffix = ".conf" if args.action == "disable" else "-write.conf"
    next_path = proxy / (target + suffix)
    base.candidate.require(next_path.read_text() == known[next_path], "NEXT_CONFIG_CHANGED")
    base.request(base.TARGETS[target] + "/actuator/health")
    base.request(base.TARGETS[target] + "/products?sort=POPULAR")
    base.nginx(next_path, "-t")
    temporary = proxy / "active.next"
    base.candidate.require(not temporary.exists() and not temporary.is_symlink(), "PENDING_CONFIG_CHANGE")
    temporary.symlink_to(next_path)
    os.replace(temporary, active)
    try:
        base.nginx(active, "-s", "reload")
        for attempt in range(5):
            try:
                base.verify(target)
                break
            except Exception:
                base.candidate.time.sleep(0.25)
        else:
            raise RuntimeError("WRITE_PROXY_VERIFY_TIMEOUT")
    except Exception:
        temporary.symlink_to(previous)
        os.replace(temporary, active)
        base.nginx(active, "-s", "reload")
        print("PREVIOUS_PROXY_CONFIG_RESTORE_REQUESTED", flush=True)
        raise
    base.request("http://127.0.0.1:8081/api/actuator/health")
    print("PROXY_TARGET=" + target)
    print("FIXTURE_WRITE_MODE=" + ("off" if args.action == "disable" else "on"))
    print("PROXY_HEALTH_AND_PRODUCTS_HTTP=200")
    print("EXISTING_V1_HEALTH_HTTP=200")
    print("PROXY_ACTION_COMPLETED_UTC=" + datetime.now(timezone.utc).isoformat())
    print("NO_GIFT_REQUEST_EXECUTED_BY_PROXY_CONTROL")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        message = str(error) if type(error) is RuntimeError else type(error).__name__
        print("STOP_WRITE_PROXY=" + message, file=sys.stderr)
        sys.exit(1)
