#!/usr/bin/env bash
# 검증한 운영 amd64 Artifact만 복사. 빌드/배포/DB 변경/기존 태그 삭제 없음.
set -euo pipefail

if [[ "${1:-}" != '--approved-production-image-copy' || "$#" != 1 ]]; then
  printf 'STOP: 운영 이미지 복사 승인 옵션 필요\n' >&2
  exit 1
fi

production_registry='721744297924.dkr.ecr.ap-northeast-2.amazonaws.com'
production_repository='seonjalal/production/backend'
production_tag='v1-1.3.0-amd64'
production_source='peterj0117/seonjalal-be@sha256:5974be93598c672bcf1d321da009ed67cd6cbba93d824681acfb35ca95b86f6d'
production_target="${production_registry}/${production_repository}:${production_tag}"
production_aws=(aws --profile seonjalal-production --region ap-northeast-2 --no-cli-pager)
production_script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)

# 모든 사전 검사를 업로드 전에 수행. 계정/Repository/태그 불일치는 중단.
production_account=$("${production_aws[@]}" sts get-caller-identity --query Account --output text)
[[ "$production_account" == '721744297924' ]] || { printf 'STOP: 계정 불일치\n'; exit 1; }
production_mutability=$("${production_aws[@]}" ecr describe-repositories \
  --repository-names "$production_repository" --query 'repositories[0].imageTagMutability' --output text)
[[ "$production_mutability" == 'IMMUTABLE' ]] || { printf 'STOP: ECR 불변 태그 정책 불일치\n'; exit 1; }
production_existing=$("${production_aws[@]}" ecr list-images --repository-name "$production_repository" \
  --filter tagStatus=TAGGED --output json | python3 -c '
import json, sys
images = json.load(sys.stdin)["imageIds"]
print(sum(image.get("imageTag") == "v1-1.3.0-amd64" for image in images))
')
if [[ "$production_existing" != '0' ]]; then
  # 이미 존재하면 읽기 검증 후 재사용. 다른 이미지면 오류로 중단, 삭제/Push 금지.
  python3 "$production_script_dir/verify-production-backend.py"
  printf 'EXISTING_TAG_REUSED_NO_ECR_WRITE\n'
  exit 0
fi
production_push_help=$(docker image push --help)
[[ "$production_push_help" == *'--platform'* ]] || { printf 'STOP: Docker의 단일 플랫폼 Push 지원 확인 필요\n'; exit 1; }
docker version --format '{{.Server.APIVersion}}' | python3 -c '
import sys
version = tuple(int(part) for part in sys.stdin.read().strip().split("."))
if version < (1,46):
    raise SystemExit("STOP: Docker Server API 1.46 이상 필요")
'
docker buildx imagetools inspect --raw "$production_source" | python3 -c '
import json, sys
manifest = json.load(sys.stdin)
if manifest.get("config", {}).get("digest") != "sha256:b93ca625008ea5eb57e600dc56e033a7ca45bce8842ac76b789eb0da6563e1f2":
    raise SystemExit("STOP: 원본 운영 Config Digest 불일치")
print("SOURCE_PRODUCTION_CONFIG_VERIFIED")
'
docker pull --platform linux/amd64 "$production_source"
production_platform=$(docker image inspect "$production_source" --format '{{.Os}}/{{.Architecture}}')
[[ "$production_platform" == 'linux/amd64' ]] || { printf 'STOP: 로컬 이미지 플랫폼 불일치\n'; exit 1; }

# 비밀번호는 파이프로 Docker에만 전달. shell 추적(set -x)으로 실행 금지.
"${production_aws[@]}" ecr get-login-password |
  docker login --username AWS --password-stdin "$production_registry"
docker tag "$production_source" "$production_target"
docker image push --platform linux/amd64 "$production_target"

# ECR이 돌려준 Digest로 다시 조회. Manifest 형식 변환 시 최상위 Digest 동일성 강제 금지.
production_digest=$("${production_aws[@]}" ecr describe-images --repository-name "$production_repository" \
  --image-ids "imageTag=$production_tag" --query 'imageDetails[0].imageDigest' --output text)
[[ "$production_digest" =~ ^sha256:[0-9a-f]{64}$ ]] || { printf 'STOP: 목적지 Digest 형식 확인 필요\n'; exit 1; }
production_destination="${production_registry}/${production_repository}@${production_digest}"
# 원본과 목적지의 전체 Config/Layer Descriptor를 비교. 환경변수 원문 조회 없음.
python3 - "$production_source" "$production_destination" <<'PY'
import json
import subprocess
import sys

def manifest(reference):
    result = subprocess.run(
        ["docker", "buildx", "imagetools", "inspect", "--raw", reference],
        check=True, capture_output=True, text=True,
    )
    return json.loads(result.stdout)

source, target = (manifest(reference) for reference in sys.argv[1:])
if source.get("config") != target.get("config") or not source.get("layers") or source["layers"] != target.get("layers"):
    raise SystemExit("STOP: 목적지 Config/Layer Descriptor 불일치. 삭제/재배포 없이 조사 필요")
print("PRODUCTION_ECR_CONFIG_AND_LAYERS_VERIFIED")
PY
printf 'PRODUCTION_BACKEND_IMAGE=%s\n' "$production_destination"
printf 'NO_BUILD_NO_ECS_DEPLOY_NO_DB_OR_TRAFFIC_CHANGE\n'
