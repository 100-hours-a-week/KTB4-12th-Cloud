"""고정 Manifest의 자산만 S3에서 읽어 크기·SHA256 확인 후 공유 볼륨 초기화."""
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import sys

import boto3
from botocore.config import Config

BUCKET = os.environ["ASSET_BUCKET"]
PREFIX = os.environ["ASSET_PREFIX"]
MANIFEST_SHA256 = os.environ["ASSET_MANIFEST_SHA256"]
EXPECTED_PATHS = {"assets/models/" + name for name in
                  ("model_q4f16.onnx", "model_q4f16.onnx_data", "tokenizer.json", "tokenizer_config.json")}


def main():
    client = boto3.client("s3", region_name="ap-northeast-2", config=Config(
        connect_timeout=10, read_timeout=60, retries={"mode": "standard", "max_attempts": 3}))
    response = client.get_object(Bucket=BUCKET, Key=PREFIX + "manifest.json")
    with closing(response["Body"]) as stream:
        raw = stream.read(8 * 1024 * 1024 + 1)
    if len(raw) > 8 * 1024 * 1024 or hashlib.sha256(raw).hexdigest() != MANIFEST_SHA256:
        raise ValueError("manifest integrity mismatch")
    files = json.loads(raw)["files"]
    if len(files) != 4 or {item["path"] for item in files} != EXPECTED_PATHS:
        raise ValueError("unexpected asset set")
    root = Path("/shared")
    for item in files:
        relative = PurePosixPath(item["path"])
        if relative.is_absolute() or ".." in relative.parts or item["key"] != PREFIX + item["path"]:
            raise ValueError("unsafe asset path")
        target = root / str(relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(target.name + ".partial")
        response = client.get_object(Bucket=BUCKET, Key=item["key"])
        if response["ContentLength"] != item["bytes"]:
            raise ValueError("asset content length mismatch")
        digest = hashlib.sha256()
        size = 0
        # StreamingBody.__enter__는 내부 HTTP 스트림을 반환하므로 closing으로 SDK 객체 유지.
        with closing(response["Body"]) as stream, temporary.open("wb") as output:
            for chunk in stream.iter_chunks(chunk_size=1024 * 1024):
                size += len(chunk)
                if size > item["bytes"]:
                    raise ValueError("asset size exceeded")
                digest.update(chunk)
                output.write(chunk)
        if size != item["bytes"] or digest.hexdigest() != item["sha256"]:
            raise ValueError("asset hash mismatch")
        os.chmod(temporary, 0o444)
        temporary.replace(target)
        print("자산 검증 완료:", item["path"], "/ bytes:", size)
    (root / "media").mkdir(exist_ok=True)
    # 기존 DB 접속 URL의 sslrootcert 경로와 동일하게 배치.
    tls = root / "tls"
    tls.mkdir(exist_ok=True)
    shutil.copyfile("/bootstrap/rds-ca.pem", tls / "rds-ca.pem")
    os.chmod(tls / "rds-ca.pem", 0o444)
    print("AI_ASSETS_INITIALIZED — 모델 4개 검증 완료; media 디렉터리는 비어 있음")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print("AI 자산 초기화 실패 — " + type(error).__name__ + "; 오류 원문 미출력", file=sys.stderr)
        sys.exit(1)
