"""실제 AWS 없이 읽기 진단 범위와 비밀 비노출 검증."""
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch
from types import SimpleNamespace

spec = importlib.util.spec_from_file_location("diagnose", Path(__file__).with_name("diagnose-develop-secret-merge.py"))
diagnose = importlib.util.module_from_spec(spec)
spec.loader.exec_module(diagnose)


class DiagnoseTests(unittest.TestCase):
    def test_private_request_file_and_cleanup(self):
        paths = []
        def run(command, **kwargs):
            self.assertNotIn("DO_NOT_PRINT", " ".join(command))
            path = Path(command[command.index("--cli-input-json") + 1].removeprefix("file://"))
            paths.append(path)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(path.parent.stat().st_mode & 0o777, 0o700)
            self.assertEqual(json.loads(path.read_text()), {"SecretString": "DO_NOT_PRINT"})
            raise TimeoutError("합성 호출 실패")
        with patch.object(diagnose.merge.subprocess, "run", side_effect=run):
            with self.assertRaises(TimeoutError):
                diagnose.merge.aws_json("secretsmanager", "put-secret-value", body={"SecretString": "DO_NOT_PRINT"})
        self.assertTrue(paths)
        self.assertFalse(paths[0].exists())
        self.assertFalse(paths[0].parent.exists())

    def test_readonly_and_sanitized_audit(self):
        journal = {"old_version": "old", "new_version": "new"}
        calls = []
        def api(service, operation, *args):
            calls.append(operation)
            if operation == "get-caller-identity":
                return {"Account": diagnose.merge.ACCOUNT}
            if operation == "describe-secret":
                return {"Name": diagnose.merge.SECRET, "VersionIdsToStages": {"old": ["AWSCURRENT"]}}
            if operation == "lookup-events":
                return {"Events": [{"CloudTrailEvent": json.dumps({
                    "requestParameters": {"secretId": diagnose.merge.SECRET,
                                          "clientRequestToken": "new", "secretString": "DO_NOT_PRINT"},
                    "errorCode": "AccessDenied", "errorMessage": "DO_NOT_PRINT"})}]}
            self.fail("허용하지 않은 API: " + operation)
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            diagnose.inspect(journal, api)
        self.assertIn("CURRENT_IS_EXPECTED_OLD=True", output.getvalue())
        self.assertIn("PUT_AUDIT_ERROR_CODE=AccessDenied", output.getvalue())
        self.assertNotIn("DO_NOT_PRINT", output.getvalue())
        self.assertEqual(calls, ["get-caller-identity", "describe-secret", "lookup-events"])

    def test_aws_error_hides_raw_stderr(self):
        for stderr, expected in (("An error occurred (AccessDeniedException) DO_NOT_PRINT", "AccessDeniedException"),
                                 ("Invalid input DO_NOT_PRINT", "CLI_OR_TRANSPORT_ERROR")):
            with patch.object(diagnose.merge.subprocess, "run", return_value=SimpleNamespace(returncode=1, stderr=stderr)):
                with self.assertRaises(ValueError) as error:
                    diagnose.merge.aws_json("secretsmanager", "put-secret-value", body={"SecretString": "DO_NOT_PRINT"})
            self.assertIn(expected, str(error.exception))
            self.assertNotIn("DO_NOT_PRINT", str(error.exception))


if __name__ == "__main__":
    unittest.main()
