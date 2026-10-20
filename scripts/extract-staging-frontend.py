#!/usr/bin/env python3
"""고정 FE 이미지에서 웹 파일만 추출. 컨테이너 시작 및 AWS 변경 없음."""

from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import tarfile
import tempfile
import time
import uuid


IMAGE = "peterj0117/seonjalal-fe@sha256:274aee76a923e1eed970bf6bdb65538754b35608110624ba0cc441b330276e61"


def docker(*args):
    result = subprocess.run(["docker", *args], capture_output=True, check=False)
    if result.returncode:
        # Docker 출력 전체에는 환경 정보가 섞일 수 있어 명령 종류만 보고한다.
        raise RuntimeError("Docker " + args[0] + " 실패. 엔진 상태 확인 필요")
    return result.stdout


def extract_files(archive, site):
    total = 0
    count = 0
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:*") as entries:
        for entry in entries:
            relative = PurePosixPath(entry.name)
            if relative.is_absolute() or ".." in relative.parts:
                raise ValueError("안전하지 않은 Archive 경로")
            target = site.joinpath(*relative.parts)
            if entry.isdir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            if not entry.isfile() or entry.size > 64 * 1024 * 1024:
                raise ValueError("링크·특수파일 또는 파일 크기 제한 위반")
            total += entry.size
            count += 1
            if total > 512 * 1024 * 1024 or count > 10000:
                raise ValueError("Archive 전체 크기 또는 파일 수 제한 위반")
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("xb") as stream:
                stream.write(entries.extractfile(entry).read())
    if not (site / "index.html").is_file():
        raise ValueError("추출 루트에 index.html이 없음")


def inspect_files(site):
    files = []
    api_files = 0
    webhook_files = 0
    for path in sorted(site.rglob("*")):
        if not path.is_file():
            continue
        content = path.read_bytes()
        files.append({
            "path": path.relative_to(site).as_posix(),
            "bytes": len(content),
            "sha256": hashlib.sha256(content).hexdigest(),
        })
        if path.suffix in (".html", ".js", ".mjs"):
            api_files += bool(re.search(rb'''["'`]/api["'`]''', content))
            webhook_files += bool(re.search(
                rb"https://(?:canary\.|ptb\.)?discord(?:app)?\.com/api/webhooks/", content
            ))
    serialized = json.dumps(files, sort_keys=True, separators=(",", ":")).encode()
    return {
        "files": files,
        "file_count": len(files),
        "total_bytes": sum(item["bytes"] for item in files),
        "tree_sha256": hashlib.sha256(serialized).hexdigest(),
        "source_map_count": sum(item["path"].endswith(".map") for item in files),
        "relative_api_literal_files": api_files,
        "discord_webhook_literal_files": webhook_files,
    }


def main():
    os.umask(0o077)
    started = time.monotonic()
    info = json.loads(docker("image", "inspect", IMAGE))[0]
    if info.get("Architecture") != "amd64" or info.get("Os") != "linux":
        raise ValueError("이미지 실행 플랫폼 불일치")
    if IMAGE not in info.get("RepoDigests", []):
        raise ValueError("고정 이미지 Digest 확인 실패")
    directory = Path(tempfile.mkdtemp(prefix="seonjalal-fe-1.3.2.", dir="/private/tmp"))
    site = directory / "site"
    site.mkdir()
    name = "seonjalal-fe-extract-" + uuid.uuid4().hex[:12]
    container = docker(
        "create", "--pull=never", "--platform", "linux/amd64", "--network", "none",
        "--name", name, IMAGE,
    ).decode().strip()
    try:
        state = json.loads(docker("inspect", container))[0]
        if state["State"]["Status"] != "created":
            raise ValueError("추출용 컨테이너가 미실행 상태가 아님")
        archive = docker("cp", container + ":/usr/share/nginx/html/.", "-")
        extract_files(archive, site)
    finally:
        state = json.loads(docker("inspect", container))[0]
        # 이 도구가 생성한 미실행 컨테이너만 제거한다. 강제 종료 옵션은 사용하지 않는다.
        if state["Name"] == "/" + name and state["State"]["Status"] == "created":
            docker("rm", container)
        else:
            raise RuntimeError("예상과 다른 컨테이너 상태. 제거하지 않고 중단")
    result = inspect_files(site)
    result.update({
        "source_image": IMAGE,
        "image_id": info["Id"],
        "version": "1.3.2",
        "extracted_utc": datetime.now(timezone.utc).isoformat(),
        "extraction_seconds": round(time.monotonic() - started, 3),
        "container_started": False,
    })
    with (directory / "manifest.json").open("x") as stream:
        json.dump(result, stream, indent=2)
    print("FRONTEND_ARTIFACT_DIR=" + str(directory))
    print("UPLOAD_SOURCE_DIR=" + str(site))
    for key in ("file_count", "total_bytes", "tree_sha256", "source_map_count",
                "relative_api_literal_files", "discord_webhook_literal_files", "extraction_seconds"):
        print(key.upper() + "=" + str(result[key]))
    print("STATIC_FILES_EXTRACTED_NO_CONTAINER_START_NO_AWS_CHANGE")
    if result["discord_webhook_literal_files"]:
        print("STOP: 웹훅 주소 흔적 발견. 업로드 전 비밀 노출 여부 검토 필요")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, RuntimeError, OSError, KeyError) as error:
        raise SystemExit("STOP: " + str(error))
