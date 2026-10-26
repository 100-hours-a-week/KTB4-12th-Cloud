"""가짜 Host로 팀 접속 전환 로직을 검증한다. AWS/Docker/원격 호출 없음."""
import ast
import base64
import copy
import importlib.util
import json
from pathlib import Path
import unittest

HERE = Path(__file__).resolve().parent
remote = importlib.util.module_from_spec(importlib.util.spec_from_file_location("remote", HERE / "grafana_team_access_remote.py"))
remote.__spec__.loader.exec_module(remote)
prepare_spec = importlib.util.spec_from_file_location("prepare", HERE / "prepare-grafana-team-access.py")
prepare = importlib.util.module_from_spec(prepare_spec)
prepare_spec.loader.exec_module(prepare)

DIGEST = "grafana/grafana@sha256:" + "a" * 64
OLD_ENV = ["GF_SECURITY_ADMIN_USER=admin", "GF_USERS_ALLOW_SIGN_UP=false", "PATH=/usr/bin"]
OLD_INSPECT = {
    "State": {"Running": True},
    "Config": {"Env": OLD_ENV},
    "HostConfig": {"Memory": 384 * 1024 ** 2, "PortBindings": {"3000/tcp": [{"HostIp": "127.0.0.1", "HostPort": "3000"}]}},
    "Mounts": [
        {"Type": "volume", "Name": "seonjalal-monitor-grafana-data", "Source": "/var/lib/docker/volumes/x", "Destination": "/var/lib/grafana", "RW": True},
        {"Type": "bind", "Source": "/opt/seonjalal-observability/secrets/grafana-admin-password", "Destination": "/run/secrets/grafana-admin-password", "RW": False},
        {"Type": "bind", "Source": "/opt/seonjalal-observability/config/provisioning", "Destination": "/etc/grafana/provisioning", "RW": False},
    ],
}
DASHBOARD = json.loads(prepare.DASHBOARD.read_text(encoding="utf-8"))


class FakeHost:
    def __init__(self, identity=None, memory=2000, image=DIGEST, fail_run=False, unhealthy_after_swap=False,
                 lose_datasource=False, editor_permission=1):
        self.ident = identity or dict(remote.EXPECTED)
        self.memory = memory
        self.files = {str(remote.ROOT / "image-lock.json"): json.dumps({"grafana": {"digest": image}})}
        self.containers = {remote.CONTAINER: copy.deepcopy(OLD_INSPECT)}
        self.running = {remote.CONTAINER}
        self.events = []
        self.run_args = None
        self.fail_run, self.unhealthy_after_swap, self.lose_datasource = fail_run, unhealthy_after_swap, lose_datasource
        self.editor_permission = editor_permission
        self.folders, self.permissions, self.dashboards = {}, [], {}
        self.swapped = False

    def identity(self):
        self.events.append("identity")
        return self.ident

    def lock(self):
        self.events.append("lock")

    def available_memory_mib(self):
        return self.memory

    def read(self, path):
        return self.files[str(path)]

    def write_private(self, path, text):
        self.events.append("backup")
        self.files[str(path)] = text

    def sleep(self, seconds):
        pass

    def docker(self, *args, check=True):
        verb = args[0]
        if verb != "inspect":
            self.events.append(verb)
        if verb == "inspect":
            if args[1] in self.containers:
                return 0, json.dumps([self.containers[args[1]]])
            return 1, ""
        if verb == "stop":
            self.running.discard(args[1])
            return 0, ""
        if verb == "rename":
            self.containers[args[2]] = self.containers.pop(args[1])
            self.running = {args[2] if name == args[1] else name for name in self.running}
            return 0, ""
        if verb == "start":
            self.running.add(args[1])
            return 0, ""
        if verb == "rm":
            self.containers.pop(args[2], None)
            self.running.discard(args[2])
            return 0, ""
        if verb == "run":
            self.run_args = list(args)
            if self.fail_run:
                if check:
                    raise SystemExit("STOP: Docker 단계 실패")
                return 1, ""
            env = [args[i + 1] for i, v in enumerate(args) if v == "-e"]
            publish = [args[i + 1] for i, v in enumerate(args) if v == "--publish"]
            mounts = [args[i + 1] for i, v in enumerate(args) if v == "-v"]
            bindings = [{"HostIp": item.rsplit(":3000:3000", 1)[0], "HostPort": "3000"} for item in publish]
            self.containers[remote.CONTAINER] = {
                "State": {"Running": True}, "Config": {"Env": env},
                "HostConfig": {"Memory": remote.MEMORY_BYTES, "PortBindings": {"3000/tcp": bindings}},
                "Mounts": [{"Type": "bind", "Source": m.split(":")[0], "Destination": m.split(":")[1], "RW": not m.endswith(":ro")} for m in mounts]}
            self.running.add(remote.CONTAINER)
            self.swapped = True
            return 0, ""
        raise AssertionError(args)

    def api(self, method, path, body=None):
        if path == "/api/health":
            healthy = remote.CONTAINER in self.running and not (self.swapped and self.unhealthy_after_swap)
            return (200, {"database": "ok"}) if healthy else (0, None)
        if path == "/api/datasources":
            return 200, ([] if self.swapped and self.lose_datasource else [{"name": "CloudWatch"}, {"name": "Prometheus"}])
        if path == f"/api/folders/{remote.FOLDER_UID}" and method == "GET":
            return (200, {}) if self.folders else (404, None)
        if path == "/api/folders":
            self.folders[body["uid"]] = body["title"]
            return 200, {}
        if path.endswith("/permissions") and method == "POST":
            self.permissions = [{"role": item["role"], "permission": item["permission"] if item["role"] != "Editor" else self.editor_permission}
                                for item in body["items"]]
            return 200, {}
        if path.endswith("/permissions"):
            return 200, self.permissions
        if path == "/api/dashboards/db":
            self.dashboards[body["dashboard"]["uid"]] = body["folderUid"]
            return 200, {}
        if path.startswith("/api/dashboards/uid/"):
            uid = path.rsplit("/", 1)[1]
            return 200, {"meta": {"folderUid": self.dashboards[uid]}}
        raise AssertionError((method, path))


def execute(host):
    lines = []
    remote.run(host, DASHBOARD, now="T0", out=lines.append)
    return lines


class SwapTests(unittest.TestCase):
    def test_swap_enables_signup_private_binding_and_keeps_data(self):
        host = FakeHost()
        execute(host)
        args = host.run_args
        for item in ("GF_USERS_ALLOW_SIGN_UP=true", "GF_USERS_AUTO_ASSIGN_ORG_ROLE=Editor", "GF_USERS_ALLOW_ORG_CREATE=false",
                     "GF_USERS_VIEWERS_CAN_EDIT=false", "GF_AUTH_ANONYMOUS_ENABLED=false"):
            self.assertIn(item, args)
        self.assertIn("127.0.0.1:3000:3000", args)
        self.assertIn("10.20.10.150:3000:3000", args)
        self.assertEqual(args[args.index("--memory") + 1], "1g")
        self.assertEqual(args[-1], DIGEST)
        self.assertIn("seonjalal-monitor-grafana-data:/var/lib/grafana", args)
        self.assertIn("/opt/seonjalal-observability/secrets/grafana-admin-password:/run/secrets/grafana-admin-password:ro", args)
        self.assertIn("/opt/seonjalal-observability/config/provisioning:/etc/grafana/provisioning:ro", args)

    def test_previous_container_is_kept_stopped_for_rollback(self):
        host = FakeHost()
        execute(host)
        self.assertIn(remote.SAVED, host.containers)
        self.assertNotIn(remote.SAVED, host.running)
        self.assertIn(remote.CONTAINER, host.running)

    def test_backup_and_checks_precede_any_change(self):
        host = FakeHost()
        execute(host)
        self.assertLess(host.events.index("backup"), host.events.index("stop"))
        self.assertLess(host.events.index("identity"), host.events.index("lock"))
        self.assertLess(host.events.index("lock"), host.events.index("backup"))
        self.assertIn("seonjalal-monitor-grafana-before-team-access", host.containers)
        self.assertTrue(any("before-team-access-T0" in key for key in host.files))

    def test_shared_folder_is_read_only_for_editors_and_holds_the_dashboard(self):
        host = FakeHost()
        execute(host)
        self.assertEqual(host.folders, {"shared": "Shared"})
        self.assertEqual({p["role"]: p["permission"] for p in host.permissions}, {"Viewer": 1, "Editor": 1})
        self.assertEqual(host.dashboards, {DASHBOARD["uid"]: "shared"})

    def test_output_has_no_secret_values(self):
        output = "\n".join(execute(FakeHost()))
        self.assertNotIn("admin-password-value", output)
        self.assertIn("NO_PASSWORD_OUTPUT", output)

    def test_second_run_skips_the_swap_and_stays_idempotent(self):
        host = FakeHost()
        execute(host)
        events_after_first = len(host.events)
        lines = execute(host)
        self.assertIn("CONTAINER_ALREADY_CONFIGURED_SKIP_SWAP", lines)
        self.assertEqual([e for e in host.events[events_after_first:] if e in ("stop", "rename", "run", "rm")], [])
        self.assertEqual(host.dashboards, {DASHBOARD["uid"]: "shared"})


class RollbackTests(unittest.TestCase):
    def assert_restored(self, host):
        self.assertIn(remote.CONTAINER, host.running)
        self.assertEqual(host.containers[remote.CONTAINER]["HostConfig"]["Memory"], 384 * 1024 ** 2)
        self.assertNotIn(remote.SAVED, host.containers)

    def test_run_failure_restores_the_previous_container(self):
        host = FakeHost(fail_run=True)
        with self.assertRaises(SystemExit):
            execute(host)
        self.assert_restored(host)
        self.assertEqual(host.dashboards, {})

    def test_unhealthy_new_container_is_rolled_back(self):
        host = FakeHost(unhealthy_after_swap=True)
        with self.assertRaises(SystemExit):
            execute(host)
        self.assert_restored(host)

    def test_lost_datasource_is_rolled_back(self):
        host = FakeHost(lose_datasource=True)
        with self.assertRaises(SystemExit):
            execute(host)
        self.assert_restored(host)


class PreflightTests(unittest.TestCase):
    def test_wrong_target_stops_before_any_change(self):
        for field, value in (("accountId", "111111111111"), ("instanceId", "i-0000"), ("privateIp", "10.20.10.99"), ("region", "us-east-1")):
            host = FakeHost(identity={**remote.EXPECTED, field: value})
            with self.assertRaises(SystemExit):
                execute(host)
            self.assertEqual(host.events, ["identity"], field)

    def test_low_memory_stops_before_any_change(self):
        host = FakeHost(memory=300)
        with self.assertRaises(SystemExit):
            execute(host)
        self.assertNotIn("stop", host.events)

    def test_mutable_image_is_refused(self):
        for image in ("grafana/grafana:latest", "grafana/grafana:13.2.4"):
            host = FakeHost(image=image)
            with self.assertRaises(SystemExit):
                execute(host)
            self.assertNotIn("stop", host.events)

    def test_editor_write_permission_is_detected(self):
        host = FakeHost(editor_permission=2)
        with self.assertRaises(SystemExit):
            execute(host)
        self.assertEqual(host.dashboards, {})


class RequestTests(unittest.TestCase):
    def test_request_targets_only_the_monitor_instance(self):
        request = prepare.build_request()
        self.assertEqual(request["InstanceIds"], ["i-0c6a75f630436591c"])
        self.assertEqual(request["DocumentName"], "AWS-RunShellScript")

    def test_payload_roundtrip_contains_logic_and_dashboard(self):
        command = prepare.build_request()["Parameters"]["commands"][0]
        tree = ast.parse(command.split("\n", 1)[1].rsplit("\nPY", 1)[0])
        call = tree.body[1].value.args[0].args[0]
        payload = base64.b64decode(ast.literal_eval(call.args[0])).decode()
        ast.parse(payload)
        self.assertTrue(payload.startswith("DASHBOARD_B64 = "))
        self.assertTrue(payload.rstrip().endswith("main()"))
        encoded = ast.literal_eval(ast.parse(payload.split("\n", 1)[0]).body[0].value)
        self.assertEqual(json.loads(base64.b64decode(encoded))["uid"], DASHBOARD["uid"])

    def test_dashboard_is_environment_agnostic(self):
        text = prepare.DASHBOARD.read_text(encoding="utf-8")
        for fixed in ("seonjalal-v2", "i-0", "app/seonjalal"):
            self.assertNotIn(fixed, text)
        self.assertTrue(all(p["datasource"]["uid"] == "${ds}" for p in DASHBOARD["panels"]))

    def test_no_secret_is_logged_by_the_remote_code(self):
        source = (HERE / "grafana_team_access_remote.py").read_text(encoding="utf-8")
        printed = [line for line in source.splitlines() if "out(" in line or "print(" in line]
        self.assertFalse([line for line in printed if "read(" in line or "credential" in line])


if __name__ == "__main__":
    unittest.main()
