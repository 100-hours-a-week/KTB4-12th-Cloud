"""AWS 없이 Redis·SMTP 비밀 추가 도구의 보존·거부·재시도·값 미출력 검사."""
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest

spec = importlib.util.spec_from_file_location(
    "tool", Path(__file__).with_name("merge-develop-runtime-secret-redis-smtp.py"))
tool = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tool)

RUNTIME_ARN = "arn:aws:secretsmanager:ap-northeast-2:721744297924:secret:/seonjalal/develop/runtime-AbCdEf"
SES_ARN = "arn:aws:secretsmanager:ap-northeast-2:721744297924:secret:seonjalal/shared/ses-smtp-local-XyZ123"
OLD_VERSION = "11111111-1111-4111-8111-111111111111"
EXISTING = {key: "existing-" + key for key in tool.PRESERVED}
SES = {"SMTP_HOST": "email-smtp.ap-northeast-2.amazonaws.com", "SMTP_USERNAME": "AKIATESTONLY",
       "SMTP_PASSWORD": "Ab+c/dE==", "SMTP_PORT": "587"}


class FakeAws:
    """Secrets Manager 최소 모의: 버전·Stage 이동·ClientRequestToken 멱등성."""

    def __init__(self, runtime=None, ses=None, account="721744297924", fail_promotion_once=False, unrelated_write=False):
        self.account = account
        self.fail_promotion_once = fail_promotion_once
        self.unrelated_write = unrelated_write
        self.calls = []
        self.secrets = {
            tool.SECRET: {"arn": RUNTIME_ARN, "versions": {OLD_VERSION: json.dumps(runtime or EXISTING)},
                          "stages": {"AWSCURRENT": OLD_VERSION}},
            tool.SES_SECRET: {"arn": SES_ARN, "versions": {"ses-v1": json.dumps(ses or SES)},
                              "stages": {"AWSCURRENT": "ses-v1"}},
        }

    @staticmethod
    def options(args):
        return dict(zip(args[::2], args[1::2]))

    def __call__(self, service, operation, *args, body=None):
        self.calls.append(operation)
        if service == "sts":
            return {"Account": self.account}
        if operation == "get-secret-value":
            options = self.options(args)
            secret = self.secrets[options["--secret-id"]]
            version = options.get("--version-id") or secret["stages"][options.get("--version-stage", "AWSCURRENT")]
            return {"ARN": secret["arn"], "Name": options["--secret-id"], "VersionId": version,
                    "SecretString": secret["versions"][version]}
        if operation == "put-secret-value":
            secret = self.secrets[body["SecretId"]]
            version = body["ClientRequestToken"]
            if version in secret["versions"] and secret["versions"][version] != body["SecretString"]:
                raise ValueError("AWS 호출 실패: put-secret-value CODE=ResourceExistsException")
            secret["versions"][version] = body["SecretString"]
            return {"VersionId": version}
        if operation == "update-secret-version-stage":
            options = self.options(args)
            secret = self.secrets[options["--secret-id"]]
            if self.fail_promotion_once:
                self.fail_promotion_once = False
                raise ValueError("AWS 호출 실패: update-secret-version-stage CODE=ThrottlingException")
            if secret["stages"].get(options["--version-stage"]) != options["--remove-from-version-id"]:
                raise ValueError("AWS 호출 실패: update-secret-version-stage CODE=InvalidRequestException")
            secret["stages"][options["--version-stage"]] = options["--move-to-version-id"]
            return {}
        raise AssertionError("예상하지 못한 호출: " + operation)

    def current_runtime(self):
        secret = self.secrets[tool.SECRET]
        return json.loads(secret["versions"][secret["stages"]["AWSCURRENT"]])


class RedisSmtpMergeTests(unittest.TestCase):
    def run_tool(self, aws, state, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                code = tool.main(list(argv), api=aws, state=state)
            except ValueError as error:
                err.write("STOP: " + str(error))
                code = 1
        return code, out.getvalue(), err.getvalue()

    def state_dir(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        return Path(temporary.name) / "state"

    def test_plan_changes_nothing_and_prints_no_values(self):
        aws = FakeAws()
        code, out, err = self.run_tool(aws, self.state_dir())
        self.assertEqual(code, 0, err)
        self.assertIn("PLAN_ONLY_NO_AWS_CHANGE", out)
        self.assertIn("CURRENT_VERSION=" + OLD_VERSION, out)
        self.assertNotIn("put-secret-value", aws.calls)
        self.assertNotIn("update-secret-version-stage", aws.calls)
        for value in ("AKIATESTONLY", "Ab+c/dE=="):
            self.assertNotIn(value, out + err)

    def test_execute_adds_three_keys_preserves_existing_and_never_prints_values(self):
        aws, state = FakeAws(), self.state_dir()
        code, out, err = self.run_tool(aws, state, "--execute", "--expected-version", OLD_VERSION)
        self.assertEqual(code, 0, err)
        merged = aws.current_runtime()
        for key, value in EXISTING.items():
            self.assertEqual(merged[key], value)
        self.assertEqual(merged["SMTP_USERNAME"], "AKIATESTONLY")
        self.assertEqual(merged["SMTP_PASSWORD"], "Ab+c/dE==")
        self.assertRegex(merged["REDIS_PASSWORD"], r"^[0-9a-f]{64}$")
        for value in (merged["REDIS_PASSWORD"], "AKIATESTONLY", "Ab+c/dE=="):
            self.assertNotIn(value, out + err)
        # 비밀이 담긴 입력 파일은 적용 검증 뒤 삭제되고, 기록 파일에는 비밀이 없다.
        self.assertFalse((state / "proposed.json").exists())
        self.assertNotIn(merged["REDIS_PASSWORD"], (state / "journal.json").read_text())

    def test_protected_state_permissions(self):
        aws, state = FakeAws(fail_promotion_once=True), self.state_dir()
        self.run_tool(aws, state, "--execute", "--expected-version", OLD_VERSION)
        self.assertEqual(stat.S_IMODE(state.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE((state / "proposed.json").stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE((state / "journal.json").stat().st_mode), 0o600)

    def test_refuses_to_overwrite_existing_keys(self):
        for key in tool.ADDED:
            with self.subTest(key=key):
                aws = FakeAws(runtime={**EXISTING, key: "already"})
                code, _, err = self.run_tool(aws, self.state_dir(), "--execute", "--expected-version", OLD_VERSION)
                self.assertEqual(code, 1)
                self.assertIn("덮어쓰기 중단", err)
                self.assertNotIn("put-secret-value", aws.calls)

    def test_refuses_when_runtime_version_differs_from_the_approved_one(self):
        aws = FakeAws()
        other = "22222222-2222-4222-8222-222222222222"
        code, _, err = self.run_tool(aws, self.state_dir(), "--execute", "--expected-version", other)
        self.assertEqual(code, 1)
        self.assertIn("버전 변경 감지", err)
        self.assertNotIn("put-secret-value", aws.calls)

    def test_execute_requires_expected_version(self):
        code, _, err = self.run_tool(FakeAws(), self.state_dir(), "--execute")
        self.assertEqual(code, 1)
        self.assertIn("--expected-version", err)

    def test_unsafe_smtp_password_rejected_before_any_write_without_value(self):
        for password in ("has space", "bad!char", "line\nbreak"):
            with self.subTest(password=password):
                aws = FakeAws(ses={**SES, "SMTP_PASSWORD": password})
                code, out, err = self.run_tool(aws, self.state_dir(), "--execute", "--expected-version", OLD_VERSION)
                self.assertEqual(code, 1)
                self.assertIn("안전하지 않은 형식", err)
                self.assertNotIn("put-secret-value", aws.calls)
                self.assertNotIn("has space", out + err)

    def test_wrong_account_or_ses_host_is_rejected(self):
        code, _, err = self.run_tool(FakeAws(account="000000000000"), self.state_dir())
        self.assertEqual(code, 1)
        self.assertIn("대상 계정 불일치", err)
        code, _, err = self.run_tool(FakeAws(ses={**SES, "SMTP_HOST": "smtp.example.invalid"}), self.state_dir())
        self.assertEqual(code, 1)
        self.assertIn("SES SMTP 호스트 불일치", err)

    def test_missing_preserved_value_rejected(self):
        broken = dict(EXISTING)
        del broken["DB_PASSWORD"]
        code, _, err = self.run_tool(FakeAws(runtime=broken), self.state_dir())
        self.assertEqual(code, 1)
        self.assertIn("기존 필수값 확인 필요", err)

    def test_interrupted_promotion_resumes_with_the_same_generated_password(self):
        aws, state = FakeAws(fail_promotion_once=True), self.state_dir()
        code, _, err = self.run_tool(aws, state, "--execute", "--expected-version", OLD_VERSION)
        self.assertEqual(code, 1)
        self.assertIn("ThrottlingException", err)
        # 승격 전이라 현재 버전은 그대로이고 새 비밀번호는 후보 버전에만 있다.
        self.assertNotIn("REDIS_PASSWORD", aws.current_runtime())
        candidate = [v for k, v in aws.secrets[tool.SECRET]["versions"].items() if k != OLD_VERSION][0]
        first_password = json.loads(candidate)["REDIS_PASSWORD"]
        code, _, err = self.run_tool(aws, state, "--execute", "--expected-version", OLD_VERSION)
        self.assertEqual(code, 0, err)
        self.assertEqual(aws.current_runtime()["REDIS_PASSWORD"], first_password)
        self.assertEqual(len(aws.secrets[tool.SECRET]["versions"]), 2)

    def test_rerun_after_success_reports_already_applied_without_new_password(self):
        aws, state = FakeAws(), self.state_dir()
        self.run_tool(aws, state, "--execute", "--expected-version", OLD_VERSION)
        password = aws.current_runtime()["REDIS_PASSWORD"]
        code, out, err = self.run_tool(aws, state, "--execute", "--expected-version", OLD_VERSION)
        self.assertEqual(code, 0, err)
        self.assertIn("ALREADY_APPLIED_NO_CHANGE", out)
        self.assertEqual(aws.current_runtime()["REDIS_PASSWORD"], password)
        self.assertEqual(len(aws.secrets[tool.SECRET]["versions"]), 2)

    def test_concurrent_change_during_candidate_verification_blocks_promotion(self):
        aws, state = FakeAws(), self.state_dir()
        original = aws.__call__

        def changing(service, operation, *args, body=None):
            result = original(service, operation, *args, body=body)
            if operation == "put-secret-value":
                # 후보 생성 직후 다른 사람이 현재 버전을 바꾼 상황을 만든다.
                secret = aws.secrets[tool.SECRET]
                secret["versions"]["other"] = json.dumps({**EXISTING, "JWT_SECRET_BASE64": "changed-by-someone"})
                secret["stages"]["AWSCURRENT"] = "other"
            return result

        code, _, err = self.run_tool(changing, state, "--execute", "--expected-version", OLD_VERSION)
        self.assertEqual(code, 1)
        self.assertIn("기존 버전 변경 감지", err)
        self.assertEqual(aws.secrets[tool.SECRET]["stages"]["AWSCURRENT"], "other")


if __name__ == "__main__":
    unittest.main()
