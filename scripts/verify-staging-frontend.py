#!/usr/bin/env python3
"""사용자 Mac에서 실행: S3 읽기만 수행하여 FE 원본 Manifest와 내용 대조."""

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import subprocess
import tempfile
import time
from datetime import datetime, timezone

ACCOUNT = "721744297924"
BUCKET = "seonjalal-v2-frontend-721744297924-ap-northeast-2"
PREFIX = "frontend/staging/"
ARTIFACT = Path("/private/tmp/seonjalal-fe-1.3.2.2h0yqnp7")
TREE = "0847c632658c6a3d7eaabcd81205884de1774c3e466867e0d9c4288d01740158"


def validate_manifest(manifest):
    files = manifest["files"]
    serialized = json.dumps(files, sort_keys=True, separators=(",", ":")).encode()
    if (len(files) != 28 or sum(item["bytes"] for item in files) != 3628454
            or hashlib.sha256(serialized).hexdigest() != TREE):
        raise ValueError("원본 Manifest 파일 수·바이트·트리 해시 불일치")
    for item in files:
        path = PurePosixPath(item["path"])
        if path.is_absolute() or ".." in path.parts or str(path) != item["path"]:
            raise ValueError("안전하지 않은 파일 경로")
    return files


def verify_file(path, expected):
    content = path.read_bytes()
    if len(content) != expected["bytes"] or hashlib.sha256(content).hexdigest() != expected["sha256"]:
        raise ValueError("파일 내용 불일치: " + expected["path"])


def aws(*args):
    # 허용 동작은 계정 확인·객체 목록 조회·단일 객체 다운로드뿐이다.
    result = subprocess.run(
        ["aws", *args, "--profile", "seonjalal-production", "--region", "ap-northeast-2",
         "--no-cli-pager", "--output", "json", "--cli-connect-timeout", "5",
         "--cli-read-timeout", "30"],
        capture_output=True, timeout=90,
    )
    if result.returncode:
        raise RuntimeError("AWS 읽기 실패: " + "/".join(args[:2]) + "; 재업로드 없이 중단")
    return json.loads(result.stdout)


def main():
    os.umask(0o077)
    files = validate_manifest(json.loads((ARTIFACT / "manifest.json").read_text()))
    # 로컬 파일도 원본과 일치해야 같은 Artifact의 원격 검증으로 취급한다.
    for item in files:
        verify_file(ARTIFACT / "site" / item["path"], item)
    if aws("sts", "get-caller-identity")["Account"] != ACCOUNT:
        raise ValueError("AWS 계정 불일치")
    remote = aws("s3api", "list-objects-v2", "--bucket", BUCKET, "--prefix", PREFIX)
    expected_sizes = {PREFIX + item["path"]: item["bytes"] for item in files}
    actual_sizes = {item["Key"]: item["Size"] for item in remote.get("Contents", [])}
    if actual_sizes != expected_sizes:
        raise ValueError("S3 객체 경로·개수·크기 불일치; 삭제/재업로드 없이 중단")
    directory = Path(tempfile.mkdtemp(prefix="seonjalal-fe-s3-verify.", dir="/private/tmp"))
    started = time.monotonic()
    for item in files:
        destination = directory / "download" / item["path"]
        destination.parent.mkdir(parents=True, exist_ok=True)
        aws("s3api", "get-object", "--bucket", BUCKET, "--key", PREFIX + item["path"], str(destination))
        verify_file(destination, item)
    result = {
        "verified_utc": datetime.now(timezone.utc).isoformat(),
        "bucket": BUCKET, "prefix": PREFIX,
        "file_count": len(files), "total_bytes": sum(expected_sizes.values()),
        "tree_sha256": TREE, "sha256_matched_files": len(files),
        "download_and_hash_seconds": round(time.monotonic() - started, 3),
        "aws_write_performed": False,
    }
    (directory / "verification.json").write_text(json.dumps(result, indent=2) + "\n")
    for key, value in result.items():
        print(key.upper() + "=" + str(value))
    print("VERIFICATION_RECORD=" + str(directory / "verification.json"))
    print("S3_ARTIFACT_VERIFIED_VIEWER_AND_API_NOT_YET_VERIFIED")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, KeyError, OSError, RuntimeError, subprocess.TimeoutExpired) as error:
        raise SystemExit("STOP: " + str(error))
