#!/usr/bin/env python3
"""Develop Secret 병합 실패 읽기 진단. 비밀값 조회·쓰기·재시도 없음."""
from datetime import datetime, timedelta, timezone
import argparse
import importlib.util
import json
from pathlib import Path
import re
import uuid

spec = importlib.util.spec_from_file_location("merge", Path(__file__).with_name("merge-develop-runtime-secret.py"))
merge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(merge)


def inspect(journal, api):
    identity = api("sts", "get-caller-identity")
    merge.require(identity.get("Account") == merge.ACCOUNT, "대상 계정 불일치")
    metadata = api("secretsmanager", "describe-secret", "--secret-id", merge.SECRET)
    merge.require(metadata.get("Name") == merge.SECRET, "Secret 대상 불일치")
    versions = metadata.get("VersionIdsToStages", {})
    current = [version for version, stages in versions.items() if "AWSCURRENT" in stages]
    merge.require(len(current) == 1, "현재 버전 단일성 확인 필요")
    print("CURRENT_IS_EXPECTED_OLD=" + str(current[0] == journal["old_version"]))
    print("CURRENT_IS_CANDIDATE=" + str(current[0] == journal["new_version"]))
    print("CANDIDATE_VERSION_EXISTS=" + str(journal["new_version"] in versions))
    print("NO_SECRET_VALUE_READ_OR_WRITE")

    # 감사 기록에는 반영 지연이 있으므로 미검출을 성공/실패 증거로 단정하지 않음.
    try:
        start = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        history = api("cloudtrail", "lookup-events", "--lookup-attributes",
                      "AttributeKey=EventName,AttributeValue=PutSecretValue",
                      "--start-time", start, "--max-results", "50", "--no-paginate")
    except ValueError as error:
        print("AUDIT_LOOKUP_UNAVAILABLE=" + str(error))
        return
    matched = 0
    for item in history.get("Events", []):
        try:
            event = json.loads(item.get("CloudTrailEvent", "{}"))
        except (TypeError, ValueError):
            continue
        request = event.get("requestParameters") or {}
        if request.get("clientRequestToken") != journal["new_version"]:
            continue
        if request.get("secretId") not in (merge.SECRET, metadata.get("ARN")):
            continue
        matched += 1
        code = event.get("errorCode")
        # 오류 메시지·요청 인자·SecretString은 절대 출력하지 않음.
        safe = code if isinstance(code, str) and re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", code) else "UNCLASSIFIED"
        print("PUT_AUDIT_ERROR_CODE=" + (safe if code else "NONE_RECORDED"))
    print("MATCHED_AUDIT_EVENTS=" + str(matched))
    if not matched:
        print("AUDIT_NOT_FOUND_MAY_BE_DELAYED_OR_LOCAL_CLI_FAILURE")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", required=True)
    args = parser.parse_args()
    directory = Path(args.directory)
    merge.require(directory.resolve().parent == Path("/private/tmp") and
                  directory.name.startswith("seonjalal-develop-recovery."), "보호 입력 폴더 경계 불일치")
    merge.protected(directory, directory=True)
    path = directory / "secret-merge-record.json"
    merge.protected(path)
    # 비밀을 포함한 inputs.json은 읽지 않고 기존의 비밀 없는 UUID 기록만 사용.
    journal = json.loads(path.read_text())
    merge.require(journal.get("secret") == merge.SECRET, "변경 기록 대상 불일치")
    for key in ("old_version", "new_version"):
        merge.require(str(uuid.UUID(journal[key])) == journal[key], "VersionId 형식 불일치")
    inspect(journal, merge.aws_json)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print("STOP: " + (str(error) if type(error) is ValueError else type(error).__name__))
        raise SystemExit(1)
