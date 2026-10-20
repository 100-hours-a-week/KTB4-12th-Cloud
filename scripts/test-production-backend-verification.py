#!/usr/bin/env python3
"""실제 AWS/레지스트리 호출 없는 ECR 동일성 검증 회귀 검사."""
import copy
import importlib.util
import json
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location("verify", Path(__file__).with_name("verify-production-backend.py"))
verify = importlib.util.module_from_spec(spec)
spec.loader.exec_module(verify)


class VerificationTest(unittest.TestCase):
    def setUp(self):
        self.source = {"config": {"digest": verify.CONFIG, "size": 100},
                       "layers": [{"digest": "sha256:" + "a" * 64, "size": 200}]}
        self.response = {"failures": [], "images": [{"registryId": verify.ACCOUNT,
            "repositoryName": verify.REPOSITORY, "imageId": {"imageDigest": "sha256:" + "b" * 64},
            "imageManifest": json.dumps(self.source)}]}

    def test_identical(self):
        self.assertEqual(verify.compare(self.source, self.response), "sha256:" + "b" * 64)

    def test_other_repository(self):
        self.response["images"][0]["repositoryName"] = "other/backend"
        with self.assertRaises(ValueError):
            verify.compare(self.source, self.response)

    def test_layer_change(self):
        target = copy.deepcopy(self.source)
        target["layers"][0]["digest"] = "sha256:" + "c" * 64
        self.response["images"][0]["imageManifest"] = json.dumps(target)
        with self.assertRaises(ValueError):
            verify.compare(self.source, self.response)

    def test_config_change(self):
        target = copy.deepcopy(self.source)
        target["config"]["digest"] = "sha256:" + "c" * 64
        self.response["images"][0]["imageManifest"] = json.dumps(target)
        with self.assertRaises(ValueError):
            verify.compare(self.source, self.response)

    def test_index_rejected(self):
        self.response["images"][0]["imageManifest"] = json.dumps({"manifests": []})
        with self.assertRaises(ValueError):
            verify.compare(self.source, self.response)

    def test_api_failure(self):
        self.response["failures"] = [{"failureCode": "ImageNotFound"}]
        with self.assertRaises(ValueError):
            verify.compare(self.source, self.response)


if __name__ == "__main__":
    unittest.main()
