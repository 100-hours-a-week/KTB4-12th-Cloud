"""AWS/DB/패키지 다운로드 없이 실행하는 로컬 안전장치 검사."""
import base64
import importlib.util
import json
import io
import os
from pathlib import Path
import stat
import sys
import tempfile
import types
import unittest
from unittest.mock import MagicMock, patch

spec = importlib.util.spec_from_file_location("runtime", Path(__file__).with_name("prepare-staging-runtime.py"))
runtime = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runtime)


class SafetyTests(unittest.TestCase):
    def exercise_verify(self, *, bad_history=False, account="721744297924"):
        payload = runtime.new_state()["payload"]
        checksum = [-277388989, -759331250, 986704585, 810214977, 2035620474,
                    1486121169, -1376361150, 1242554244, 317652465, -232005916,
                    1520669154, 530316866]
        history = [(str(i), c, 1) for i, c in enumerate(checksum, 1)]
        if bad_history:
            history[-1] = ("12", 0, 1)
        cursor = MagicMock()
        cursor.fetchone.side_effect = [("Ssl_cipher", "TLS_AES_256_GCM_SHA384"), (5022, 5022)]
        cursor.fetchall.side_effect = [
            [(f"GRANT SELECT, INSERT, UPDATE, DELETE ON `{runtime.DB_NAME}`.* TO app",)],
            history, [(3, 59), (4, 87)],
        ]
        connection = MagicMock()
        connection.__enter__.return_value = connection
        connection.cursor.return_value.__enter__.return_value = cursor
        def fake_aws(service, operation, *args, **kwargs):
            if (service, operation) == ("sts", "get-caller-identity"):
                return {"Account": account}
            if (service, operation) == ("rds", "describe-db-instances"):
                return {"DBInstances": [{"DBInstanceStatus": "available", "Endpoint": {"Address": runtime.DB_HOST, "Port": 3306}, "MasterUsername": "migrationadmin", "MasterUserSecret": {"SecretStatus": "active", "SecretArn": "test-master"}}]}
            if (service, operation) == ("secretsmanager", "get-secret-value"):
                if args[-1] == "test-master":
                    return {"SecretString": json.dumps({"username": "migrationadmin", "password": "test-master-password"})}
                return {"ARN": runtime.SECRET_ARN, "SecretString": json.dumps(payload)}
            raise AssertionError("읽기 외 API 금지")
        output = io.StringIO()
        with patch.object(runtime, "aws", side_effect=fake_aws) as api, \
             patch.object(runtime, "connect_db", return_value=connection) as db, \
             patch.object(sys, "stdout", new=output):
            if bad_history or account != runtime.ACCOUNT:
                with self.assertRaises(runtime.SafetyStop):
                    runtime.verify_runtime(Path("/unused"), 13306)
            else:
                runtime.verify_runtime(Path("/unused"), 13306)
        return api, db, cursor, output.getvalue(), payload

    def test_verify_is_readonly_and_does_not_publish_secrets(self):
        api, db, cursor, output, payload = self.exercise_verify()
        self.assertIn("READONLY_PREFLIGHT_OK_NO_DATA_OR_SECRET_CHANGE", output)
        self.assertIn("FIXTURE_GIFT_COUNT=5022", output)
        db.assert_called_once_with(Path("/unused"), 13306, "migrationadmin", "test-master-password")
        queries = [c.args[0] for c in cursor.execute.call_args_list]
        self.assertEqual(queries[-1], "ROLLBACK")
        self.assertTrue(all(q.startswith(("SHOW ", "SELECT ", "START TRANSACTION READ ONLY", "ROLLBACK")) for q in queries))
        self.assertTrue(all(c.args[1] != "put-secret-value" for c in api.call_args_list))
        for secret in payload.values():
            self.assertNotIn(secret, output)
        self.assertNotIn("test-master-password", output)

    def test_verify_rejects_wrong_history_and_rolls_back(self):
        _, _, cursor, output, _ = self.exercise_verify(bad_history=True)
        self.assertEqual(cursor.execute.call_args.args[0], "ROLLBACK")
        self.assertNotIn("READONLY_PREFLIGHT_OK", output)

    def test_verify_rejects_wrong_account_before_db_connection(self):
        _, db, _, _, _ = self.exercise_verify(account="000000000000")
        db.assert_not_called()

    def test_diagnostic_error_redacts_all_generated_secrets(self):
        state = runtime.new_state()
        text = json.dumps(runtime.publish_body(state)) + " " + " ".join(state["payload"].values())
        redacted = runtime.redact_cli_error(text, state)
        for value in state["payload"].values():
            self.assertNotIn(value, redacted)

    def test_diagnose_validates_locally_without_aws_api_or_db(self):
        with tempfile.TemporaryDirectory() as value:
            directory = Path(value)
            state = runtime.new_state()
            state["db_user_created"] = True
            runtime.secure_write(directory / "state.json", state)
            with patch.object(runtime.subprocess, "run", return_value=types.SimpleNamespace(returncode=0)) as run, \
                 patch.object(runtime, "aws") as api, patch.object(runtime, "connect_db") as db, \
                 patch.object(sys, "stdout", new=io.StringIO()) as output:
                runtime.diagnose(directory)
            run.assert_not_called()
            self.assertIn("LOCAL_REQUEST_FORMAT_VALIDATION=PASS", output.getvalue())
            api.assert_not_called()
            db.assert_not_called()
            self.assertEqual(json.loads((directory / "state.json").read_text()), state)

    def test_publish_request_has_no_response_fields(self):
        body = runtime.publish_body(runtime.new_state())
        self.assertEqual(set(body), {"SecretId", "ClientRequestToken", "SecretString"})
        self.assertNotIn("ARN", body)
        self.assertNotIn("VersionId", body)

    def test_invalid_request_token_is_blocked(self):
        state = runtime.new_state()
        state["request_token"] = "short"
        with self.assertRaises(RuntimeError):
            runtime.publish_body(state)

    def exercise_apply(self, directory, *, existing_user=False, occupied_secret=False):
        state = runtime.new_state()
        runtime.secure_write(directory / "state.json", state)
        cursor = MagicMock()
        cursor.fetchone.side_effect = [(1,), (16,)]
        cursor.fetchall.side_effect = [
            [(runtime.APP_HOST,)] if existing_user else [],
            [("GRANT USAGE ON *.* TO 'test'@'10.20.%'",)],
            [("GRANT SELECT, INSERT, UPDATE, DELETE ON `seonjalal_develop`.* TO 'test'@'10.20.%'",)],
        ]
        connection = MagicMock()
        connection.__enter__.return_value.cursor.return_value.__enter__.return_value = cursor
        calls = []

        def fake_aws(service, operation, *args, body=None):
            calls.append((service, operation, body))
            if operation == "get-caller-identity":
                return {"Account": runtime.ACCOUNT}
            if operation == "describe-db-instances":
                return {"DBInstances": [{"DBInstanceStatus": "available", "Endpoint": {"Address": runtime.DB_HOST, "Port": 3306}, "MasterUsername": "migrationadmin", "MasterUserSecret": {"SecretStatus": "active", "SecretArn": "test-master"}}]}
            if operation == "describe-secret":
                return {"ARN": runtime.SECRET_ARN, "VersionIdsToStages": {"old": ["AWSCURRENT"]} if occupied_secret else {}}
            if operation == "get-secret-value":
                return {"SecretString": json.dumps({"username": "migrationadmin", "password": "mock-master-password"} if args[-1] == "test-master" else state["payload"])}
            if operation == "put-secret-value":
                return {"VersionId": state["request_token"]}
            raise AssertionError("예상하지 않은 API 호출")

        with patch.object(runtime, "aws", side_effect=fake_aws), patch.object(runtime, "connect_db", return_value=connection), patch.object(sys, "stdout", new=io.StringIO()):
            runtime.apply(directory, 13306)
        return state, calls, cursor

    def test_success_creates_only_dml_user_publishes_four_keys_and_scrubs_journal(self):
        with tempfile.TemporaryDirectory() as value:
            directory = Path(value)
            state, calls, cursor = self.exercise_apply(directory)
            persisted = json.loads((directory / "state.json").read_text())
        self.assertTrue(persisted["completed"])
        self.assertNotIn("payload", persisted)
        published = [body for _, op, body in calls if op == "put-secret-value"]
        self.assertEqual(len(published), 1)
        self.assertEqual(json.loads(published[0]["SecretString"]), state["payload"])
        self.assertNotIn("mock-master-password", published[0]["SecretString"])
        statements = [call.args[0] for call in cursor.execute.call_args_list]
        self.assertTrue(any(sql.startswith("CREATE USER") and "REQUIRE SSL" in sql for sql in statements))
        self.assertTrue(any(sql.startswith("GRANT SELECT, INSERT, UPDATE, DELETE") for sql in statements))
        self.assertFalse(any(sql.startswith(("ALTER", "DROP", "TRUNCATE", "INSERT", "UPDATE", "DELETE")) for sql in statements))

    def test_preexisting_user_is_never_adopted(self):
        with tempfile.TemporaryDirectory() as value:
            with self.assertRaisesRegex(RuntimeError, "동일 사용자"):
                self.exercise_apply(Path(value), existing_user=True)

    def test_preexisting_runtime_value_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as value:
            with self.assertRaisesRegex(RuntimeError, "덮어쓰기 금지"):
                self.exercise_apply(Path(value), occupied_secret=True)

    def test_new_state_has_four_independent_keys(self):
        first, second = runtime.new_state(), runtime.new_state()
        self.assertEqual(len(first["payload"]), 4)
        self.assertNotEqual(first["payload"], second["payload"])
        self.assertEqual(len(base64.b64decode(first["payload"]["JWT_SECRET_BASE64"])), 64)
        self.assertEqual(len(base64.b64decode(first["payload"]["LOGIN_RATE_LIMIT_HMAC_SECRET_BASE64"])), 32)

    def test_only_expected_dml_grants_allowed(self):
        self.assertTrue(runtime.grants_are_safe([
            ("GRANT USAGE ON *.* TO 'test'@'10.20.%' REQUIRE SSL",),
            ("GRANT SELECT, INSERT, UPDATE, DELETE ON `seonjalal_develop`.* TO 'test'@'10.20.%'",),
        ]))

    def test_admin_other_schema_and_grant_option_blocked(self):
        for statement in [
            "GRANT ALL PRIVILEGES ON `seonjalal_develop`.* TO 'test'@'%'",
            "GRANT SELECT ON *.* TO 'test'@'%'",
            "GRANT SELECT ON `other`.* TO 'test'@'%'",
            "GRANT SELECT ON `seonjalal_develop`.* TO 'test'@'%' WITH GRANT OPTION",
            "GRANT CREATE ON `seonjalal_develop`.* TO 'test'@'%'",
        ]:
            self.assertFalse(runtime.grants_are_safe([(statement,)]))

    def test_journal_is_private_and_atomically_replaceable(self):
        with tempfile.TemporaryDirectory() as value:
            path = Path(value) / "state.json"
            runtime.secure_write(path, {"completed": False})
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            runtime.secure_write(path, {"completed": True})
            self.assertEqual(json.loads(path.read_text()), {"completed": True})
            self.assertEqual(list(Path(value).iterdir()), [path])

    def test_cli_secret_is_private_file_not_argument_and_file_is_removed(self):
        body = {"SecretString": "do-not-print-me"}
        observed_paths = []

        def execute(command, **kwargs):
            self.assertNotIn("do-not-print-me", " ".join(command))
            path = Path(command[command.index("--cli-input-json") + 1].removeprefix("file://"))
            observed_paths.append(path)
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(path.parent.stat().st_mode), 0o700)
            self.assertEqual(json.loads(path.read_text()), body)
            return types.SimpleNamespace(returncode=0, stdout="{}")

        with patch.object(runtime.subprocess, "run", side_effect=execute):
            runtime.aws("secretsmanager", "put-secret-value", body=body)
        self.assertFalse(observed_paths[0].exists())
        self.assertFalse(observed_paths[0].parent.exists())

    def test_private_request_file_is_removed_even_when_cli_raises(self):
        observed_paths = []

        def execute(command, **kwargs):
            observed_paths.append(Path(command[-1].removeprefix("file://")))
            raise OSError("mock CLI failure")

        with patch.object(runtime.subprocess, "run", side_effect=execute):
            with self.assertRaises(OSError):
                runtime.run_cli(["aws", "mock"], {"SecretString": "test"})
        self.assertFalse(observed_paths[0].exists())

    def test_cli_error_does_not_echo_secret(self):
        with patch.object(runtime.subprocess, "run", return_value=types.SimpleNamespace(
            returncode=1, stderr="An error (AccessDeniedException): secret-should-not-leak")):
            with self.assertRaises(RuntimeError) as error:
                runtime.aws("secretsmanager", "put-secret-value", body={"SecretString": json.dumps({"DB_PASSWORD": "secret-should-not-leak"})})
        self.assertNotIn("secret-should-not-leak", str(error.exception))
        self.assertIn("AccessDeniedException", str(error.exception))

    def test_tunnel_does_not_replace_tls_server_hostname(self):
        class FakeTLS:
            pass
        connection = MagicMock()
        connection._sock = FakeTLS()
        factory = MagicMock(return_value=connection)
        transport = MagicMock()
        with patch.dict(sys.modules, {"pymysql": types.SimpleNamespace(Connection=factory)}), \
             patch.object(runtime.ssl, "create_default_context") as context, \
             patch.object(runtime.ssl, "SSLSocket", FakeTLS), \
             patch.object(runtime.socket, "create_connection", return_value=transport) as connect:
            runtime.connect_db(Path("/private/tmp/protected"), 13306, "test", "hidden")
        self.assertEqual(factory.call_args.kwargs["host"], runtime.DB_HOST)
        self.assertEqual(factory.call_args.kwargs["ssl"], context.return_value)
        connect.assert_called_once_with(("127.0.0.1", 13306), timeout=10)
        connection.connect.assert_called_once_with(sock=transport)

    def test_unapproved_apply_stops_before_external_calls(self):
        with patch.object(sys, "argv", ["script", "apply"]), patch.object(runtime, "aws") as aws:
            with self.assertRaises(RuntimeError):
                runtime.main()
        aws.assert_not_called()


if __name__ == "__main__":
    unittest.main()
