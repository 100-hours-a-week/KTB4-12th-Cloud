"""Terraform/AWS 실행 없이 실제 정책 JSON의 액션·리소스·리전 경계 정적검사."""
import json
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1] / "infrastructure/terraform/environments/staging/cutover-control"


class ControlPolicyTest(unittest.TestCase):
    def policy(self):
        return json.loads((ROOT / "dms-policy.json").read_text())

    def test_only_three_explicit_actions(self):
        actions = {action for row in self.policy()["Statement"] for action in row["Action"]}
        self.assertEqual(actions, {"dms:DescribeReplicationTasks", "dms:DescribeTableStatistics", "dms:StopReplicationTask"})

    def test_stop_and_table_read_scoped_to_exact_new_task(self):
        rows = [row for row in self.policy()["Statement"] if "dms:StopReplicationTask" in row["Action"]]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["Resource"], "arn:aws:dms:ap-northeast-2:721744297924:task:OU5M6NHIS5BOHGL7KSXRWHCKNY")
        self.assertIn("dms:DescribeTableStatistics", rows[0]["Action"])

    def test_only_status_listing_uses_resource_star(self):
        rows = [row for row in self.policy()["Statement"] if row["Resource"] == "*"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["Action"], ["dms:DescribeReplicationTasks"])

    def test_all_statements_region_limited(self):
        for row in self.policy()["Statement"]:
            self.assertEqual(row["Condition"], {"StringEquals": {"aws:RequestedRegion": "ap-northeast-2"}})

    def test_only_one_iam_policy_resource_no_provisioners(self):
        text = (ROOT / "main.tf").read_text()
        self.assertEqual(re.findall(r'resource\s+"([^"]+)"\s+"([^"]+)"', text),
                         [("aws_iam_role_policy", "cutover_control")])
        self.assertNotIn("provisioner", text)
        self.assertIn("data.aws_iam_instance_profile.source.role_arn == local.source_role_arn", text)


if __name__ == "__main__":
    unittest.main()
