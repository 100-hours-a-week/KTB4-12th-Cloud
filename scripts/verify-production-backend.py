#!/usr/bin/env python3
"""기존 운영 ECR 태그를 읽기 전용으로 검증. 로그인/Push/삭제/배포 없음."""
import json
import re
import subprocess
import sys

ACCOUNT = "721744297924"
REPOSITORY = "seonjalal/production/backend"
TAG = "v1-1.3.0-amd64"
SOURCE = "peterj0117/seonjalal-be@sha256:5974be93598c672bcf1d321da009ed67cd6cbba93d824681acfb35ca95b86f6d"
CONFIG = "sha256:b93ca625008ea5eb57e600dc56e033a7ca45bce8842ac76b789eb0da6563e1f2"


def compare(source, response):
    # 원본 Config/Layer 참조 전체 비교. Index는 별도 조사 대상으로 중단.
    if response.get("failures") or len(response.get("images", [])) != 1:
        raise ValueError("ECR 이미지 조회 실패 또는 결과 개수 불일치")
    image = response["images"][0]
    if image.get("registryId") != ACCOUNT or image.get("repositoryName") != REPOSITORY:
        raise ValueError("ECR 대상 계정/저장소 불일치")
    digest = image.get("imageId", {}).get("imageDigest", "")
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
        raise ValueError("목적지 Digest 형식 불일치")
    target = json.loads(image["imageManifest"])
    if source.get("config", {}).get("digest") != CONFIG:
        raise ValueError("원본 운영 Config Digest 불일치")
    if not source.get("layers") or source["config"] != target.get("config") or source["layers"] != target.get("layers"):
        raise ValueError("기존 태그의 Config/Layer Descriptor 불일치. 삭제/덮어쓰기 금지")
    return digest


def read_json(command):
    result = subprocess.run(command, capture_output=True, text=True, timeout=60)
    if result.returncode:
        raise ValueError("읽기 명령 실패: " + command[0])
    return json.loads(result.stdout)


def main():
    source = read_json(["docker", "buildx", "imagetools", "inspect", "--raw", SOURCE])
    response = read_json([
        "aws", "--profile", "seonjalal-production", "--region", "ap-northeast-2", "--no-cli-pager",
        "ecr", "batch-get-image", "--registry-id", ACCOUNT, "--repository-name", REPOSITORY,
        "--image-ids", "imageTag=" + TAG, "--output", "json",
    ])
    digest = compare(source, response)
    print("PRODUCTION_ECR_CONFIG_AND_LAYERS_VERIFIED")
    print(f"PRODUCTION_BACKEND_IMAGE={ACCOUNT}.dkr.ecr.ap-northeast-2.amazonaws.com/{REPOSITORY}@{digest}")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        detail = str(error) if isinstance(error, ValueError) and not isinstance(error, json.JSONDecodeError) else type(error).__name__
        print("STOP: " + detail, file=sys.stderr)
        sys.exit(1)
