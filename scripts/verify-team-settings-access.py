#!/usr/bin/env python3
"""팀 IAM 권한 시뮬레이션. 실제 Secret 값 조회·설정 쓰기·서버 조작 없음."""
import json
import subprocess
import sys

ACCOUNT = "721744297924"
TEAMS = {"fullstack": ("fs-neo", "fs-theo", "fs-ava"), "ai": ("ai-dylan", "ai-emet")}
REGION = "ap-northeast-2"


def api(service, operation, *args):
    result = subprocess.run(["aws", "--profile", "seonjalal-production", "--region", REGION,
                             "--no-cli-pager", "--cli-connect-timeout", "5", "--cli-read-timeout", "30",
                             service, operation, *args, "--output", "json"],
                            capture_output=True, text=True, timeout=90)
    if result.returncode:
        raise RuntimeError("AWS 읽기/시뮬레이션 실패: " + operation + ". 원문 비공개")
    return json.loads(result.stdout)


def cases(team, user, secrets, mfa):
    other = "ai" if team == "fullstack" else "fullstack"
    cases = []
    allowed = "allowed" if mfa else "denied"
    for env in ("develop", "staging"):
        own_param = f"arn:aws:ssm:{REGION}:{ACCOUNT}:parameter/seonjalal/{env}/config/{team}/CHECK"
        other_param = f"arn:aws:ssm:{REGION}:{ACCOUNT}:parameter/seonjalal/{env}/config/{other}/CHECK"
        for action in ("secretsmanager:GetSecretValue", "secretsmanager:PutSecretValue"):
            cases += [(action, secrets[(env, team)], allowed), (action, secrets[(env, other)], "denied")]
        for action in ("ssm:GetParameter", "ssm:PutParameter"):
            cases += [(action, own_param, allowed), (action, other_param, "denied")]
    # 상위 경로 재귀 조회로 다른 팀/운영 설정을 우회하지 못하는지 포함.
    cases += [("ssm:GetParametersByPath", f"arn:aws:ssm:{REGION}:{ACCOUNT}:parameter{path}", "denied")
              for path in ("/seonjalal", "/seonjalal/develop", "/seonjalal/staging", "/seonjalal/develop/config")]
    cases += [
        ("secretsmanager:GetSecretValue", f"arn:aws:secretsmanager:{REGION}:{ACCOUNT}:secret:/seonjalal/production/runtime-ABC123", "denied"),
        ("secretsmanager:GetSecretValue", f"arn:aws:secretsmanager:{REGION}:{ACCOUNT}:secret:seonjalal/production/backend/runtime-ABC123", "denied"),
        ("secretsmanager:GetSecretValue", f"arn:aws:secretsmanager:{REGION}:{ACCOUNT}:secret:/seonjalal/develop/runtime-ABC123", "denied"),
        ("secretsmanager:DeleteSecret", secrets[("develop", team)], "denied"),
        ("ssm:PutParameter", f"arn:aws:ssm:{REGION}:{ACCOUNT}:parameter/seonjalal/production/config/{team}/CHECK", "denied"),
        ("ssm:PutParameter", f"arn:aws:ssm:{REGION}:{ACCOUNT}:parameter/seonjalal/develop/release/backend_sha", "denied"),
        ("ssm:DeleteParameter", f"arn:aws:ssm:{REGION}:{ACCOUNT}:parameter/seonjalal/develop/config/{team}/CHECK", "denied"),
        ("ec2:TerminateInstances", f"arn:aws:ec2:{REGION}:{ACCOUNT}:instance/i-00000000000000000", "denied"),
        ("ssm:SendCommand", f"arn:aws:ec2:{REGION}:{ACCOUNT}:instance/i-00000000000000000", "denied"),
        ("iam:CreateAccessKey", f"arn:aws:iam::{ACCOUNT}:user/{user}", "denied"),
        ("iam:AttachUserPolicy", f"arn:aws:iam::{ACCOUNT}:user/{user}", "denied"),
        ("iam:ChangePassword", f"arn:aws:iam::{ACCOUNT}:user/{user}", "allowed"),
        ("iam:CreateVirtualMFADevice", f"arn:aws:iam::{ACCOUNT}:mfa/{user}", "allowed"),
    ]
    return cases


def inspect_result(expected, response):
    # 최신 API는 action별 집계 결과 안에 리소스별 판정을 반환. 집계 deny를 개별결과로 오인하지 않음.
    actual = {}
    for row in response.get("EvaluationResults", []):
        if row.get("ResourceSpecificResults"):
            for resource in row["ResourceSpecificResults"]:
                actual[(row["EvalActionName"], resource["EvalResourceName"])] = {
                    "EvalDecision": resource["EvalResourceDecision"],
                    "MissingContextValues": resource.get("MissingContextValues", []),
                }
        else:
            actual[(row["EvalActionName"], row.get("EvalResourceName"))] = row
    for action, resource, decision in expected:
        row = actual.get((action, resource))
        if row is None or row.get("MissingContextValues"):
            raise RuntimeError("권한 평가 결과/컨텍스트 누락: " + action)
        state = row.get("EvalDecision")
        if state not in ("allowed", "explicitDeny", "implicitDeny"):
            raise RuntimeError("알 수 없는 권한 평가 결과")
        if (state == "allowed") != (decision == "allowed"):
            raise RuntimeError("기대 권한 불일치: " + action + " RESOURCE=" + resource)


def main():
    if api("sts", "get-caller-identity").get("Account") != ACCOUNT:
        raise RuntimeError("대상 계정 불일치")
    secrets = {}
    for env in ("develop", "staging"):
        for team in TEAMS:
            name = f"/seonjalal/{env}/teams/{team}"
            metadata = api("secretsmanager", "describe-secret", "--secret-id", name)
            if metadata.get("Name") != name:
                raise RuntimeError("Secret 메타데이터 대상 불일치")
            secrets[(env, team)] = metadata["ARN"]
    total = 0
    for team, users in TEAMS.items():
        for user in users:
            for mfa in (True, False):
                expected = cases(team, user, secrets, mfa)
                result = api("iam", "simulate-principal-policy", "--policy-source-arn",
                             f"arn:aws:iam::{ACCOUNT}:user/{user}",
                             "--action-names", *sorted({action for action, _, _ in expected}),
                             "--resource-arns", *sorted({resource for _, resource, _ in expected}),
                             "--context-entries",
                             "ContextKeyName=aws:MultiFactorAuthPresent,ContextKeyValues=" + str(mfa).lower() + ",ContextKeyType=boolean",
                             "ContextKeyName=aws:username,ContextKeyValues=" + user + ",ContextKeyType=string")
                inspect_result(expected, result)
                total += len(expected)
            print("USER_ACCESS_SIMULATION_PASSED=" + user, flush=True)
    print("CHECKED_CASES=" + str(total))
    print("TEAM_SETTINGS_IAM_SIMULATION_PASSED")
    print("NO_SECRET_VALUE_READ_NO_WRITE_NO_SERVER_ACTION")
    print("LIMITATION=IAM simulation only; real login/MFA/KMS/runtime integration not tested")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print("STOP: " + (str(error) if type(error) is RuntimeError else type(error).__name__), file=sys.stderr)
        raise SystemExit(1)
