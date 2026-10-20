#!/usr/bin/env python3
"""승인된 시험 ECS 노드2대만 축소 삭제. 다른 EC2/RDS/네트워크 삭제 없음."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import time

spec = importlib.util.spec_from_file_location("closeout", Path(__file__).with_name("collect-rehearsal-closeout.py"))
helper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helper)
aws, require = helper.aws, helper.require
CLUSTER = "seonjalal-v2-staging-cluster"
SERVICE = "seonjalal-v2-staging-backend"
ASG = "seonjalal-v2-staging-ecs-application"
IDS = ["i-09a8f9d32136ed6dc", "i-0a715048312bc854d"]
PRODUCTION = "i-0417358a95b551c12"


def group():
    rows = aws("autoscaling", "describe-auto-scaling-groups", "--auto-scaling-group-names", ASG)["AutoScalingGroups"]
    require(len(rows) == 1 and rows[0]["AutoScalingGroupName"] == ASG, "EXACT_ASG_NOT_FOUND")
    return rows[0]


def validate_group(row):
    require(set(x["InstanceId"] for x in row["Instances"]) == set(IDS), "ASG_INSTANCE_LIST_CHANGED")
    require(row["MinSize"] == 2 and row["DesiredCapacity"] == 2, "ASG_CAPACITY_CHANGED")
    require(not any(x["ProcessName"] == "Terminate" for x in row.get("SuspendedProcesses", [])),
            "ASG_TERMINATION_ALREADY_SUSPENDED")


def service():
    result = aws("ecs", "describe-services", "--cluster", CLUSTER, "--services", SERVICE)
    require(not result.get("failures") and len(result["services"]) == 1, "EXACT_SERVICE_NOT_FOUND")
    return result["services"][0]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence-directory", required=True)
    parser.add_argument("--approved-stage-node-removal", action="store_true")
    args = parser.parse_args()
    require(args.approved_stage_node_removal, "EXPLICIT_NODE_REMOVAL_APPROVAL_REQUIRED")
    require(aws("sts", "get-caller-identity")["Account"] == "721744297924", "WRONG_ACCOUNT")
    directory = Path(args.evidence_directory)
    require(not directory.is_symlink(), "SYMLINK_DIRECTORY_NOT_ALLOWED")
    directory = directory.resolve()
    require(directory.parent == helper.ROOT / "docs/rehearsals" and directory.name.startswith("closeout-20261019-"),
            "WRONG_EVIDENCE_DIRECTORY")
    require((directory / "artifact-hashes.json").is_file() and (directory / "dms-before-cleanup.json").is_file(),
            "CLOSEOUT_EVIDENCE_REQUIRED")
    record_path = directory / "node-removal.json"
    require(not record_path.exists() and not record_path.is_symlink(), "PREVIOUS_NODE_REMOVAL_RECORD_EXISTS_DO_NOT_REPEAT")
    before = group()
    validate_group(before)
    current = service()
    require(current["serviceName"] == SERVICE and current["schedulingStrategy"] == "REPLICA", "SERVICE_CHANGED")
    services = aws("ecs", "list-services", "--cluster", CLUSTER)["serviceArns"]
    require(services == [current["serviceArn"]], "EXTRA_CLUSTER_SERVICES_REQUIRE_REVIEW")
    provider = aws("ecs", "describe-capacity-providers", "--capacity-providers",
                   "seonjalal-v2-staging-application")["capacityProviders"]
    require(len(provider) == 1 and provider[0]["autoScalingGroupProvider"]["autoScalingGroupArn"] == before["AutoScalingGroupARN"]
            and provider[0]["autoScalingGroupProvider"]["managedScaling"]["status"] == "DISABLED"
            and provider[0]["autoScalingGroupProvider"]["managedTerminationProtection"] == "DISABLED",
            "CAPACITY_PROVIDER_CHANGED")
    production = aws("ec2", "describe-instances", "--instance-ids", PRODUCTION)["Reservations"]
    require(len(production) == 1 and production[0]["Instances"][0]["State"]["Name"] == "running", "V1_PRODUCTION_NOT_RUNNING")
    os.umask(0o077)
    state = {"phase": "removal_intent", "asg": ASG, "instance_ids": IDS,
             "before_min": before["MinSize"], "before_desired": before["DesiredCapacity"],
             "before_max": before["MaxSize"], "production_instance_excluded": PRODUCTION}
    descriptor = os.open(record_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w") as stream:
        json.dump(state, stream, indent=2)
    try:
        # 재생성·예약 확장을 먼저 막고, Terminate는 허용해 승인된 노드 축소만 수행.
        aws("autoscaling", "suspend-processes", "--auto-scaling-group-name", ASG,
            "--scaling-processes", "Launch", "AlarmNotification", "ScheduledActions", "AZRebalance", "ReplaceUnhealthy", "InstanceRefresh")
        print("STAGING_ASG_RELAUNCH_SUSPENDED", flush=True)
        aws("ecs", "update-service", "--cluster", CLUSTER, "--service", SERVICE, "--desired-count", "0")
        print("STAGING_ECS_SERVICE_DESIRED=0", flush=True)
        deadline = time.monotonic() + 180
        while True:
            row = service()
            tasks = aws("ecs", "list-tasks", "--cluster", CLUSTER, "--desired-status", "RUNNING")["taskArns"]
            if row["desiredCount"] == row["runningCount"] == row["pendingCount"] == 0 and not tasks:
                break
            require(time.monotonic() < deadline, "ECS_DRAIN_TIMEOUT_NODES_NOT_REMOVED")
            time.sleep(5)
        print("STAGING_ECS_RUNNING_AND_PENDING_TASKS=0", flush=True)
        require(set(x["InstanceId"] for x in group()["Instances"]) == set(IDS), "NODES_CHANGED_BEFORE_REMOVAL")
        # desired0을 먼저 설정. 보호 해제 시 다른 인스턴스로 교체되지 않도록 순서 고정.
        aws("autoscaling", "update-auto-scaling-group", "--auto-scaling-group-name", ASG,
            "--min-size", "0", "--desired-capacity", "0")
        aws("autoscaling", "set-instance-protection", "--auto-scaling-group-name", ASG,
            "--instance-ids", *IDS, "--no-protected-from-scale-in")
        print("APPROVED_TWO_NODES_SCALE_IN_REQUESTED", flush=True)
        deadline = time.monotonic() + 240
        while True:
            statuses = aws("ec2", "describe-instances", "--instance-ids", *IDS)["Reservations"]
            values = {row["InstanceId"]: row["State"]["Name"] for group_row in statuses for row in group_row["Instances"]}
            if all(values.get(node) == "terminated" for node in IDS) and not group()["Instances"]:
                break
            require(time.monotonic() < deadline, "NODE_TERMINATION_PENDING_CHECK_STATUS_DO_NOT_REPEAT")
            time.sleep(5)
        production = aws("ec2", "describe-instances", "--instance-ids", PRODUCTION)["Reservations"]
        require(production[0]["Instances"][0]["State"]["Name"] == "running", "V1_PRODUCTION_STATE_RECHECK_REQUIRED")
        state.update(phase="two_test_nodes_terminated", asg_min=0, asg_desired=0,
                     ecs_running_tasks=0, production_state="running")
        print("TEST_ECS_NODES_TERMINATED=2 V1_PRODUCTION_RUNNING", flush=True)
        print("NO_OTHER_EC2_RDS_DMS_OR_NETWORK_DELETE")
    except Exception as error:
        state.update(phase="partial_state_preserved_do_not_repeat", error=type(error).__name__)
        raise
    finally:
        temporary = record_path.with_suffix(".next.json")
        with temporary.open("x") as stream:
            json.dump(state, stream, indent=2)
        temporary.replace(record_path)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        detail = str(error) if type(error) is RuntimeError else type(error).__name__
        raise SystemExit("STOP_NODE_CLEANUP=" + detail + "; do not repeat; inspect partial state")
