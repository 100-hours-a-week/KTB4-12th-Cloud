"""실제AWS/컨테이너없이ECR발행및V2Delivery경계·실패경로검증."""
import argparse
import copy
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


cd = load("delivery", ROOT / "scripts/deploy-v2-release.py")


def healthy(definition="old"):
    return {"status": "ACTIVE", "desiredCount": 2, "runningCount": 2, "pendingCount": 0,
            "taskDefinition": definition, "deployments": [{"rolloutState": "COMPLETED"}],
            "deploymentConfiguration": {"minimumHealthyPercent": 100, "maximumPercent": 200,
                "deploymentCircuitBreaker": {"enable": True, "rollback": True}}}


def task(family, container):
    return {"family": family, "taskDefinitionArn": "old", "revision": 4, "status": "ACTIVE",
            "networkMode": "awsvpc", "requiresCompatibilities": ["EC2"],
            "containerDefinitions": [{"name": container, "image": "old-image", "environment": [
                {"name": "SPRING_FLYWAY_ENABLED", "value": "false"},
                {"name": "PAYMENT_SERVER_BASE_URL", "value": "http://payment-server:8090"}]}]}


class DeliveryTests(unittest.TestCase):
    def test_definition_changes_only_target_image(self):
        original = task("family", "backend")
        before = copy.deepcopy(original)
        output = cd.render_task(original, "backend", "pinned@sha256:" + "a" * 64)
        self.assertEqual(original, before)
        self.assertNotIn("taskDefinitionArn", output)
        self.assertNotIn("revision", output)
        self.assertEqual(output["containerDefinitions"][0]["environment"], original["containerDefinitions"][0]["environment"])
        self.assertTrue(output["containerDefinitions"][0]["image"].startswith("pinned@"))

    def test_baseline_requires_safe_service(self):
        cd.baseline(healthy())
        for value in ({**healthy(), "desiredCount": 0}, {**healthy(), "pendingCount": 1},
                      {**healthy(), "deployments": [{"rolloutState": "IN_PROGRESS"}]},
                      {**healthy(), "deploymentConfiguration": {}}):
            with self.assertRaises(cd.DeliveryError):
                cd.baseline(value)

    def test_automatic_rollback_not_misreported_as_success(self):
        def api(service, operation, *args):
            return {"services": [healthy("old")]} if operation == "describe-services" else {}
        with self.assertRaises(cd.DeliveryError):
            cd.stable_at(api, {"cluster": "cluster", "service": "service"}, "new")

    def test_dry_run_blocks_mutating_aws_call_before_subprocess(self):
        with patch.object(cd.subprocess, "run") as run:
            for operation in cd.MUTATIONS:
                with self.assertRaises(cd.DeliveryError):
                    cd.Aws(False)("ecs", operation)
            run.assert_not_called()

    def test_failure_rolls_back_both_services_without_schema_restore(self):
        args = argparse.Namespace(environment="staging", backend_version="2.0.0", frontend_version="", component="backend",
                                  backend_layout="split-v2", compatibility_reviewed="true", apply=True)
        plans = [{"service": name, "cluster": "cluster", "previous": "old-" + name,
                  "definition": {"family": name}, "image": "pinned"} for name in ("payment", "main")]
        state = {p["service"]: p["previous"] for p in plans}
        def api(service, operation, *items, body=None):
            if operation == "get-caller-identity":
                return {"Account": cd.ACCOUNT}
            if operation == "describe-services":
                name = items[items.index("--services") + 1]
                return {"services": [healthy(state[name])]}
            if operation == "register-task-definition":
                return {"taskDefinition": {"taskDefinitionArn": "new-" + body["family"]}}
            if operation == "update-service":
                name, definition = items[items.index("--service") + 1], items[items.index("--task-definition") + 1]
                state[name] = definition
                if name == "main" and definition == "new-main":
                    raise cd.DeliveryError("의도한 실패")
                return {}
            if operation == "wait":
                return {}
            self.fail("예상하지 않은 API: " + operation)
        with tempfile.TemporaryDirectory() as temporary, patch.object(cd, "prepare_backend", return_value=plans):
            with self.assertRaises(cd.DeliveryError):
                cd.deliver(args, api, Path(temporary))
            receipt = json.loads((Path(temporary) / "receipt.json").read_text())
        self.assertEqual(state, {"payment": "old-payment", "main": "old-main"})
        self.assertEqual(receipt["status"], "failed")
        self.assertIn("restored", receipt["rollback"])


if __name__ == "__main__":
    unittest.main()
