"""Task-local asset initialization: pinned manifest, model lock, no provider calls."""
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import sys


def validate_manifest(raw, expected_hash, prefix, model_lock):
    if len(raw) > 1024 * 1024 or hashlib.sha256(raw).hexdigest() != expected_hash:
        raise ValueError("ASSET_MANIFEST_HASH_MISMATCH")
    data = json.loads(raw)
    if not isinstance(data, dict) or set(data) != {"files"}:
        raise ValueError("ASSET_MANIFEST_FORMAT")
    files = data["files"]
    if not isinstance(files, list) or not 1 <= len(files) <= 10000:
        raise ValueError("ASSET_MANIFEST_FILES")
    seen, total = {}, 0
    for item in files:
        if not isinstance(item, dict) or set(item) != {"path", "key", "bytes", "sha256"}:
            raise ValueError("ASSET_ENTRY_FORMAT")
        path, key = item["path"], item["key"]
        if (not isinstance(path, str) or not isinstance(key, str)
                or path != str(PurePosixPath(path)) or ".." in PurePosixPath(path).parts
                or not path.startswith(("assets/models/", "media/"))
                or path.startswith("/") or path in seen or "\\" in path
                or not key.startswith(prefix) or ".." in key or "\\" in key):
            raise ValueError("ASSET_PATH_REJECTED")
        if (type(item["bytes"]) is not int or not 0 < item["bytes"] <= 256 * 1024 * 1024
                or not isinstance(item["sha256"], str)
                or not re.fullmatch(r"[0-9a-f]{64}", item["sha256"])):
            raise ValueError("ASSET_SIZE_OR_HASH_REJECTED")
        seen[path] = item
        total += item["bytes"]
    if total > 1024 * 1024 * 1024:
        raise ValueError("ASSET_TOTAL_SIZE_REJECTED")
    # 배포 Manifest와 이미지에 고정된 모델의 크기·Hash를 함께 검증한다.
    for model in model_lock["files"]:
        item = seen.get("assets/models/" + model["path"])
        if item is None or (item["bytes"], item["sha256"]) != (model["bytes"], model["sha256"]):
            raise ValueError("IMAGE_MODEL_LOCK_MISMATCH")
    return files


def download_verified(client, bucket, item, root):
    destination = root / item["path"]
    destination.parent.mkdir(parents=True, exist_ok=True)
    digest, size = hashlib.sha256(), 0
    with client.get_object(Bucket=bucket, Key=item["key"])["Body"] as body:
        with destination.open("xb") as output:
            for chunk in iter(lambda: body.read(1024 * 1024), b""):
                size += len(chunk)
                if size > item["bytes"]:
                    raise ValueError("ASSET_EXCEEDS_DECLARED_SIZE")
                digest.update(chunk)
                output.write(chunk)
    if size != item["bytes"] or digest.hexdigest() != item["sha256"]:
        raise ValueError("ASSET_CONTENT_MISMATCH")
    destination.chmod(0o444)


def prepare_media_directory(root):
    # BE가 상품 이미지를 제공하는 배포에서도 Search/Chat의 정적 경로는 존재해야 한다.
    (root / "media").mkdir(parents=True, exist_ok=True)


def main():
    import boto3
    from botocore.config import Config

    client = boto3.client("s3", region_name=os.environ["AWS_REGION"],
                          config=Config(connect_timeout=5, read_timeout=30, retries={"max_attempts": 3}))
    bucket = os.environ["ASSET_BUCKET"]
    with client.get_object(Bucket=bucket, Key=os.environ["ASSET_MANIFEST_KEY"])["Body"] as body:
        raw = body.read(1024 * 1024 + 1)
    model_lock = json.loads(Path("/opt/encoder/model.lock.json").read_text())
    files = validate_manifest(raw, os.environ["ASSET_MANIFEST_SHA256"], os.environ["ASSET_PREFIX"], model_lock)
    for item in files:
        download_verified(client, bucket, item, Path("/shared"))
    prepare_media_directory(Path("/shared"))
    directory = Path("/shared/tls")
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "rds-ca.pem").write_text(os.environ["RDS_CA_PEM"])
    os.chown("/runtime", 10001, 10001)
    os.chmod("/runtime", 0o700)
    print("AI_ASSETS_AND_CA_VERIFIED")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print("AI_ASSET_INIT_FAILED:" + type(exc).__name__, file=sys.stderr)
        raise SystemExit(1) from None
