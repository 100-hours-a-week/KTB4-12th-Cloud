#!/usr/bin/env python3
"""V2 BE Delivery: ECR Digest 고정→ECS Service 갱신. 기본은 읽기 검증만."""
import argparse
import copy
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

ACCOUNT = "721744297924"
REGION = "ap-northeast-2"
REGISTRY = f"{ACCOUNT}.dkr.ecr.{REGION}.amazonaws.com"
MUTATIONS = {"register-task-definition", "update-service"}
TASK_KEYS = ("family", "taskRoleArn", "executionRoleArn", "networkMode", "containerDefinitions", "volumes",
             "placementConstraints", "requiresCompatibilities", "cpu", "memory", "pidMode", "ipcMode",
             "proxyConfiguration", "inferenceAccelerators", "ephemeralStorage", "runtimePlatform", "enableFaultInjection")


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
    require(output.get("networkMode") == "awsvpc" and "EC2" in output.get("requiresCompatibilities", []),
            "검토한 ECS on EC2/awsvpc Task만 지원")
    matches[0]["image"] = uri
    return output


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


def deliver(args, api, directory):
    require(api("sts", "get-caller-identity").get("Account") == ACCOUNT, "AWS 계정 불일치")
    backends = prepare_backend(api, args.environment, args.backend_version, args.backend_layout,
                               args.compatibility_reviewed == "true")
    receipt = {"environment": args.environment, "apply": args.apply, "status": "validated",
               "backend": [{"service": p["service"], "previous": p["previous"], "image": p["image"]} for p in backends],
               "limitations": ["DB schema rollback not performed", "FE delivery belongs to FE repository"]}
    path = directory / "receipt.json"
    path.write_text(json.dumps(receipt, indent=2))
    if not args.apply:
        print("V2_BACKEND_VALIDATED_NO_AWS_WRITE")
        return
    attempted = []
    try:
        apply_backend(api, backends, attempted)
    except Exception:
        receipt["status"] = "failed"
        try:
            rollback_backend(api, attempted)
            receipt["rollback"] = "previous application revisions restored"
        except Exception:
            receipt["rollback"] = "manual intervention required: backend"
        path.write_text(json.dumps(receipt, indent=2))
        raise
    receipt["status"] = "deployed"
    receipt["backend"] = [{"service": p["service"], "previous": p["previous"], "new": p["new"], "image": p["image"]} for p in attempted]
    path.write_text(json.dumps(receipt, indent=2))
    print("V2_BACKEND_DEPLOYED_AND_INFRA_HEALTH_VERIFIED")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--environment", choices=["staging", "production"], required=True)
    parser.add_argument("--backend-version", required=True)
    parser.add_argument("--backend-layout", choices=["legacy-v1", "split-v2"], default="split-v2")
    parser.add_argument("--compatibility-reviewed", choices=["true", "false"], default="false")
    parser.add_argument("--state-dir", required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    require(os.environ.get("GITHUB_ACTIONS") == "true" and os.environ.get("GITHUB_REF") == "refs/heads/main",
            "main의 GitHub Actions Delivery에서만 실행 허용")
    require(os.environ.get("V2_CD_READY") == "true", "환경 준비 플래그 필요")
    require(os.environ.get("V2_TASK_DEFINITION_OWNER") == "cd", "Task Definition 배포 소유권 이전 확인 필요")
    os.umask(0o077)
    directory = Path(args.state_dir)
    require(directory.is_absolute() and len(directory.parts) >= 4 and not directory.exists(), "새로운 전용 작업 폴더 필요")
    directory.mkdir(mode=0o700, parents=True)
    deliver(args, Aws(args.apply), directory)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print("STOP: " + (str(error) if isinstance(error, DeliveryError) else type(error).__name__), file=sys.stderr)
        raise SystemExit(1)
