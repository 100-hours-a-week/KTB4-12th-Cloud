"""외부 호출 없이 Secret3개 추가·기존값 보존·경합/재시도 보호 검증."""
import base64
import copy
import importlib.util
import json
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location("merge", Path(__file__).with_name("merge-develop-runtime-secret.py"))
merge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(merge)
OLD = "00000000-0000-4000-8000-000000000001"
NEW = "00000000-0000-4000-8000-000000000002"


class FakeAWS:
    def __init__(self):
        self.original = {key: "test-only-existing" for key in merge.PRESERVED}
        self.original["OTHER_EXISTING_KEY"] = "test-only-preserve"
        self.versions = {OLD: self.original}
        self.current = OLD
        self.calls = []
        self.race = False

    def __call__(self, service, operation, *args, body=None):
        self.calls.append(operation)
        if operation == "get-secret-value":
            version = args[args.index("--version-id") + 1] if "--version-id" in args else self.current
            return {"Name": merge.SECRET, "ARN": f"arn:aws:secretsmanager:ap-northeast-2:{merge.ACCOUNT}:secret:{merge.SECRET}-ABC123",
                    "VersionId": version, "SecretString": json.dumps(self.versions[version])}
        if operation == "put-secret-value":
            assert "AWSCURRENT" not in body["VersionStages"]
            self.versions[body["ClientRequestToken"]] = json.loads(body["SecretString"])
            return {}
        if operation == "update-secret-version-stage":
            if self.race:
                self.current = "other-writer"
            if args[args.index("--remove-from-version-id") + 1] != self.current:
                raise ValueError("현재 버전 경합")
            self.current = args[args.index("--move-to-version-id") + 1]
            return {}
        raise AssertionError(operation)


class MergeTest(unittest.TestCase):
    def setUp(self):
        self.api = FakeAWS()
        self.proposed = {"PAYMENT_SERVER_API_KEY": "test-only-" + "x" * 32,
                         "AI_CHAT_SERVICE_TOKEN": "test-only-ai",
                         "CHAT_CONVERSATION_TOKEN_SECRET_BASE64": base64.b64encode(b"x" * 32).decode()}
        self.journal = {"old_version": OLD, "new_version": NEW}

    def test_addition_preserves_existing_and_verifies_before_promotion(self):
        before = copy.deepcopy(self.api.original)
        merge.perform(self.proposed, self.journal, self.api)
        self.assertEqual(self.api.current, NEW)
        for key, value in before.items():
            self.assertEqual(self.api.versions[NEW][key], value)
        self.assertEqual(self.api.versions[OLD], before)
        self.assertEqual(len(self.api.versions[NEW]) - len(before), 3)
        self.assertEqual(self.api.calls, ["get-secret-value", "put-secret-value", "get-secret-value",
                                          "get-secret-value", "update-secret-version-stage", "get-secret-value"])

    def test_existing_added_key_is_not_overwritten(self):
        self.api.original["PAYMENT_SERVER_API_KEY"] = "test-only-existing-key"
        with self.assertRaises(ValueError):
            merge.perform(self.proposed, self.journal, self.api)
        self.assertNotIn("put-secret-value", self.api.calls)

    def test_stale_version_stops_before_write(self):
        self.api.current = "other-writer"
        self.api.versions[self.api.current] = self.api.original
        with self.assertRaises(ValueError):
            merge.perform(self.proposed, self.journal, self.api)
        self.assertNotIn("put-secret-value", self.api.calls)

    def test_concurrent_promotion_is_rejected(self):
        self.api.race = True
        with self.assertRaises(ValueError):
            merge.perform(self.proposed, self.journal, self.api)
        self.assertEqual(self.api.current, "other-writer")
        self.assertIn(NEW, self.api.versions)

    def test_repeated_success_only_reads(self):
        merge.perform(self.proposed, self.journal, self.api)
        self.api.calls.clear()
        merge.perform(self.proposed, self.journal, self.api)
        self.assertEqual(self.api.calls, ["get-secret-value", "get-secret-value"])

    def test_arbitrary_key_rejected(self):
        self.proposed["AWS_ACCESS_KEY_ID"] = "test-only-denied"
        with self.assertRaises(ValueError):
            merge.perform(self.proposed, self.journal, self.api)
        self.assertNotIn("put-secret-value", self.api.calls)

    def test_missing_preserved_key_stops(self):
        del self.api.original["DB_PASSWORD"]
        with self.assertRaises(ValueError):
            merge.perform(self.proposed, self.journal, self.api)
        self.assertNotIn("put-secret-value", self.api.calls)


if __name__ == "__main__":
    unittest.main()
