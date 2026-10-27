#!/usr/bin/env python3
"""스테이징 전원 스크립트: 사용하지 않을 때는 전부 끄고, 쓸 때만 켠다.

Terraform 입력은 항상 "꺼짐" 기준선으로 유지하고, 켜기·끄기는 이 스크립트가 담당한다(혼합 방식).
켜진 동안에는 스테이징 Root를 apply하지 않는다. apply가 서비스·호스트를 0으로 되돌릴 수 있다.

  status                 현재 상태만 조회(변경 없음)
  on  [--execute]        RDS 시작 → 호스트(ASG, AI 호스트 포함) → BE·AI 서비스. --execute가 없으면 계획만 출력
  off [--execute]        서비스 0 → 호스트 0 → RDS 정지. --execute가 없으면 계획만 출력

대상은 이 파일의 허용 목록(계정, 클러스터, ASG, DB 이름)뿐이며 운영 리소스는 다루지 않는다.
공유 요소(ALB, NAT 인스턴스, Secrets 엔드포인트)는 운영도 쓰므로 건드리지 않는다.
"""
import argparse
import json
import subprocess
import sys
import time

ACCOUNT_ID = "721744297924"
REGION = "ap-northeast-2"
PROFILE = "seonjalal-production"
CLUSTER = "seonjalal-v2-staging-cluster"
ASG = "seonjalal-v2-staging-ecs-application"
# 스테이징 API 원본 이름의 규칙이 이 ALB(443)에 있어야 켠 뒤 외부 요청이 BE로 간다(Terraform 소유).
ALB = "seonjalal-v2-staging-alb"
ORIGIN_HOST = "v2-staging-origin.seonjalal.com"
# 존재하는 DB만 대상으로 한다(AI PostgreSQL은 만들어진 뒤부터 포함).
DATABASES = ("seonjalal-v2-staging", "seonjalal-v2-staging-ai-postgres")
SERVICE_PREFIX = "seonjalal-v2-staging-"
# AI 호스트·서비스는 만들어진 뒤부터 대상이 된다(없으면 건너뜀). AI 호스트는 0↔1 단순 전환이며 휴면 프로세스·축소 보호가 없다.
AI_ASG = "seonjalal-v2-staging-ecs-ai"
AI_SERVICE = SERVICE_PREFIX + "ai"
SLOT_SUFFIX = {"blue": "", "green": "-b"}
# 블루-그린 Main·Payment 서비스. V1 단일 BE(`backend`)는 켜지 않고 끌 때만 0으로 맞춘다.
SERVICE_BASES = ("main-backend", "payment-server")
# Terraform이 휴면 모드에서 일시 중지하는 프로세스(`staging/rehearsal/compute.tf`와 같아야 한다).
DORMANT_PROCESSES = ("AZRebalance", "AlarmNotification", "InstanceRefresh", "Launch", "ReplaceUnhealthy", "ScheduledActions")

MUTATING = {
    "start-db-instance", "stop-db-instance", "resume-processes", "suspend-processes",
    "update-auto-scaling-group", "set-instance-protection", "update-service",
}


class Aws:
    def __call__(self, *args):
        command = ["aws", *args, "--profile", PROFILE, "--region", REGION, "--output", "json"]
        result = subprocess.run(command, capture_output=True, text=True)
        if result.returncode != 0:
            first = (result.stderr.strip().splitlines() or ["(no stderr)"])[0][:240]
            raise RuntimeError(f"aws {args[0]} {args[1]} 실패: {first}")
        return json.loads(result.stdout) if result.stdout.strip() else {}


def require(condition, message):
    if not condition:
        raise SystemExit("STOP: " + message)


def wait_until(check, label, attempts=90, interval=10, sleep=time.sleep):
    for attempt in range(attempts):
        if check():
            return
        if attempt < attempts - 1:
            sleep(interval)
    raise SystemExit(f"STOP: 대기 시간 초과: {label}")


def guard(api):
    identity = api("sts", "get-caller-identity")
    require(identity["Account"] == ACCOUNT_ID, "대상 계정이 다르다")
    require(CLUSTER.startswith("seonjalal-v2-staging") and ASG.startswith("seonjalal-v2-staging"), "스테이징이 아닌 대상")
    require(all(name.startswith("seonjalal-v2-staging") for name in DATABASES), "스테이징이 아닌 DB")


def databases(api):
    found = {}
    for name in DATABASES:
        try:
            item = api("rds", "describe-db-instances", "--db-instance-identifier", name)["DBInstances"][0]
        except RuntimeError:
            continue
        found[name] = item["DBInstanceStatus"]
    return found


def services(api):
    arns = api("ecs", "list-services", "--cluster", CLUSTER)["serviceArns"]
    if not arns:
        return {}
    described = api("ecs", "describe-services", "--cluster", CLUSTER, "--services", *arns)["services"]
    return {s["serviceName"]: s for s in described}


def asg_state(api, name=ASG):
    groups = api("autoscaling", "describe-auto-scaling-groups", "--auto-scaling-group-names", name)["AutoScalingGroups"]
    if not groups:
        return None
    group = groups[0]
    return {
        "min": group["MinSize"], "desired": group["DesiredCapacity"], "max": group["MaxSize"],
        "suspended": sorted(p["ProcessName"] for p in group.get("SuspendedProcesses", [])),
        "instances": [i["InstanceId"] for i in group.get("Instances", [])],
        "in_service": [i["InstanceId"] for i in group.get("Instances", []) if i["LifecycleState"] == "InService"],
    }


def origin_rule_present(api):
    """ALB 443에 스테이징 원본 이름 규칙이 있는지. 확인할 수 없으면 None."""
    try:
        lb = api("elbv2", "describe-load-balancers", "--names", ALB)["LoadBalancers"][0]["LoadBalancerArn"]
        listener = next(l["ListenerArn"] for l in api("elbv2", "describe-listeners", "--load-balancer-arn", lb)["Listeners"] if l["Port"] == 443)
        rules = api("elbv2", "describe-rules", "--listener-arn", listener)["Rules"]
    except (RuntimeError, StopIteration, KeyError, IndexError):
        return None
    return any(ORIGIN_HOST in c.get("HostHeaderConfig", {}).get("Values", []) for r in rules for c in r.get("Conditions", []))


def status(api):
    dbs, svcs, asg, ai = databases(api), services(api), asg_state(api), asg_state(api, AI_ASG)
    state = {"databases": dbs, "asg": asg, "ai_asg": ai,
             "services": {n: (s["desiredCount"], s["runningCount"]) for n, s in sorted(svcs.items())}}
    warnings = []
    running_any = any(desired > 0 for desired, _ in state["services"].values())
    if any(v in ("available", "starting") for v in dbs.values()) and not running_any and asg["desired"] == 0 and (ai is None or ai["desired"] == 0):
        warnings.append("DB만 켜져 있다(서비스·호스트는 꺼짐). 사용하지 않는다면 off로 정지")
    if running_any and any(v != "available" for v in dbs.values()):
        warnings.append("서비스가 켜져 있는데 DB가 available이 아니다")
    if asg["desired"] == 0 and not asg["suspended"]:
        warnings.append("호스트는 0인데 일시 중지 프로세스가 해제돼 있다(Terraform 기준선과 다름). off로 재설정 가능")
    if asg["desired"] > 0 and "Launch" in asg["suspended"]:
        warnings.append("호스트 desired>0인데 Launch가 중지돼 있어 인스턴스가 만들어지지 않는다")
    if ai is not None and ai["desired"] > 0 and state["services"].get(AI_SERVICE, (0, 0))[0] == 0:
        warnings.append("AI 호스트만 켜져 있다(AI 서비스는 꺼짐 또는 없음). 사용하지 않는다면 off로 정리")
    rule = origin_rule_present(api)
    state["origin_rule"] = rule
    if rule is False:
        warnings.append(f"ALB에 {ORIGIN_HOST} 규칙이 없다. 서비스를 켜도 외부 요청이 BE로 가지 않는다(Terraform 기준선 traffic_cutover 필요)")
    elif rule is None:
        warnings.append("ALB 원본 규칙 존재를 확인하지 못했다")
    state["warnings"] = warnings
    return state


def print_status(state):
    print("== DB:", ", ".join(f"{k}={v}" for k, v in state["databases"].items()) or "(없음)")
    asg = state["asg"]
    print(f"== 호스트 ASG: min {asg['min']} / desired {asg['desired']} / max {asg['max']}, InService {len(asg['in_service'])}, 일시 중지 {len(asg['suspended'])}개")
    ai = state["ai_asg"]
    if ai is not None:
        print(f"== AI 호스트 ASG: min {ai['min']} / desired {ai['desired']} / max {ai['max']}, InService {len(ai['in_service'])}")
    print("== 서비스(desired/running):", ", ".join(f"{n.removeprefix(SERVICE_PREFIX)}={d}/{r}" for n, (d, r) in state["services"].items()))
    for warning in state["warnings"]:
        print("!!", warning)


def plan_on(args, ai=False):
    suffix = SLOT_SUFFIX[args.slot]
    steps = []
    if not args.skip_db:
        steps.append("DB 시작(정지 상태면 start, 정지 중이면 정지 완료까지 대기) 후 available 대기")
    steps.append(f"호스트 ASG: 일시 중지 프로세스 {len(DORMANT_PROCESSES)}개 재개, min/desired {args.hosts}로 올림, InService·ECS 컨테이너 인스턴스 {args.hosts}대 대기")
    if ai:
        steps.append("AI 호스트 ASG: desired 1(BE 호스트와 함께 요청해 부팅 시간을 겹침), InService·ECS 등록 대기")
    for base in SERVICE_BASES:
        steps.append(f"서비스 {SERVICE_PREFIX}{base}{suffix}: desired {args.tasks}")
    if ai:
        steps.append(f"AI 서비스 {AI_SERVICE}: desired 1(있으면). 모델 자산 준비 때문에 안정화에 시간이 걸림")
    steps.append("서비스 안정화 대기와 Main 대상 그룹 헬스 확인")
    return steps


def plan_off(args, ai=False):
    steps = ["스테이징 서비스 전부 desired 0(AI 서비스 포함, 안정화 대기)",
             "호스트 인스턴스 축소 보호 해제 → ASG min/desired 0 → 인스턴스 종료 대기 → 휴면 프로세스 다시 중지(Terraform 기준선)"]
    if ai:
        steps.append("AI 호스트 ASG desired 0 → 인스턴스 종료 대기")
    if not args.skip_db:
        steps.append("DB 정지(stopping). 정지 후 최대 7일이 지나면 AWS가 자동으로 다시 시작함")
    return steps


def run_on(api, args, sleep=time.sleep):
    suffix = SLOT_SUFFIX[args.slot]
    ai = (not args.no_ai) and asg_state(api, AI_ASG) is not None
    if not args.skip_db:
        for name in databases(api):
            current = databases(api)[name]
            if current == "stopping":
                wait_until(lambda n=name: databases(api)[n] == "stopped", f"{name} 정지 완료", sleep=sleep)
                current = "stopped"
            if current == "stopped":
                api("rds", "start-db-instance", "--db-instance-identifier", name)
                print(f"DB 시작 요청: {name}")
        for name in databases(api):
            wait_until(lambda n=name: databases(api)[n] == "available", f"{name} available", attempts=120, sleep=sleep)
        print("DB available")
    api("autoscaling", "resume-processes", "--auto-scaling-group-name", ASG, "--scaling-processes", *DORMANT_PROCESSES)
    api("autoscaling", "update-auto-scaling-group", "--auto-scaling-group-name", ASG,
        "--min-size", str(args.hosts), "--desired-capacity", str(args.hosts))
    print(f"호스트 {args.hosts}대 요청")
    if ai:
        # 부팅 시간을 겹치기 위해 BE 호스트 요청 직후 AI 호스트도 요청한다.
        api("autoscaling", "update-auto-scaling-group", "--auto-scaling-group-name", AI_ASG, "--min-size", "0", "--desired-capacity", "1")
        print("AI 호스트 1대 요청")
    expected = args.hosts + (1 if ai else 0)
    wait_until(lambda: len(asg_state(api)["in_service"]) >= args.hosts, "ASG InService", sleep=sleep)
    if ai:
        wait_until(lambda: len(asg_state(api, AI_ASG)["in_service"]) >= 1, "AI ASG InService", sleep=sleep)
    wait_until(lambda: len(api("ecs", "list-container-instances", "--cluster", CLUSTER, "--status", "ACTIVE")["containerInstanceArns"]) >= expected,
               "ECS 컨테이너 인스턴스 등록", sleep=sleep)
    print("호스트 준비 완료")
    names = [f"{SERVICE_PREFIX}{base}{suffix}" for base in SERVICE_BASES]
    for name in names:
        api("ecs", "update-service", "--cluster", CLUSTER, "--service", name, "--desired-count", str(args.tasks))
    ai_service = ai and AI_SERVICE in services(api)
    if ai_service:
        api("ecs", "update-service", "--cluster", CLUSTER, "--service", AI_SERVICE, "--desired-count", "1")
    elif ai:
        print("AI 서비스가 아직 없어 호스트만 켰다(ai-runtime 적용 후 서비스가 생김)")
    wait_until(lambda: all(s["runningCount"] >= args.tasks and len(s["deployments"]) == 1 for s in
                           (services(api)[n] for n in names)), "서비스 안정화", sleep=sleep)
    if ai_service:
        # 모델 자산 내려받기(assets-init)와 헬스 통과에 시간이 걸려 대기를 길게 잡는다.
        wait_until(lambda: services(api)[AI_SERVICE]["runningCount"] >= 1 and len(services(api)[AI_SERVICE]["deployments"]) == 1,
                   "AI 서비스 안정화", attempts=180, sleep=sleep)
    main = services(api)[names[0]]
    for balancer in main.get("loadBalancers", []):
        wait_until(lambda b=balancer: sum(1 for t in api("elbv2", "describe-target-health", "--target-group-arn", b["targetGroupArn"])["TargetHealthDescriptions"]
                                          if t["TargetHealth"]["State"] == "healthy") >= args.tasks, "대상 그룹 헬스", sleep=sleep)
    print("ON 완료: 대상 그룹 헬스 확인")


def run_off(api, args, sleep=time.sleep):
    for name, service in services(api).items():
        if service["desiredCount"] > 0:
            api("ecs", "update-service", "--cluster", CLUSTER, "--service", name, "--desired-count", "0")
    wait_until(lambda: all(s["runningCount"] == 0 for s in services(api).values()), "서비스 Task 종료", sleep=sleep)
    print("서비스 0 확인")
    state = asg_state(api)
    if state["instances"]:
        api("autoscaling", "set-instance-protection", "--auto-scaling-group-name", ASG,
            "--instance-ids", *state["instances"], "--no-protected-from-scale-in")
    if state["desired"] or state["min"] or state["instances"]:
        api("autoscaling", "update-auto-scaling-group", "--auto-scaling-group-name", ASG, "--min-size", "0", "--desired-capacity", "0")
        wait_until(lambda: not asg_state(api)["instances"], "인스턴스 종료", sleep=sleep)
    # Terraform 휴면 기준선(프로세스 중지)과 맞춘다. 이미 맞으면 호출하지 않는다.
    if set(DORMANT_PROCESSES) - set(asg_state(api)["suspended"]):
        api("autoscaling", "suspend-processes", "--auto-scaling-group-name", ASG, "--scaling-processes", *DORMANT_PROCESSES)
    print("호스트 0, 휴면 프로세스 중지 상태")
    ai_state = asg_state(api, AI_ASG)
    if ai_state is not None and (ai_state["desired"] or ai_state["min"] or ai_state["instances"]):
        api("autoscaling", "update-auto-scaling-group", "--auto-scaling-group-name", AI_ASG, "--min-size", "0", "--desired-capacity", "0")
        wait_until(lambda: not asg_state(api, AI_ASG)["instances"], "AI 인스턴스 종료", sleep=sleep)
        print("AI 호스트 0")
    if not args.skip_db:
        for name, current in databases(api).items():
            if current == "available":
                api("rds", "stop-db-instance", "--db-instance-identifier", name)
                print(f"DB 정지 요청: {name}")
    print("OFF 완료(공유 요소 ALB·NAT·Secrets 엔드포인트는 운영이 쓰므로 유지)")


def main(argv=None, api=None, sleep=time.sleep):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=["status", "on", "off"])
    parser.add_argument("--execute", action="store_true", help="없으면 계획만 출력하고 AWS를 변경하지 않는다")
    parser.add_argument("--slot", choices=sorted(SLOT_SUFFIX), default="green", help="켤 블루-그린 슬롯(기본 green=-b, 운영과 같은 방향)")
    parser.add_argument("--hosts", type=int, default=2, help="켤 호스트 수(롤링 배포 여유를 위해 2 이상)")
    parser.add_argument("--tasks", type=int, default=2, help="서비스별 Task 수(HA를 위해 2 이상)")
    parser.add_argument("--skip-db", action="store_true", help="DB 시작·정지를 건너뜀")
    parser.add_argument("--no-ai", action="store_true", help="켤 때 AI 호스트·서비스를 제외(끌 때는 항상 포함)")
    args = parser.parse_args(argv)
    require(2 <= args.hosts <= 4, "호스트 수는 2~4")
    require(2 <= args.tasks <= args.hosts * 2, "Task 수는 2 이상, 호스트 수의 2배 이하")
    api = api or Aws()
    guard(api)
    state = status(api)
    print_status(state)
    if args.command == "status":
        return state
    ai_present = state["ai_asg"] is not None
    steps = plan_on(args, ai_present and not args.no_ai) if args.command == "on" else plan_off(args, ai_present)
    print(f"-- {args.command.upper()} 계획")
    for number, step in enumerate(steps, 1):
        print(f"  {number}. {step}")
    if not args.execute:
        print("계획만 출력했다. 실제 실행은 --execute를 붙여 다시 실행")
        return state
    (run_on if args.command == "on" else run_off)(api, args, sleep=sleep)
    print_status(status(api))
    return state


if __name__ == "__main__":
    try:
        main()
    except RuntimeError as error:
        print("실패:", error, file=sys.stderr)
        sys.exit(1)
