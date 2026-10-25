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


class EcrPublisherTests(unittest.TestCase):
    def test_two_repo_helpers_identical_and_no_dockerhub_release(self):
        helpers = [(ROOT.parent / repo / ".github/scripts/prepare-ecr-release.py").read_text()
                   for repo in ("KTB4-12th-BE", "KTB4-12th-FE")]
        self.assertEqual(*helpers)
        for repo in ("KTB4-12th-BE", "KTB4-12th-FE"):
            release = (ROOT.parent / repo / ".github/workflows/ci.yml").read_text().split("\n  publish:\n")[1]
            self.assertIn("id-token: write", release)
            self.assertNotIn("DOCKERHUB", release)
            self.assertIn("org.opencontainers.image.revision", release)

    def test_release_targets_and_retry_conflict(self):
        helper = load("publisher", ROOT.parent / "KTB4-12th-BE/.github/scripts/prepare-ecr-release.py")
        calls = []
        existing = False
        def run(command):
            calls.append(command)
            if command[:3] == ["aws", "ecr", "describe-repositories"]:
                return json.dumps({"repositories": [{"imageTagMutability": "IMMUTABLE"}]})
            if command[:3] == ["aws", "ecr", "batch-get-image"]:
                return json.dumps({"images": [{}]} if existing else {"failures": [{"failureCode": "ImageNotFound"}]})
            if command[:2] == ["docker", "pull"]:
                return ""
            return "b" * 40
        values = helper.resolve("seonjalal/production/backend", "2.0.0", "a" * 40, cd.REGISTRY,
                                ["main-backend", "payment-server"], run)
        self.assertEqual(values["main_backend_exists"], "false")
        self.assertTrue(values["payment_server_uri"].endswith(":payment-server-2.0.0"))
        existing = True
        with self.assertRaises(ValueError):
            helper.resolve("seonjalal/production/backend", "2.0.0", "a" * 40, cd.REGISTRY,
                           ["main-backend", "payment-server"], run)

    def test_reject_version_and_registry_injection(self):
        helper = load("publisher2", ROOT.parent / "KTB4-12th-FE/.github/scripts/prepare-ecr-release.py")
        for value in ("latest", "2.0.0+build", "2.0.0;evil", "../2.0.0"):
            with self.assertRaises(ValueError):
                helper.resolve("seonjalal/production/frontend", value, "a" * 40, cd.REGISTRY, ["frontend"])


class DeliveryTests(unittest.TestCase):
    def test_frontend_preflight_metadata_and_no_container_start(self):
        operations = []
        def api(service, operation, *args):
            operations.append(operation)
            if operation == "get-public-access-block":
                return {"PublicAccessBlockConfiguration": {key: True for key in (
                    "BlockPublicAcls", "IgnorePublicAcls", "BlockPublicPolicy", "RestrictPublicBuckets")}}
            if operation == "get-bucket-versioning":
                return {"Status": "Enabled"}
            if operation == "get-distribution":
                return {"Distribution": {"Status": "Deployed", "DistributionConfig": {"Enabled": True,
                    "Origins": {"Items": [{"Id": "s3", "DomainName": cd.BUCKET + ".s3.ap-northeast-2.amazonaws.com",
                        "OriginPath": "/frontend/staging", "OriginAccessControlId": "oac"}]},
                    "DefaultCacheBehavior": {"TargetOriginId": "s3"}}}}
            if operation == "get-bucket-policy":
                return {"Policy": json.dumps({"Statement": [{"Effect": "Allow", "Principal": {"Service": "cloudfront.amazonaws.com"},
                    "Action": "s3:GetObject", "Resource": f"arn:aws:s3:::{cd.BUCKET}/frontend/staging/*",
                    "Condition": {"StringEquals": {"AWS:SourceArn": f"arn:aws:cloudfront::{cd.ACCOUNT}:distribution/E123456789"}}}]})}
            if operation == "describe-repositories":
                return {"repositories": [{"imageTagMutability": "IMMUTABLE"}]}
            if operation == "describe-images":
                return {"imageDetails": [{"imageDigest": "sha256:" + "a" * 64}]}
            if operation == "get-object":
                key = args[args.index("--key") + 1]
                Path(args[-1]).write_text("old-index" if key.endswith("index.html") else "same-asset")
                return {"VersionId": "old", "ETag": "etag"}
            self.fail("사전 검사 중 허용하지 않은 API: " + operation)
        def docker(args):
            self.assertNotIn("run", args)
            self.assertNotIn("start", args)
            if args[1] == "create":
                return "a" * 64
            if args[1] == "cp":
                site = Path(args[-1])
                (site / "assets").mkdir()
                (site / "assets/app-hash.js").write_text("same-asset")
                (site / "index.html").write_text("new-index")
            return ""
        with tempfile.TemporaryDirectory() as temporary, patch.object(cd, "command", side_effect=docker), patch.dict(
                os.environ, {"V2_FRONTEND_BUCKET": cd.BUCKET, "V2_CLOUDFRONT_DISTRIBUTION_ID": "E123456789"}):
            plan = cd.prepare_frontend(api, "staging", "1.3.2", Path(temporary))
            self.assertEqual(plan["existing"], {"assets/app-hash.js"})
        self.assertFalse(set(operations) & cd.MUTATIONS)

    def test_definition_changes_only_target_image(self):
        original = task("family", "backend")
        before = copy.deepcopy(original)
        output = cd.render_task(original, "backend", "pinned@sha256:" + "a" * 64)
        self.assertEqual(original, before)
        self.assertNotIn("taskDefinitionArn", output)
        self.assertNotIn("revision", output)
        self.assertEqual(output["containerDefinitions"][0]["environment"], original["containerDefinitions"][0]["environment"])
        self.assertTrue(output["containerDefinitions"][0]["image"].startswith("pinned@"))

    def test_bridge_task_definition_is_allowed_and_unknown_mode_is_rejected(self):
        bridge = task("family", "backend")
        bridge["networkMode"] = "bridge"
        self.assertEqual(cd.render_task(bridge, "backend", "pinned@sha256:" + "a" * 64)["networkMode"], "bridge")
        for mode in ("host", "none", None):
            broken = task("family", "backend")
            broken["networkMode"] = mode
            with self.assertRaises(cd.DeliveryError):
                cd.render_task(broken, "backend", "pinned@sha256:" + "a" * 64)

    def _slot_api(self, active):
        """active: 서비스 이름 -> desiredCount. 없는 이름은 describe-services가 빈 목록을 반환."""
        def api(service, operation, *args):
            self.assertEqual(operation, "describe-services")
            name = args[args.index("--services") + 1]
            if name not in active:
                return {"services": [], "failures": [{"reason": "MISSING"}]}
            return {"services": [{"status": "ACTIVE", "desiredCount": active[name]}]}
        return api

    def test_slot_auto_selects_only_the_live_slot(self):
        cluster, env = "seonjalal-v2-staging-cluster", "staging"
        blue = {"seonjalal-v2-staging-main-backend": 2}
        green = {"seonjalal-v2-staging-main-backend-b": 2}
        self.assertEqual(cd.resolve_slot(self._slot_api(blue), cluster, env, "auto"), "")
        self.assertEqual(cd.resolve_slot(self._slot_api(green), cluster, env, "auto"), "-b")
        # 기존 슬롯이 desired 0으로 대기 중이면 서비스 중이 아니다.
        retired = {**green, "seonjalal-v2-staging-main-backend": 0}
        self.assertEqual(cd.resolve_slot(self._slot_api(retired), cluster, env, "auto"), "-b")

    def test_slot_auto_refuses_ambiguous_or_missing_slot(self):
        cluster, env = "seonjalal-v2-staging-cluster", "staging"
        both = {"seonjalal-v2-staging-main-backend": 2, "seonjalal-v2-staging-main-backend-b": 2}
        for active in (both, {}):
            with self.assertRaises(cd.DeliveryError):
                cd.resolve_slot(self._slot_api(active), cluster, env, "auto")

    def test_explicit_slot_skips_discovery_and_rejects_unknown(self):
        def never(*args, **kwargs):
            self.fail("명시한 슬롯은 서비스 조회를 하지 않아야 한다")
        self.assertEqual(cd.resolve_slot(never, "c", "staging", "green"), "-b")
        self.assertEqual(cd.resolve_slot(never, "c", "staging", "blue"), "")
        with self.assertRaises(cd.DeliveryError):
            cd.resolve_slot(never, "c", "staging", "purple")

    def _backend_api(self, suffix, alias, network_mode):
        """지정한 슬롯의 Main/Payment만 정상 서비스로 응답하는 가짜 API."""
        names = {f"seonjalal-v2-staging-payment-server{suffix}": "payment-server",
                 f"seonjalal-v2-staging-main-backend{suffix}": "main-backend"}
        def api(service, operation, *args):
            if operation == "describe-repositories":
                return {"repositories": [{"imageTagMutability": "IMMUTABLE"}]}
            if operation == "describe-images":
                return {"imageDetails": [{"imageDigest": "sha256:" + "a" * 64}]}
            if operation == "describe-services":
                name = args[args.index("--services") + 1]
                if name not in names:
                    return {"services": []}
                value = healthy("td-" + name)
                value["serviceConnectConfiguration"] = {"enabled": True, "namespace": "ns", "services": [
                    {"clientAliases": [{"dnsName": alias, "port": 8090}]}]}
                return {"services": [value]}
            if operation == "describe-task-definition":
                family = args[args.index("--task-definition") + 1][len("td-"):]
                container = names[family]
                definition = task(family, container)
                definition["networkMode"] = network_mode
                definition["containerDefinitions"][0]["environment"][1]["value"] = f"http://{alias}:8090"
                return {"taskDefinition": definition}
            self.fail("예상하지 않은 API: " + operation)
        return api

    def test_green_slot_deploys_to_suffixed_services_with_isolated_payment_alias(self):
        api = self._backend_api("-b", "payment-server-b", "bridge")
        plans = cd.prepare_backend(api, "staging", "2.0.1", "split-v2", True, "green")
        self.assertEqual([p["service"] for p in plans],
                         ["seonjalal-v2-staging-payment-server-b", "seonjalal-v2-staging-main-backend-b"])
        self.assertEqual({p["slot"] for p in plans}, {"green"})
        self.assertTrue(all(p["definition"]["networkMode"] == "bridge" for p in plans))

    def test_blue_slot_keeps_existing_names_and_alias(self):
        api = self._backend_api("", "payment-server", "awsvpc")
        plans = cd.prepare_backend(api, "staging", "2.0.1", "split-v2", True, "blue")
        self.assertEqual([p["service"] for p in plans],
                         ["seonjalal-v2-staging-payment-server", "seonjalal-v2-staging-main-backend"])
        self.assertEqual({p["slot"] for p in plans}, {"blue"})

    def test_slot_alias_mismatch_is_rejected(self):
        # 새 슬롯 서비스인데 Main이 기존 슬롯의 Payment 주소를 바라보면 슬롯 분리가 깨진다.
        api = self._backend_api("-b", "payment-server", "bridge")
        with self.assertRaises(cd.DeliveryError):
            cd.prepare_backend(api, "staging", "2.0.1", "split-v2", True, "green")

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

    def test_static_rejects_symlinks_maps_and_hidden_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            site = Path(temporary)
            (site / "index.html").write_text("index")
            self.assertEqual(len(cd.validate_site(site)), 1)
            for name in ("secret.map", ".env", "other.html"):
                path = site / name
                path.write_text("not-public")
                with self.assertRaises(cd.DeliveryError):
                    cd.validate_site(site)
                path.unlink()
            (site / "linked.js").symlink_to(site / "index.html")
            with self.assertRaises(cd.DeliveryError):
                cd.validate_site(site)

    def test_assets_before_index_and_no_delete(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            site = directory / "site"
            (site / "assets").mkdir(parents=True)
            (site / "assets/app-hash.js").write_text("new-js")
            (site / "index.html").write_text("new-index")
            previous = directory / "previous"
            previous.write_text("old-index")
            objects = {"frontend/staging/index.html": b"old-index"}
            writes = []
            def api(service, operation, *args):
                if operation == "get-object":
                    key = args[args.index("--key") + 1]
                    Path(args[-1]).write_bytes(objects[key])
                    return {"VersionId": "old" if objects[key] == b"old-index" else "new", "ETag": "test-etag"}
                if operation == "put-object":
                    key = args[args.index("--key") + 1]
                    writes.append(key)
                    objects[key] = Path(args[args.index("--body") + 1]).read_bytes()
                    return {"VersionId": "new"}
                if operation == "create-invalidation":
                    return {"Invalidation": {"Id": "inv"}}
                if operation == "wait":
                    return {}
                self.fail("예상하지 않은 API: " + operation)
            plan = {"files": list(site.rglob("*.js")) + [site / "index.html"], "site": site,
                    "prefix": "frontend/staging/", "bucket": cd.BUCKET, "distribution": "DIST",
                    "previous": previous, "previous_metadata": {"VersionId": "old"}, "index_attempted": False}
            cd.apply_frontend(api, plan)
            self.assertEqual(writes, ["frontend/staging/assets/app-hash.js", "frontend/staging/index.html"])

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

    def test_frontend_rollback_uses_conditional_write_and_checks_content(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            site = directory / "site"
            site.mkdir()
            (site / "index.html").write_bytes(b"new-index")
            previous = directory / "previous"
            previous.write_bytes(b"old-index")
            current = b"new-index"
            def api(service, operation, *args):
                nonlocal current
                if operation == "get-object":
                    Path(args[-1]).write_bytes(current)
                    return {"ETag": "current-etag"}
                if operation == "put-object":
                    self.assertEqual(args[args.index("--if-match") + 1], "current-etag")
                    current = Path(args[args.index("--body") + 1]).read_bytes()
                    return {}
                if operation == "create-invalidation":
                    return {"Invalidation": {"Id": "inv"}}
                return {}
            plan = {"site": site, "previous": previous, "previous_metadata": {"CacheControl": "no-cache"},
                    "index_attempted": True, "bucket": "bucket", "prefix": "frontend/staging/", "distribution": "DIST"}
            cd.rollback_frontend(api, plan)
            self.assertEqual(current, b"old-index")

    def test_frontend_rollback_failure_does_not_skip_backend_recovery(self):
        args = argparse.Namespace(environment="staging", backend_version="2.0.0", frontend_version="2.0.0", component="all",
                                  backend_layout="split-v2", compatibility_reviewed="true", apply=True)
        frontend = {"bucket": "bucket", "prefix": "frontend/staging/", "distribution": "DIST", "image": "pinned"}
        with tempfile.TemporaryDirectory() as temporary, \
                patch.object(cd, "prepare_backend", return_value=[]), \
                patch.object(cd, "prepare_frontend", return_value=frontend), \
                patch.object(cd, "apply_backend"), \
                patch.object(cd, "apply_frontend", side_effect=cd.DeliveryError("업로드 실패")), \
                patch.object(cd, "rollback_frontend", side_effect=cd.DeliveryError("FE복구실패")), \
                patch.object(cd, "rollback_backend") as backend_restore:
            with self.assertRaises(cd.DeliveryError):
                cd.deliver(args, lambda *a: {"Account": cd.ACCOUNT}, Path(temporary))
            backend_restore.assert_called_once()
            receipt = json.loads((Path(temporary) / "receipt.json").read_text())
            self.assertIn("manual intervention required: frontend", receipt["rollback"])


if __name__ == "__main__":
    unittest.main()
