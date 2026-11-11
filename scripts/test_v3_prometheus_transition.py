"""실제 Docker/파일/네트워크 없이 TSDB 단일 실행과 실패 복구 순서 검증."""
import copy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).parent / "lib"))
from v3_prometheus_transition import NAME, LABEL, DESTINATION, apply, create_body, rollback, verify_loaded_config, verify_container_snapshot


def snapshot():
    return {"Id": "old", "Name": "/" + NAME, "State": {"Running": True},
            "Config": {"Image": "prometheus@sha256:fixture", "Cmd": ["--query.max-concurrency=4"],
                       "User": "nobody", "Env": ["PRESERVED=fixture"], "Labels": {}},
            "HostConfig": {"Memory": 671088640, "NanoCpus": 750000000,
                           "PortBindings": {"9090/tcp": [{"HostIp": "127.0.0.1", "HostPort": "9090"}]},
                           "Mounts": [{"Type": "volume", "Source": "original-tsdb", "Target": "/prometheus"}]},
            "NetworkSettings": {"Networks": {"seonjalal-monitor": {"Aliases": ["prometheus", "old"]}}}}


class FakeEngine:
    def __init__(self, original):
        self.objects = {"old": copy.deepcopy(original)}
        self.operations = []

    def get(self, name):
        value = next((item for key, item in self.objects.items()
                      if key == name or item["Name"] == "/" + name), None)
        return copy.deepcopy(value)

    def stop(self, identifier):
        self.operations.append(("stop", identifier))
        self.objects[identifier]["State"]["Running"] = False

    def rename(self, identifier, name):
        self.operations.append(("rename", identifier))
        self.objects[identifier]["Name"] = "/" + name

    def create(self, name, body):
        self.operations.append(("create", "new"))
        self.objects["new"] = {"Id": "new", "Name": "/" + name, "Config": body,
                               "State": {"Running": False}}
        return "new"

    def start(self, identifier):
        if any(item["State"]["Running"] for key, item in self.objects.items() if key != identifier):
            raise AssertionError("두 TSDB writer 동시 실행")
        self.operations.append(("start", identifier))
        self.objects[identifier]["State"]["Running"] = True


class TransitionTest(unittest.TestCase):
    def setUp(self):
        self.original = snapshot()
        self.engine = FakeEngine(self.original)
        self.body = create_body(self.original, "192.0.2.10", "/rules/payment.yml", "fixture-unit")
        self.changed = False
        self.events = []

    def activate(self):
        self.changed = True

    def restore(self):
        self.changed = False

    def execute(self, verify=lambda identifier: None, activate=None):
        apply(self.engine, self.original, self.body, "fixture-unit", activate or self.activate,
              lambda: self.changed, self.restore, lambda: None, verify,
              lambda phase, **values: self.events.append(phase))

    def test_payload_preserves_original_and_does_not_mutate_snapshot(self):
        self.assertEqual(self.original, snapshot())
        for key in ("User", "Image", "Env"):
            self.assertEqual(self.body[key], self.original["Config"][key])
        self.assertEqual(self.body["HostConfig"]["Mounts"][0], self.original["HostConfig"]["Mounts"][0])
        self.assertEqual(self.body["HostConfig"]["Memory"], 671088640)
        self.assertEqual(self.body["HostConfig"]["NanoCpus"], 750000000)
        self.assertEqual(self.body["NetworkingConfig"]["EndpointsConfig"]["seonjalal-monitor"]["Aliases"], ["prometheus"])

    def test_success_stops_original_before_new_writer(self):
        self.execute()
        self.assertFalse(self.engine.get("old")["State"]["Running"])
        self.assertTrue(self.engine.get(NAME)["State"]["Running"])
        self.assertEqual(self.engine.operations[0], ("stop", "old"))
        self.assertEqual(self.events[-1], "applied")

    def test_verification_failure_restores_original(self):
        def fail(identifier):
            raise RuntimeError("simulated_not_ready")
        with self.assertRaisesRegex(RuntimeError, "original_restored"):
            self.execute(verify=fail)
        self.assertFalse(self.changed)
        self.assertEqual(self.engine.get(NAME)["Id"], "old")
        self.assertTrue(self.engine.get(NAME)["State"]["Running"])
        self.assertFalse(self.engine.get("new")["State"]["Running"])
        self.assertEqual(self.events[-1], "rolled_back")

    def test_activation_failure_before_create_recovers(self):
        def fail():
            raise RuntimeError("simulated_disk_failure")
        with self.assertRaisesRegex(RuntimeError, "original_restored"):
            self.execute(activate=fail)
        self.assertTrue(self.engine.get(NAME)["State"]["Running"])
        self.assertIsNone(self.engine.get("new"))

    def test_rollback_does_not_stop_foreign_container(self):
        self.engine.rename("old", "backup")
        self.engine.objects["foreign"] = {"Id": "foreign", "Name": "/" + NAME,
            "Config": {"Labels": {}}, "State": {"Running": True}}
        with self.assertRaisesRegex(RuntimeError, "foreign_container"):
            rollback(self.engine, "old", "fixture-unit", lambda: False, self.restore, lambda: None)
        self.assertTrue(self.engine.get("foreign")["State"]["Running"])

    def test_runtime_config_comparison_accepts_normalized_defaults(self):
        before = {"global": {"scrape_interval": "15s", "evaluation_interval": "1m"},
                  "scrape_configs": [{"job_name": "preserved", "follow_redirects": True}],
                  "alerting": {"alertmanagers": []}}
        after = copy.deepcopy(before)
        after["rule_files"] = [DESTINATION]
        after["alerting"]["alertmanagers"] = [{"scheme": "http", "timeout": "10s", "api_version": "v2",
            "static_configs": [{"targets": ["alertmanager:9093"]}]}]
        verify_loaded_config(before, after)
        after["global"]["scrape_interval"] = "30s"
        with self.assertRaisesRegex(RuntimeError, "existing_runtime_config_changed"):
            verify_loaded_config(before, after)

    def test_mount_order_does_not_hide_real_mount_changes(self):
        before = snapshot()
        before["Mounts"] = [
            {"Type": "bind", "Source": "/config/a", "Destination": "/etc/config", "RW": False},
            {"Type": "volume", "Name": "original", "Source": "/data", "Destination": "/prometheus", "RW": True},
        ]
        current = copy.deepcopy(before)
        current["Mounts"].reverse()
        self.assertTrue(verify_container_snapshot(before, current))
        current["Mounts"][0]["Name"] = "different-volume"
        with self.assertRaisesRegex(RuntimeError, "drift_mounts"):
            verify_container_snapshot(before, current)

    def test_actual_resource_drift_still_blocks(self):
        before = snapshot()
        before["Mounts"] = []
        current = copy.deepcopy(before)
        current["HostConfig"]["Memory"] = 1
        with self.assertRaisesRegex(RuntimeError, "drift_hostconfig"):
            verify_container_snapshot(before, current)


if __name__ == "__main__":
    unittest.main()
