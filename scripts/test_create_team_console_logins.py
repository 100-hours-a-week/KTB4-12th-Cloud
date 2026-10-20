"""실제 IAM 접근 없이 로그인 생성 범위·기존값 보존·출력 보안 검사."""
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace

spec = importlib.util.spec_from_file_location("login", Path(__file__).with_name("create-team-console-logins.py"))
login = importlib.util.module_from_spec(spec)
spec.loader.exec_module(login)


class Fake:
    def __init__(self):
        self.profiles = {}
        self.created = []
        self.fail = False

    def __call__(self, service, operation, *args, body=None):
        if operation == "get-caller-identity":
            return {"Account": login.ACCOUNT}
        if operation == "get-account-password-policy":
            return {"PasswordPolicy": {"MinimumPasswordLength": 40}}
        if operation == "get-user":
            return {"User": {"Arn": f"arn:aws:iam::{login.ACCOUNT}:user/{args[1]}"}}
        if operation == "get-login-profile":
            if args[1] not in self.profiles:
                raise login.AwsFailure(operation, "NoSuchEntity")
            return {"LoginProfile": self.profiles[args[1]]}
        if operation == "create-login-profile":
            if self.fail:
                raise login.AwsFailure(operation, "ExpiredToken")
            self.created.append(body.copy())
            self.profiles[body["UserName"]] = {"UserName": body["UserName"], "PasswordResetRequired": True}
            return {}
        raise AssertionError(operation)


class Tests(unittest.TestCase):
    def test_password_not_in_argv_and_request_file_cleaned(self):
        paths = []
        def run(command, **kwargs):
            self.assertNotIn("PASSWORD_CANARY", " ".join(command))
            path = Path(command[-1].removeprefix("file://"))
            paths.append(path)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(path.parent.stat().st_mode & 0o777, 0o700)
            self.assertEqual(json.loads(path.read_text())["Password"], "PASSWORD_CANARY")
            return SimpleNamespace(returncode=1, stderr="An error occurred (AccessDenied) PASSWORD_CANARY")
        with patch.object(login.subprocess, "run", side_effect=run):
            with self.assertRaises(login.AwsFailure) as error:
                login.api("iam", "create-login-profile", body={"Password": "PASSWORD_CANARY"})
        self.assertNotIn("PASSWORD_CANARY", str(error.exception))
        self.assertFalse(paths[0].exists())
        self.assertFalse(paths[0].parent.exists())

    def test_new_four_only_and_private_handoff(self):
        fake, output = Fake(), io.StringIO()
        with tempfile.TemporaryDirectory() as temporary, contextlib.redirect_stdout(output):
            path = login.issue(fake, Path(temporary))
            record = json.loads(path.read_text())
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(set(record["users"]), set(login.USERS))
            for user in record["users"].values():
                self.assertEqual(user["status"], "issued")
                self.assertEqual(len(user["temporary_password"]), 40)
                self.assertNotIn(user["temporary_password"], output.getvalue())
        self.assertEqual(len(fake.created), 4)
        self.assertNotIn("fs-theo", [item["UserName"] for item in fake.created])
        self.assertTrue(all(item["PasswordResetRequired"] for item in fake.created))

    def test_existing_profile_stops_before_any_create(self):
        fake = Fake()
        fake.profiles["ai-emet"] = {"UserName": "ai-emet"}
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaises(ValueError):
                login.issue(fake, Path(temporary))
            self.assertFalse((Path(temporary) / "credentials.json").exists())
        self.assertEqual(fake.created, [])

    def test_failed_create_keeps_private_pending_record(self):
        fake = Fake()
        fake.fail = True
        with tempfile.TemporaryDirectory() as temporary, contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(login.AwsFailure):
                login.issue(fake, Path(temporary))
            record = json.loads((Path(temporary) / "credentials.json").read_text())
            self.assertEqual(record["users"]["fs-neo"]["status"], "pending")
            self.assertEqual(record["users"]["fs-ava"]["status"], "prepared")


if __name__ == "__main__":
    unittest.main()
