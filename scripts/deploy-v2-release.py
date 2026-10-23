#!/usr/bin/env python3
# ==============================================================================
# V2 릴리스 배포 스크립트 (GitHub Actions cd-v2.yml에서만 실행 가능)
# ------------------------------------------------------------------------------
# 역할
#   - BE: ECR 이미지 태그를 digest로 고정 -> 현재 ECS Task Definition을 복제해 이미지만 교체
#     -> 새 revision 등록 -> ECS Service 갱신 -> 안정화 확인.
#   - FE: ECR 이미지에서 정적 파일을 꺼내 S3(frontend/<env>/)에 업로드 -> CloudFront 무효화.
# 트리거/호출
#   - cd-v2.yml 의 "Validate all targets..." 단계. 로컬/다른 워크플로에서 실행하면 main()이 거부.
#   - --apply가 없으면 읽기 전용 검증만 한다(AWS 변경 API는 Aws 래퍼가 차단).
# 입력 -> 출력
#   - 입력: --environment --component --backend-version --frontend-version --backend-layout
#     --compatibility-reviewed --state-dir [--apply], 환경변수 V2_FRONTEND_BUCKET 등.
#   - 출력: state-dir/receipt.json(서비스, 이전/신규 Task Definition, 이미지 참조, 롤백 결과),
#     stdout 마커(V2_RELEASE_VALIDATED_NO_AWS_WRITE / V2_RELEASE_DEPLOYED_AND_INFRA_HEALTH_VERIFIED),
#     실패 시 stderr "STOP: 사유"와 종료 코드 1. 오류 원문은 노출하지 않는다(Secret 유출 방지).
# 전체 흐름(deliver)
#   1) AWS 계정 일치 확인 -> 2) 선택된 모든 대상 사전 검증(prepare_*) -> 3) receipt 기록
#   -> 4) apply가 아니면 여기서 종료 -> 5) BE 서비스를 순서대로 갱신 -> 6) FE 업로드
#   -> 7) 실패 시 FE index.html 복구 + BE 이전 revision 복구 시도
# 실패/롤백 범위
#   - 복구 대상은 "앱"(ECS revision, S3 index.html)뿐. DB 스키마/데이터는 자동 복구하지 않으며
#     그래서 호환성 검토 체크(--compatibility-reviewed)와 Flyway 비활성 조건을 강제한다.
#   - FE 새 assets는 업로드된 채 남지만 index.html이 이전 것으로 돌아가면 참조되지 않는다.
#   - 자동 복구가 실패하면 receipt.rollback에 "manual intervention required" 기록.
# 이미지 저장소 규약(코드로 확인)
#   - BE: ECR seonjalal/production/backend, 태그 payment-server-<v> / main-backend-<v> / v1-<v>-amd64
#   - FE: ECR seonjalal/production/frontend, 태그 <v>. (staging도 이 저장소를 사용)
#   - ECR 태그가 IMMUTABLE이 아니면 거부한다. 태그를 게시하는 쪽은 FE/BE 저장소의 CI이며
#     이 파일에서는 태그 규약만 확인 가능.
# ==============================================================================
"""V2 수동 Delivery: ECR Digest 고정→ECS Service 갱신/S3 정적 배포. 기본은 읽기 검증만."""
import argparse
import copy
import hashlib
import json
import mimetypes
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

# 배포 대상 AWS 계정/리전/FE 버킷 이름은 코드에 고정. 다른 계정이면 deliver()가 거부한다.
ACCOUNT = "721744297924"
REGION = "ap-northeast-2"
REGISTRY = f"{ACCOUNT}.dkr.ecr.{REGION}.amazonaws.com"
BUCKET = f"seonjalal-v2-frontend-{ACCOUNT}-{REGION}"
# AWS를 "변경"하는 API 목록. --apply 없이 이 목록의 호출을 시도하면 Aws 래퍼가 차단(dry-run 안전장치).
# 새 변경 API를 추가하면 반드시 여기에도 넣어야 dry-run 보호가 유지된다.
MUTATIONS = {"register-task-definition", "update-service", "put-object", "create-invalidation"}
# describe-task-definition 결과 중 register-task-definition에 다시 넣을 수 있는 필드만 허용 목록으로 복제
# (revision, status 등 읽기 전용 필드 제외). 목록에 없는 새 필드는 복제되지 않는다는 점에 주의.
TASK_KEYS = ("family", "taskRoleArn", "executionRoleArn", "networkMode", "containerDefinitions", "volumes",
             "placementConstraints", "requiresCompatibilities", "cpu", "memory", "pidMode", "ipcMode",
             "proxyConfiguration", "inferenceAccelerators", "ephemeralStorage", "runtimePlatform", "enableFaultInjection")


# 예상된 중단 사유(검증 실패 등). main()은 이 예외의 메시지만 출력하고 다른 예외는 타입명만 출력.
class DeliveryError(Exception):
    pass


class AwsError(DeliveryError):
    def __init__(self, operation, code):
        self.code = code
        super().__init__(operation + " CODE=" + code + ". 원문 비공개")


# 검증 헬퍼: 조건이 거짓이면 즉시 DeliveryError. 이 파일의 모든 안전 검사는 이 함수를 통한다.
def require(condition, message):
    if not condition:
        raise DeliveryError(message)


# 로컬 명령(docker) 실행. 실패 시 stderr 원문을 숨기고 명령 이름만 노출(로그 유출 방지).
def command(args, timeout=660):
    result = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    if result.returncode:
        raise DeliveryError("로컬 명령 실패: " + args[0] + ". 원문 비공개")
    return result.stdout.strip()


# AWS CLI 호출 래퍼. (1) apply가 아니면 MUTATIONS 차단 (2) 요청 본문은 argv가 아니라 0600 임시 파일로
# 전달 (3) 오류는 AWS 오류 코드만 남기고 원문 비공개. 인증은 워크플로가 설정한 OIDC 단기 자격증명 사용.
class Aws:
    def __init__(self, apply):
        self.apply = apply

    def __call__(self, service, operation, *args, body=None):
        require(operation not in MUTATIONS or self.apply, "검증 모드에서 AWS 변경 차단")
        base = ["aws", "--region", REGION, "--no-cli-pager", "--cli-connect-timeout", "5", "--cli-read-timeout", "30",
                service, operation, *args, "--output", "json"]
        if body is not None:
            # Task Definition에 기존 민감 설정이 있을 수 있으므로 argv/로그/Artifact로 노출하지 않음.
            with tempfile.TemporaryDirectory(prefix="v2-request-") as temporary:
                path = Path(temporary) / "request.json"
                path.write_text(json.dumps(body))
                path.chmod(0o600)
                result = subprocess.run(base + ["--cli-input-json", "file://" + str(path)],
                                        capture_output=True, text=True, timeout=90)
        else:
            result = subprocess.run(base, capture_output=True, text=True, timeout=660)
        if result.returncode:
            match = re.search(r"An error occurred \(([A-Za-z0-9_.-]+)\)", result.stderr)
            raise AwsError(operation, match[1] if match else "CLI_OR_TRANSPORT_ERROR")
        return json.loads(result.stdout) if result.stdout.strip() else {}


# 릴리스 버전 형식 검사(예 1.2.3, 접미사 허용 예 2.0.0-rc.1). 태그 문자열 조합 전 인젝션/오타 방지.
def version(value):
    require(bool(re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z][0-9A-Za-z.-]*)?", value)),
            "릴리스 버전 형식 불일치")
    return value


# 태그 -> digest 고정. ECR 저장소가 불변 태그인지 확인한 뒤 태그가 가리키는 sha256 digest를 조회하고
# repo@sha256:... 형태 URI를 반환한다. 이후 배포는 태그가 아니라 digest 기준이므로 같은 태그가
# 나중에 바뀌어도 배포 내용이 달라지지 않는다.
def image(api, repository, tag):
    metadata = api("ecr", "describe-repositories", "--repository-names", repository)["repositories"][0]
    require(metadata["imageTagMutability"] == "IMMUTABLE", "릴리스 ECR 불변 태그 설정 필요")
    details = api("ecr", "describe-images", "--repository-name", repository,
                  "--image-ids", "imageTag=" + tag)["imageDetails"]
    require(len(details) == 1 and re.fullmatch(r"sha256:[0-9a-f]{64}", details[0]["imageDigest"]), "이미지 Digest 확인 실패")
    return REGISTRY + "/" + repository + "@" + details[0]["imageDigest"]


# ECS Service 한 개를 조회. 없거나 실패 목록이 있으면 중단.
def service(api, cluster, name):
    result = api("ecs", "describe-services", "--cluster", cluster, "--services", name)
    require(not result.get("failures") and len(result.get("services", [])) == 1, "ECS Service 미준비: " + name)
    return result["services"][0]


# 배포 전/후 "정상 기준" 검사. 통과 조건: ACTIVE, desired>=2, running==desired, pending 0,
# 배포 1건 COMPLETED, ECS Rolling, minimumHealthy>=100 & maximum>=200(무중단 교체 여지),
# Circuit Breaker enable+rollback. 이미 비정상이거나 다른 배포가 진행 중이면 새 배포를 시작하지 않는다.
def baseline(value):
    config = value.get("deploymentConfiguration", {})
    breaker = config.get("deploymentCircuitBreaker", {})
    require(value.get("status") == "ACTIVE" and value.get("desiredCount", 0) >= 2, "정상 Service 최소2개 기준 필요")
    require(value.get("runningCount") == value["desiredCount"] and value.get("pendingCount") == 0, "기존 Service 비정상/배포중")
    deployments = value.get("deployments", [])
    require(len(deployments) == 1 and deployments[0].get("rolloutState") == "COMPLETED", "완료된 단일 배포 기준 필요")
    require(value.get("deploymentController", {}).get("type", "ECS") == "ECS", "ECS Rolling만 지원")
    require(config.get("strategy", "ROLLING") == "ROLLING", "Rolling 배포 전략 필요")
    require(config.get("minimumHealthyPercent", 0) >= 100 and config.get("maximumPercent", 0) >= 200,
            "정상 Task 유지/신규 Task 여유 용량 설정 필요")
    require(breaker.get("enable") and breaker.get("rollback"), "Circuit Breaker 자동 rollback 필요")


# 현재 Task Definition을 복제하고 대상 컨테이너의 이미지만 digest URI로 교체한 새 정의를 만든다.
# 다른 설정(환경변수, Role 등)은 그대로 유지. 검토된 ECS on EC2 + awsvpc 구성만 허용.
def render_task(task, container, uri):
    output = {key: copy.deepcopy(task[key]) for key in TASK_KEYS if key in task}
    matches = [item for item in output["containerDefinitions"] if item["name"] == container]
    require(len(matches) == 1, "이미지 교체 대상 컨테이너 불일치")
    require(output.get("networkMode") == "awsvpc" and "EC2" in output.get("requiresCompatibilities", []),
            "검토한 ECS on EC2/awsvpc Task만 지원")
    matches[0]["image"] = uri
    return output


# BE 사전 검증과 배포 계획 수립(읽기 전용). 서비스별로 이미지 digest, 현재 revision(previous),
# 새 정의를 계산한다.
# - legacy-v1: 단일 backend 서비스(이전 시험용). split-v2: payment-server와 main-backend 2개 서비스.
# - split-v2 추가 조건: Task가 SPRING_FLYWAY_ENABLED=false 일 것(DB 마이그레이션은 배포 전에
#   별도로 끝내야 함), Service Connect 활성, Main의 PAYMENT_SERVER_BASE_URL=http://payment-server:8090,
#   Payment의 Service Connect alias 존재, 두 서비스의 namespace 동일.
# - 배포 순서는 반환 리스트 순서(payment-server -> main-backend).
def prepare_backend(api, env, release, layout, compatibility):
    require(compatibility, "DB 스키마·신구 API/Payment 호환성 검토 체크 필요")
    version(release)
    cluster = f"seonjalal-v2-{env}-cluster"
    names = ([("backend", "backend", "v1-" + release + "-amd64")] if layout == "legacy-v1" else
             [("payment-server", "payment-server", "payment-server-" + release),
              ("main-backend", "main-backend", "main-backend-" + release)])
    plans = []
    for suffix, container, tag in names:
        name = f"seonjalal-v2-{env}-" + suffix
        uri = image(api, "seonjalal/production/backend", tag)
        current = service(api, cluster, name)
        baseline(current)
        old = current["taskDefinition"]
        task = api("ecs", "describe-task-definition", "--task-definition", old)["taskDefinition"]
        require(task.get("family") == name, "Task family 환경 경계 불일치")
        definition = render_task(task, container, uri)
        target = next(item for item in definition["containerDefinitions"] if item["name"] == container)
        if layout == "split-v2":
            settings = {item["name"]: item["value"] for item in target.get("environment", [])}
            require(settings.get("SPRING_FLYWAY_ENABLED") == "false", "분리형 서비스는 사전 DB Migration 완료 후 Flyway 비활성 Task로 배포 필요")
            connect = current.get("serviceConnectConfiguration", {})
            require(connect.get("enabled"), "분리형 Main/Payment Service Connect 구성이 먼저 필요")
            if suffix == "main-backend":
                require(settings.get("PAYMENT_SERVER_BASE_URL") == "http://payment-server:8090",
                        "Main의 Payment 내부주소/Service Connect alias 계약 불일치")
            else:
                require(any(alias.get("dnsName") == "payment-server" and alias.get("port") == 8090
                            for item in connect.get("services", []) for alias in item.get("clientAliases", [])),
                        "Payment Service Connect endpoint 미준비")
        plans.append({"service": name, "cluster": cluster, "previous": old, "definition": definition, "image": uri,
                      "namespace": current.get("serviceConnectConfiguration", {}).get("namespace")})
    if layout == "split-v2":
        require(all(p["namespace"] for p in plans) and len({p["namespace"] for p in plans}) == 1,
                "Main/Payment Service Connect namespace 불일치")
    return plans


# 서비스가 안정화될 때까지 대기 후, 실제로 expected revision인지 재확인한다.
# Circuit Breaker가 자동 롤백해도 waiter는 성공할 수 있으므로 "안정화 = 새 버전 배포 성공"이 아님.
def stable_at(api, plan, expected):
    # Circuit Breaker가 이전 버전으로 복귀해도 waiter는 성공할 수 있어 정확한 revision을 재검사.
    api("ecs", "wait", "services-stable", "--cluster", plan["cluster"], "--services", plan["service"])
    current = service(api, plan["cluster"], plan["service"])
    baseline(current)
    require(current["taskDefinition"] == expected, "안정화됐으나 요청 Revision 아님(rollback 또는 동시 배포)")


# [변경] 서비스별로: 동시 배포 여부 재확인 -> 새 Task Definition revision 등록 -> update-service
# -> 안정화 확인. attempted에 등록 직후 추가해 실패 시 rollback_backend가 되돌릴 대상이 된다.
# 앞 서비스에서 실패하면 뒤 서비스는 시작하지 않는다.
def apply_backend(api, plans, attempted):
    for plan in plans:
        current = service(api, plan["cluster"], plan["service"])
        baseline(current)
        require(current["taskDefinition"] == plan["previous"], "계획 이후 동시 배포 감지")
        registered = api("ecs", "register-task-definition", body=plan["definition"])
        plan["new"] = registered["taskDefinition"]["taskDefinitionArn"]
        attempted.append(plan)
        api("ecs", "update-service", "--cluster", plan["cluster"], "--service", plan["service"],
            "--task-definition", plan["new"])
        stable_at(api, plan, plan["new"])


# [롤백] 시도한 서비스를 역순으로 이전 revision으로 되돌리고 안정화를 확인한다.
# 현재 revision이 previous/new 어느 쪽도 아니면(다른 사람이 배포) 자동 롤백을 중단한다.
# 새 Task Definition revision 자체는 삭제하지 않고 남긴다.
def rollback_backend(api, attempted):
    for plan in reversed(attempted):
        current = service(api, plan["cluster"], plan["service"])
        require(current["taskDefinition"] in (plan["previous"], plan["new"]), "다른 배포가 감지돼 자동 rollback 중단")
        if current["taskDefinition"] != plan["previous"]:
            api("ecs", "update-service", "--cluster", plan["cluster"], "--service", plan["service"],
                "--task-definition", plan["previous"])
        stable_at(api, plan, plan["previous"])


# FE 산출물 안전 검사: symlink 금지, index.html 필수, 파일 2000개/100MiB 이하, 허용 문자 경로만,
# 숨김 파일 금지, 공개 source map(.map) 금지, html은 index.html 하나만(SPA 전제).
def validate_site(site):
    files = sorted(site.rglob("*"))
    require(all(not item.is_symlink() for item in files), "정적 산출물 symlink 금지")
    files = [item for item in files if item.is_file()]
    require((site / "index.html").is_file() and 0 < len(files) <= 2000, "정적 index/파일 수 확인 필요")
    require(sum(item.stat().st_size for item in files) <= 100 * 1024**2, "정적 파일 크기 상한 초과")
    for path in files:
        relative = path.relative_to(site).as_posix()
        require(bool(re.fullmatch(r"[A-Za-z0-9_./@-]+", relative)) and
                not any(part.startswith(".") for part in path.relative_to(site).parts), "숨김파일/허용외 경로 금지")
        require(path.suffix != ".map", "공개 source map 배포 금지")
        require(path.suffix != ".html" or relative == "index.html", "현재 Delivery는 단일 SPA index만 지원")
    return files


# S3 객체 다운로드. 키가 없으면 None 반환(존재 여부 확인에도 사용), 그 외 오류는 전파.
def get_object(api, bucket, key, destination):
    try:
        return api("s3api", "get-object", "--bucket", bucket, "--key", key, str(destination))
    except AwsError as error:
        if error.code in ("NoSuchKey", "404"):
            return None
        raise


# FE 사전 검증(읽기 전용 + 이미지 pull). 확인 항목: 버킷/배포 ID 환경변수, 공개 접근 차단, 버전 관리,
# CloudFront가 환경 prefix(frontend/<env>) 전용 OAC 원본을 쓰는지, (production) /api/* 가 BE 원본으로
# 라우팅되는지, 버킷 정책이 해당 CloudFront만 읽게 허용하는지.
# 그다음 이미지에서 /usr/share/nginx/html을 복사해 검사하고, 이미 있는 같은 이름 asset이
# 내용이 다르면 중단한다(구버전 탭이 쓰는 해시 파일을 덮어쓰지 않기 위함).
def prepare_frontend(api, env, release, directory):
    version(release)
    bucket = os.environ.get("V2_FRONTEND_BUCKET", "")
    distribution = os.environ.get("V2_CLOUDFRONT_DISTRIBUTION_ID", "")
    require(bucket == BUCKET and bool(re.fullmatch(r"[A-Z0-9]{8,32}", distribution)), "FE 버킷/CloudFront 환경 설정 필요")
    prefix = f"frontend/{env}/"
    access = api("s3api", "get-public-access-block", "--bucket", bucket)["PublicAccessBlockConfiguration"]
    require(all(access.get(key) is True for key in ("BlockPublicAcls", "IgnorePublicAcls", "BlockPublicPolicy", "RestrictPublicBuckets")),
            "공유 S3 공개 접근 차단 유지 필요")
    require(api("s3api", "get-bucket-versioning", "--bucket", bucket).get("Status") == "Enabled", "S3 버전 관리 필요")
    distro = api("cloudfront", "get-distribution", "--id", distribution)["Distribution"]
    config = distro["DistributionConfig"]
    require(config.get("Enabled") and distro.get("Status") == "Deployed", "CloudFront 배포 완료 필요")
    origins = config.get("Origins", {}).get("Items", [])
    expected_domain = bucket + f".s3.{REGION}.amazonaws.com"
    origin = next((item for item in origins if item.get("DomainName") == expected_domain and item.get("OriginPath") == "/" + prefix.rstrip("/")), None)
    require(origin and origin.get("OriginAccessControlId"), "환경 prefix 전용 S3 OAC 원본 필요")
    require(config.get("DefaultCacheBehavior", {}).get("TargetOriginId") == origin["Id"], "기본 원본 prefix 불일치")
    if env == "production":
        # FE는 /api 상대주소로빌드됨. 정적S3원본만준비하고운영화면완료로오인하지않음.
        require(any(item.get("PathPattern") in ("/api/*", "/api*") and item.get("TargetOriginId") != origin["Id"]
                    and "POST" in item.get("AllowedMethods", {}).get("Items", [])
                    for item in config.get("CacheBehaviors", {}).get("Items", [])),
                "운영CloudFront의/api→Backend원본라우팅이먼저필요")
    policy = json.loads(api("s3api", "get-bucket-policy", "--bucket", bucket)["Policy"])
    arn = f"arn:aws:cloudfront::{ACCOUNT}:distribution/{distribution}"
    require(any(item.get("Effect") == "Allow" and item.get("Principal") == {"Service": "cloudfront.amazonaws.com"}
                and item.get("Action") == "s3:GetObject" and item.get("Resource") == f"arn:aws:s3:::{bucket}/{prefix}*"
                and item.get("Condition", {}).get("StringEquals", {}).get("AWS:SourceArn") == arn
                for item in policy.get("Statement", [])), "해당 환경 CloudFront만 읽는 버킷 정책 필요")
    uri = image(api, "seonjalal/production/frontend", release)
    command(["docker", "pull", "--platform", "linux/amd64", uri])
    container = command(["docker", "create", "--platform", "linux/amd64", uri])
    require(bool(re.fullmatch(r"[0-9a-f]{64}", container)), "추출용 컨테이너 식별자 불일치")
    site = directory / "site"
    site.mkdir(mode=0o700)
    try:
        command(["docker", "cp", container + ":/usr/share/nginx/html/.", str(site)])
    finally:
        command(["docker", "rm", container])
    files = validate_site(site)
    previous = directory / "previous-index.html"
    metadata = get_object(api, bucket, prefix + "index.html", previous)
    # assets는 기존 파일과 내용이 같거나 새 이름인 경우만 허용. 기존 탭의 chunk 삭제/덮어쓰기 방지.
    existing = set()
    for path in files:
        relative = path.relative_to(site).as_posix()
        if relative == "index.html":
            continue
        prior = directory / "compare-static"
        exists = get_object(api, bucket, prefix + relative, prior)
        if exists:
            require(hashlib.sha256(prior.read_bytes()).digest() == hashlib.sha256(path.read_bytes()).digest(),
                    "기존 정적 파일과 이름은 같지만 내용이 다름. 버전/해시 파일명 필요")
            existing.add(relative)
    return {"bucket": bucket, "prefix": prefix, "distribution": distribution, "image": uri,
            "site": site, "files": files, "previous": previous, "previous_metadata": metadata,
            "index_attempted": False, "existing": existing}


# CloudFront 전체(/*) 무효화 후 완료까지 대기. index.html 변경을 즉시 반영하기 위함.
def invalidate(api, plan):
    result = api("cloudfront", "create-invalidation", "--distribution-id", plan["distribution"], "--paths", "/*")
    api("cloudfront", "wait", "invalidation-completed", "--distribution-id", plan["distribution"],
        "--id", result["Invalidation"]["Id"])


# [변경] 업로드 순서: assets 먼저, index.html 마지막(새 index가 올라갈 때 참조 파일이 이미 존재).
# - assets: 이미 같은 파일이 있으면 건너뜀, 새 파일은 --if-none-match * 로 덮어쓰기 금지,
#   1년 immutable 캐시.
# - index.html: 계획 시점 이후 바뀌었으면(동시 배포) 중단, --if-match ETag로 조건부 쓰기, no-cache.
# - 매 파일 업로드 후 다시 내려받아 sha256 검증. 마지막에 CloudFront 무효화.
def apply_frontend(api, plan):
    ordered = [path for path in plan["files"] if path.name != "index.html"] + [plan["site"] / "index.html"]
    for path in ordered:
        relative = path.relative_to(plan["site"]).as_posix()
        if relative in plan.get("existing", set()):
            continue
        conditions = ["--if-none-match", "*"]
        # 계획 후 다른CD가index를바꿨으면바로덮어쓰지않고중단. 환경concurrency도필수.
        if relative == "index.html":
            now = get_object(api, plan["bucket"], plan["prefix"] + relative, plan["site"].parent / "current-index")
            require((now or {}).get("VersionId") == (plan["previous_metadata"] or {}).get("VersionId"), "index 동시 배포 감지")
            if now:
                require(bool(now.get("ETag")), "index 조건부 쓰기 ETag 필요")
                conditions = ["--if-match", now["ETag"]]
            plan["index_attempted"] = True
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        result = api("s3api", "put-object", "--bucket", plan["bucket"], "--key", plan["prefix"] + relative,
                     "--body", str(path), "--content-type", mimetypes.guess_type(relative)[0] or "application/octet-stream",
                     "--cache-control", "no-cache" if relative == "index.html" else "public,max-age=31536000,immutable",
                     "--metadata", "sha256=" + digest, *conditions)
        if relative == "index.html":
            plan["new_index_version"] = result.get("VersionId")
        checked = plan["site"].parent / "verified-object"
        get_object(api, plan["bucket"], plan["prefix"] + relative, checked)
        require(hashlib.sha256(checked.read_bytes()).hexdigest() == digest, "S3 업로드 후 해시 검증 실패")
    invalidate(api, plan)


# [롤백] index.html만 이전 내용으로 복원(assets는 그대로 둠, 새 index가 없어지면 참조되지 않음).
# index 업로드를 시도하기 전 실패였으면 할 일 없음. 최초 FE 배포는 이전 index가 없어 수동 복구.
# 다른 배포가 index를 바꿨으면 자동 복구 중단. 복원 후 내용 검증 + CloudFront 무효화.
def rollback_frontend(api, plan):
    if not plan["index_attempted"]:
        return
    require(plan["previous_metadata"] is not None, "최초 FE 배포에는 이전index가없음. 수동복구필요")
    now_path = plan["site"].parent / "rollback-index-check"
    current = get_object(api, plan["bucket"], plan["prefix"] + "index.html", now_path)
    require(current is not None and now_path.read_bytes() in (plan["previous"].read_bytes(), (plan["site"] / "index.html").read_bytes()),
            "다른FE배포감지. 자동복구중단")
    metadata = plan["previous_metadata"]
    require(bool(current.get("ETag")), "index 조건부 복구 ETag 필요")
    api("s3api", "put-object", "--bucket", plan["bucket"], "--key", plan["prefix"] + "index.html",
        "--body", str(plan["previous"]), "--content-type", metadata.get("ContentType", "text/html"),
        "--cache-control", metadata.get("CacheControl", "no-cache"), "--if-match", current["ETag"])
    checked = plan["site"].parent / "rollback-index-verified"
    get_object(api, plan["bucket"], plan["prefix"] + "index.html", checked)
    require(checked.read_bytes() == plan["previous"].read_bytes(), "index 복구 후 내용 확인 실패")
    invalidate(api, plan)


# 전체 오케스트레이션. 순서가 중요: 선택된 모든 대상의 검증을 끝낸 뒤에야 변경을 시작한다.
# (BE만 통과하고 FE에서 실패해 반쯤 배포되는 상황을 최소화)
def deliver(args, api, directory):
    require(api("sts", "get-caller-identity").get("Account") == ACCOUNT, "AWS 계정 불일치")
    # 모든선택대상의검사완료전에는등록/배포/업로드를하지않음.
    backends = prepare_backend(api, args.environment, args.backend_version, args.backend_layout,
                               args.compatibility_reviewed == "true") if args.component in ("backend", "all") else []
    frontend = prepare_frontend(api, args.environment, args.frontend_version, directory) if args.component in ("frontend", "all") else None
    receipt = {"environment": args.environment, "apply": args.apply, "status": "validated",
               "backend": [{"service": p["service"], "previous": p["previous"], "image": p["image"]} for p in backends],
               "frontend": {key: frontend[key] for key in ("bucket", "prefix", "distribution", "image")} if frontend else None,
               "limitations": ["DB schema rollback not performed", "private viewer/auth/payment QA remains required"]}
    path = directory / "receipt.json"
    path.write_text(json.dumps(receipt, indent=2))
    # dry-run은 여기서 종료. receipt.json은 이미 기록되어 있다(status=validated).
    if not args.apply:
        print("V2_RELEASE_VALIDATED_NO_AWS_WRITE")
        return
    attempted = []
    try:
        apply_backend(api, backends, attempted)
        if frontend:
            apply_frontend(api, frontend)
    # 배포 중 어떤 예외든 복구 시도: FE 먼저, 그다음 BE. 한쪽 복구가 실패해도 다른 쪽은 시도한다.
    # 결과를 receipt에 남기고 예외를 다시 던져 워크플로를 실패시킨다. DB는 복구하지 않는다.
    except Exception:
        receipt["status"] = "failed"
        # 앱과 정적 index만 복구. FE 복구 실패 시에도 BE 복구를 시도하되 DB는 자동 복원하지 않음.
        failures = []
        for name, restore in (("frontend", lambda: rollback_frontend(api, frontend) if frontend else None),
                              ("backend", lambda: rollback_backend(api, attempted))):
            try:
                restore()
            except Exception:
                failures.append(name)
        receipt["rollback"] = "manual intervention required: " + ",".join(failures) if failures else "previous application revisions/index restored"
        path.write_text(json.dumps(receipt, indent=2))
        raise
    receipt["status"] = "deployed"
    receipt["backend"] = [{"service": p["service"], "previous": p["previous"], "new": p["new"], "image": p["image"]} for p in attempted]
    path.write_text(json.dumps(receipt, indent=2))
    print("V2_RELEASE_DEPLOYED_AND_INFRA_HEALTH_VERIFIED")


# 진입 가드. GitHub Actions의 main 브랜치 실행, V2_CD_READY, (BE 포함 시) Task Definition 소유권을
# 확인한 뒤, 새 전용 상태 디렉터리(0700)에서만 실행한다. 로컬 PC에서 실수로 실행하는 것을 막는다.
def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--environment", choices=["staging", "production"], required=True)
    parser.add_argument("--component", choices=["backend", "frontend", "all"], required=True)
    parser.add_argument("--backend-version", default="")
    parser.add_argument("--frontend-version", default="")
    parser.add_argument("--backend-layout", choices=["legacy-v1", "split-v2"], default="split-v2")
    parser.add_argument("--compatibility-reviewed", choices=["true", "false"], default="false")
    parser.add_argument("--state-dir", required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    require(os.environ.get("GITHUB_ACTIONS") == "true" and os.environ.get("GITHUB_REF") == "refs/heads/main",
            "main의GitHub Actions수동승인Delivery에서만실행허용")
    require(os.environ.get("V2_CD_READY") == "true", "환경준비플래그필요")
    if args.component != "frontend":
        require(os.environ.get("V2_TASK_DEFINITION_OWNER") == "cd", "Task Definition 배포 소유권 이전 확인 필요")
    os.umask(0o077)
    directory = Path(args.state_dir)
    require(directory.is_absolute() and len(directory.parts) >= 4 and not directory.exists(), "새로운전용작업폴더필요")
    directory.mkdir(mode=0o700, parents=True)
    deliver(args, Aws(args.apply), directory)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print("STOP: " + (str(error) if isinstance(error, DeliveryError) else type(error).__name__), file=sys.stderr)
        raise SystemExit(1)
