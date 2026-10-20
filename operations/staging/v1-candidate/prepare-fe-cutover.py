#!/usr/bin/env python3
"""고정 시험 FE 설정의 백업·비활성 전환안 생성만 수행. 적용/reload 없음."""
import hashlib
import http.client
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import tempfile

FE = "seonjalal-frontend"
CANDIDATE = "seonjalal-v1-cutover-20261019-candidate"
SOURCE = "seonjalal-backend"
DB = "seonjalal-database"
NETWORK = "seonjalal-develop_service-network"
ALIAS = "cutover-rds-20261019"
CONFIG = Path("/opt/seonjalal-develop/nginx/conf.d/default.conf")
IMAGE = "sha256:b93ca625008ea5eb57e600dc56e033a7ca45bce8842ac76b789eb0da6563e1f2"


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def validate_config_info(info):
    # 사용자 실제 stat 결과에 고정. 소유권 변경이나 임의 소유자 허용 없음.
    require(stat.S_ISREG(info.st_mode) and info.st_uid == 1001
            and info.st_gid == 1001 and stat.S_IMODE(info.st_mode) == 0o644,
            "CONFIG_METADATA_DIFFERS_FROM_VERIFIED_STATE")


def run(args, data=None):
    # Docker inspect 전체 환경과 도구 stderr는 출력하지 않음.
    result = subprocess.run(args, input=data, capture_output=True)
    require(result.returncode == 0, "LOCAL_COMMAND_FAILED")
    return result.stdout


def inspect():
    return json.loads(run(["docker", "inspect", FE, CANDIDATE, SOURCE, DB]))


def validate(items):
    require([x["Name"] for x in items] == ["/" + n for n in (FE, CANDIDATE, SOURCE, DB)],
            "CONTAINER_NAMES_CHANGED")
    require(all(x["State"]["Status"] == "running" for x in items), "CONTAINER_NOT_RUNNING")
    require(items[1]["Image"] == items[2]["Image"] == IMAGE, "FIXED_IMAGE_CHANGED")
    mounts = [m for m in items[0]["Mounts"] if m["Destination"] == "/etc/nginx/conf.d/default.conf"]
    require(len(mounts) == 1 and mounts[0]["Type"] == "bind"
            and mounts[0]["Source"] == str(CONFIG), "FE_CONFIG_MOUNT_CHANGED")
    nets = items[1]["NetworkSettings"]["Networks"]
    require(set(nets) == {NETWORK, "seonjalal-cutover-20261019"}, "CANDIDATE_NETWORK_CHANGED")
    require(ALIAS in (nets[NETWORK].get("Aliases") or []), "CANDIDATE_ALIAS_MISSING")
    require(NETWORK in items[0]["NetworkSettings"]["Networks"], "FE_NETWORK_CHANGED")


def variants(original):
    text = original.decode("utf-8")
    require(text.count("proxy_pass http://backend:8080/") == 2, "UNEXPECTED_PROXY_LAYOUT")
    require(text.count("    location /api/ {\n") == 1, "UNEXPECTED_API_LAYOUT")
    start = text.index("    location /api/ {\n")
    end = text.index("    location / {\n", start)
    # 순수 읽기 허용 목록: GET 상품목록·health만. 다른 API는 Freeze 중 503.
    gate = '''    location = /api/products {
        if ($request_method != GET) { return 503; }
        proxy_pass http://backend:8080/products;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    location = /api {
        add_header Retry-After 5 always;
        return 503;
    }

    location /api/ {
        add_header Retry-After 5 always;
        return 503;
    }

'''
    frozen = (text[:start] + gate + text[end:]).replace(
        "    location = /api/health {\n",
        "    location = /api/health {\n        if ($request_method != GET) { return 503; }\n",
    )
    # 비활성 설정안만 생성. 기존 설정은 main에서 검토 원본과의 정확한 일치 요구.
    target = lambda value: value.replace("proxy_pass http://backend:8080/",
                                         "proxy_pass http://" + ALIAS + ":8080/")
    return {"source-open.conf": original, "source-freeze.conf": frozen.encode(),
            "target-freeze.conf": target(frozen).encode(), "target-open.conf": target(text).encode()}


def check_syntax(fe_id, value):
    # Nginx에는 일반 임시 파일을 전달. 크기 0인 stdin/pipe를 설정 파일로 취급하지 않음.
    # 이 파일은 비활성 구문 검사 자료이며 활성 설정 쓰기·reload 없음.
    wrapper = (b"error_log stderr;\nevents {}\nhttp {\n"
               b"include /etc/nginx/mime.types;\naccess_log off;\n" + value + b"\n}\n")
    path = run(["docker", "exec", fe_id, "mktemp", "/tmp/cutover-nginx-check.XXXXXX"]).decode().strip()
    require(re.fullmatch(r"/tmp/cutover-nginx-check\.[A-Za-z0-9]+", path), "SYNTAX_CHECK_PATH_INVALID")
    run(["docker", "exec", "-i", fe_id, "sh", "-ec", 'umask 077; cat > "$1"', "sh", path], wrapper)
    run(["docker", "exec", fe_id, "nginx", "-t", "-c", path])


def health():
    connection = http.client.HTTPConnection("127.0.0.1", 8081, timeout=10)
    try:
        connection.request("GET", "/api/health")
        response = connection.getresponse()
        require(response.status == 200, "EXISTING_FE_HEALTH_FAILED")
        response.read(65536)
    finally:
        connection.close()


def main(expected):
    require(os.geteuid() == 0, "ROOT_REQUIRED")
    addresses = json.loads(run(["ip", "-j", "-4", "address", "show"]))
    require(any(a.get("local") == "172.31.6.212" for interface in addresses
                for a in interface.get("addr_info", [])), "WRONG_SOURCE_HOST")
    before = inspect()
    validate(before)
    info = CONFIG.lstat()
    validate_config_info(info)
    original = CONFIG.read_bytes()
    require(original == expected, "ACTIVE_CONFIG_DIFFERS_FROM_REVIEWED_TEMPLATE")
    require(run(["docker", "exec", before[0]["Id"], "cat", "/etc/nginx/conf.d/default.conf"])
            == original, "MOUNTED_CONFIG_DIFFERS")
    health()
    plans = variants(original)
    for value in plans.values():
        check_syntax(before[0]["Id"], value)
    # 구문 검사를 통과한 안만 보호 폴더에 저장. 기존 폴더나 자료 덮어쓰기 없음.
    os.umask(0o077)
    directory = Path(tempfile.mkdtemp(prefix="seonjalal-cutover-fe-20261019-", dir="/opt"))
    print("FE_PREPARE_DIR=" + str(directory), flush=True)
    for name, value in {"original.conf": original, **plans}.items():
        with (directory / name).open("xb") as stream:
            stream.write(value)
            stream.flush()
            os.fsync(stream.fileno())
    after = inspect()
    validate(after)
    require([x["Id"] for x in after] == [x["Id"] for x in before], "CONTAINER_IDS_CHANGED")
    require(CONFIG.read_bytes() == original, "ACTIVE_CONFIG_CHANGED_DURING_PREPARE")
    after_info = CONFIG.lstat()
    validate_config_info(after_info)
    require((after_info.st_dev, after_info.st_ino) == (info.st_dev, info.st_ino),
            "ACTIVE_CONFIG_INODE_CHANGED")
    health()
    record = {"phase": "prepared_not_applied", "source_config": str(CONFIG),
              "original_metadata": {"uid": info.st_uid, "gid": info.st_gid,
                                    "mode": "0644", "device": info.st_dev, "inode": info.st_ino},
              "original_sha256": hashlib.sha256(original).hexdigest(),
              "files_sha256": {n: hashlib.sha256(v).hexdigest() for n, v in plans.items()},
              "container_ids": {x["Name"]: x["Id"] for x in before},
              "syntax_checked": 4, "reload_executed": False, "freeze_executed": False}
    with (directory / "verification.json").open("x") as stream:
        json.dump(record, stream, indent=2)
        stream.write("\n")
    print("FE_BACKUP_AND_FOUR_INACTIVE_CONFIGS_READY")
    print("NGINX_SYNTAX_CHECKS=4 EXISTING_FE_HEALTH_HTTP=200")
    print("NO_ACTIVE_CONFIG_WRITE_NO_RELOAD_NO_DB_OR_DMS_CHANGE")
    print("LIMITATION=Auth QA, data equality, drain/fence, DMS stop and timed cutover pending")
