"""실제 AWS 호출 없이 권한 검사 판정·경계 검증."""
import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location("verify", Path(__file__).with_name("verify-team-settings-access.py"))
verify = importlib.util.module_from_spec(spec)
spec.loader.exec_module(verify)


class AccessTests(unittest.TestCase):
    def test_mfa_gates_configuration_not_bootstrap(self):
        secrets = {(env, team): env + "/" + team for env in ("develop", "staging") for team in verify.TEAMS}
        for mfa in (True, False):
            cases = verify.cases("ai", "ai-dylan", secrets, mfa)
            self.assertIn(("secretsmanager:PutSecretValue", "develop/ai", "allowed" if mfa else "denied"), cases)
            self.assertIn(("secretsmanager:GetSecretValue", "develop/fullstack", "denied"), cases)
            self.assertIn(("iam:ChangePassword", f"arn:aws:iam::{verify.ACCOUNT}:user/ai-dylan", "allowed"), cases)

    def test_missing_or_unexpected_allow_stops(self):
        expected = [("ssm:PutParameter", "resource", "denied")]
        for response in ({}, {"EvaluationResults": [{"EvalActionName": "ssm:PutParameter",
                    "EvalResourceName": "resource", "EvalDecision": "allowed"}]}):
            with self.assertRaises(RuntimeError):
                verify.inspect_result(expected, response)

    def test_valid_deny_and_allow(self):
        for wanted, actual in (("denied", "explicitDeny"), ("denied", "implicitDeny"), ("allowed", "allowed")):
            verify.inspect_result([("action", "resource", wanted)], {"EvaluationResults": [{
                "EvalActionName": "action", "EvalResourceName": "resource", "EvalDecision": actual}]})

    def test_per_resource_results_override_aggregate_deny(self):
        verify.inspect_result([("action", "own", "allowed"), ("action", "other", "denied")], {
            "EvaluationResults": [{"EvalActionName": "action", "EvalResourceName": "*", "EvalDecision": "explicitDeny",
                "ResourceSpecificResults": [
                    {"EvalResourceName": "own", "EvalResourceDecision": "allowed"},
                    {"EvalResourceName": "other", "EvalResourceDecision": "explicitDeny"}]}]})


if __name__ == "__main__":
    unittest.main()
