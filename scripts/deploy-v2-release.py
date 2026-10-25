#!/usr/bin/env python3
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

ACCOUNT = "721744297924"
REGION = "ap-northeast-2"
REGISTRY = f"{ACCOUNT}.dkr.ecr.{REGION}.amazonaws.com"
BUCKET = f"seonjalal-v2-frontend-{ACCOUNT}-{REGION}"
MUTATIONS = {"register-task-definition", "update-service", "put-object", "create-invalidation"}
TASK_KEYS = ("family", "taskRoleArn", "executionRoleArn", "networkMode", "containerDefinitions", "volumes",
             "placementConstraints", "requiresCompatibilities", "cpu", "memory", "pidMode", "ipcMode",
             "proxyConfiguration", "inferenceAccelerators", "ephemeralStorage", "runtimePlatform", "enableFaultInjection")
# 블루-그린 슬롯별 서비스 이름 접미사. blue는 기존 이름, green은 "-b".
SLOTS = {"blue": "", "green": "-b"}


class DeliveryError(Exception):
    pass


class AwsError(DeliveryError):
    def __init__(self, operation, code):
        self.code = code
        super().__init__(operation + " CODE=" + code + ". 원문 비공개")


def require(condition, message):
    if not condition:
        raise DeliveryError(message)


def command(args, timeout=660):
    result = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    if result.returncode:
        raise DeliveryError("로컬 명령 실패: " + args[0] + ". 원문 비공개")
    return result.stdout.strip()


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


def version(value):
    require(bool(re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z][0-9A-Za-z.-]*)?", value)),
            "릴리스 버전 형식 불일치")
    return value


def image(api, repository, tag):
    metadata = api("ecr", "describe-repositories", "--repository-names", repository)["repositories"][0]
    require(metadata["imageTagMutability"] == "IMMUTABLE", "릴리스 ECR 불변 태그 설정 필요")
    details = api("ecr", "describe-images", "--repository-name", repository,
                  "--image-ids", "imageTag=" + tag)["imageDetails"]
    require(len(details) == 1 and re.fullmatch(r"sha256:[0-9a-f]{64}", details[0]["imageDigest"]), "이미지 Digest 확인 실패")
    return REGISTRY + "/" + repository + "@" + details[0]["imageDigest"]


def service(api, cluster, name):
    result = api("ecs", "describe-services", "--cluster", cluster, "--services", name)
    require(not result.get("failures") and len(result.get("services", [])) == 1, "ECS Service 미준비: " + name)
    return result["services"][0]


def active_slots(api, cluster, env):
    """Main 서비스가 ACTIVE이고 desired>0인 슬롯 이름 목록."""
    found = []
    for slot, suffix in SLOTS.items():
        result = api("ecs", "describe-services", "--cluster", cluster, "--services", f"seonjalal-v2-{env}-main-backend{suffix}")
        if any(item.get("status") == "ACTIVE" and item.get("desiredCount", 0) > 0 for item in result.get("services", [])):
            found.append(slot)
    return found


def resolve_slot(api, cluster, env, requested):
    """배포 대상 슬롯의 이름 접미사. auto는 서비스 중인 슬롯이 정확히 하나일 때만 허용."""
    if requested != "auto":
        require(requested in SLOTS, "슬롯 값 불일치")
        return SLOTS[requested]
    found = active_slots(api, cluster, env)
    require(len(found) == 1, "서비스 중인 슬롯을 하나로 특정할 수 없음(" + (",".join(found) or "없음")
            + "). 블루-그린 전환 중에는 backend_slot을 직접 지정")
    return SLOTS[found[0]]


def service_connect(value):
    """Service Connect 설정. describe-services는 이 설정을 Service 최상위가 아니라 PRIMARY 배포(deployments[])에 담아 준다."""
    for deployment in value.get("deployments", []):
        if deployment.get("status", "PRIMARY") == "PRIMARY" and deployment.get("serviceConnectConfiguration"):
            return deployment["serviceConnectConfiguration"]
    return value.get("serviceConnectConfiguration", {})


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


def render_task(task, container, uri):
    output = {key: copy.deepcopy(task[key]) for key in TASK_KEYS if key in task}
    matches = [item for item in output["containerDefinitions"] if item["name"] == container]
    require(len(matches) == 1, "이미지 교체 대상 컨테이너 불일치")
    # 기존 Task Definition을 복제해 이미지만 바꾸므로 네트워크 모드는 기존 서비스와 항상 같다.
    require(output.get("networkMode") in ("awsvpc", "bridge") and "EC2" in output.get("requiresCompatibilities", []),
            "검토한 ECS on EC2(awsvpc 또는 bridge) Task만 지원")
    matches[0]["image"] = uri
    return output


def prepare_backend(api, env, release, layout, compatibility, slot="auto"):
    require(compatibility, "DB 스키마·신구 API/Payment 호환성 검토 체크 필요")
    version(release)
    cluster = f"seonjalal-v2-{env}-cluster"
    # legacy-v1은 슬롯이 없다. split-v2는 서비스 중인 슬롯(또는 지정한 슬롯)의 서비스에 배포한다.
    slot_suffix = "" if layout == "legacy-v1" else resolve_slot(api, cluster, env, slot)
    payment_alias = "payment-server" + slot_suffix
    slot_name = None if layout == "legacy-v1" else ("green" if slot_suffix else "blue")
    names = ([("backend", "backend", "v1-" + release + "-amd64")] if layout == "legacy-v1" else
             [("payment-server", "payment-server", "payment-server-" + release),
              ("main-backend", "main-backend", "main-backend-" + release)])
    plans = []
    for suffix, container, tag in names:
        name = f"seonjalal-v2-{env}-" + suffix + slot_suffix
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
            connect = service_connect(current)
            require(connect.get("enabled"), "분리형 Main/Payment Service Connect 구성이 먼저 필요")
            if suffix == "main-backend":
                require(settings.get("PAYMENT_SERVER_BASE_URL") == f"http://{payment_alias}:8090",
                        "Main의 Payment 내부주소/Service Connect alias 계약 불일치")
            else:
                require(any(alias.get("dnsName") == payment_alias and alias.get("port") == 8090
                            for item in connect.get("services", []) for alias in item.get("clientAliases", [])),
                        "Payment Service Connect endpoint 미준비")
        plans.append({"service": name, "cluster": cluster, "previous": old, "definition": definition, "image": uri,
                      "slot": slot_name,
                      "namespace": service_connect(current).get("namespace")})
    if layout == "split-v2":
        require(all(p["namespace"] for p in plans) and len({p["namespace"] for p in plans}) == 1,
                "Main/Payment Service Connect namespace 불일치")
    return plans


def stable_at(api, plan, expected):
    # Circuit Breaker가 이전 버전으로 복귀해도 waiter는 성공할 수 있어 정확한 revision을 재검사.
    api("ecs", "wait", "services-stable", "--cluster", plan["cluster"], "--services", plan["service"])
    current = service(api, plan["cluster"], plan["service"])
    baseline(current)
    require(current["taskDefinition"] == expected, "안정화됐으나 요청 Revision 아님(rollback 또는 동시 배포)")


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


def rollback_backend(api, attempted):
    for plan in reversed(attempted):
        current = service(api, plan["cluster"], plan["service"])
        require(current["taskDefinition"] in (plan["previous"], plan["new"]), "다른 배포가 감지돼 자동 rollback 중단")
        if current["taskDefinition"] != plan["previous"]:
            api("ecs", "update-service", "--cluster", plan["cluster"], "--service", plan["service"],
                "--task-definition", plan["previous"])
        stable_at(api, plan, plan["previous"])


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


def get_object(api, bucket, key, destination):
    try:
        return api("s3api", "get-object", "--bucket", bucket, "--key", key, str(destination))
    except AwsError as error:
        if error.code in ("NoSuchKey", "404"):
            return None
        raise


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


def invalidate(api, plan):
    result = api("cloudfront", "create-invalidation", "--distribution-id", plan["distribution"], "--paths", "/*")
    api("cloudfront", "wait", "invalidation-completed", "--distribution-id", plan["distribution"],
        "--id", result["Invalidation"]["Id"])


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


def deliver(args, api, directory):
    require(api("sts", "get-caller-identity").get("Account") == ACCOUNT, "AWS 계정 불일치")
    # 모든선택대상의검사완료전에는등록/배포/업로드를하지않음.
    backends = prepare_backend(api, args.environment, args.backend_version, args.backend_layout,
                               args.compatibility_reviewed == "true", getattr(args, "backend_slot", "auto")) if args.component in ("backend", "all") else []
    frontend = prepare_frontend(api, args.environment, args.frontend_version, directory) if args.component in ("frontend", "all") else None
    receipt = {"environment": args.environment, "apply": args.apply, "status": "validated",
               "backend": [{"service": p["service"], "slot": p.get("slot"), "previous": p["previous"], "image": p["image"]} for p in backends],
               "frontend": {key: frontend[key] for key in ("bucket", "prefix", "distribution", "image")} if frontend else None,
               "limitations": ["DB schema rollback not performed", "private viewer/auth/payment QA remains required"]}
    path = directory / "receipt.json"
    path.write_text(json.dumps(receipt, indent=2))
    if not args.apply:
        print("V2_RELEASE_VALIDATED_NO_AWS_WRITE")
        return
    attempted = []
    try:
        apply_backend(api, backends, attempted)
        if frontend:
            apply_frontend(api, frontend)
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
    receipt["backend"] = [{"service": p["service"], "slot": p.get("slot"), "previous": p["previous"], "new": p["new"], "image": p["image"]} for p in attempted]
    path.write_text(json.dumps(receipt, indent=2))
    print("V2_RELEASE_DEPLOYED_AND_INFRA_HEALTH_VERIFIED")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--environment", choices=["staging", "production"], required=True)
    parser.add_argument("--component", choices=["backend", "frontend", "all"], required=True)
    parser.add_argument("--backend-version", default="")
    parser.add_argument("--frontend-version", default="")
    parser.add_argument("--backend-layout", choices=["legacy-v1", "split-v2"], default="split-v2")
    parser.add_argument("--backend-slot", choices=["auto", "blue", "green"], default="auto",
                        help="배포할 블루-그린 슬롯. auto는 서비스 중인 슬롯이 하나일 때만 선택")
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
