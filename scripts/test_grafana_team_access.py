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
DASHBOARD_PATH = HERE.parent / "operations" / "grafana" / "dashboards" / "cloudwatch-load-test.json"

DIGEST = "grafana/grafana@sha256:" + "a" * 64
OLD_INSPECT = {
    "State": {"Running": True},
    "Config": {"Env": ["GF_SECURITY_ADMIN_USER=admin", "GF_USERS_ALLOW_SIGN_UP=false", "PATH=/usr/bin"]},
    "HostConfig": {"Memory": 384 * 1024 ** 2, "PortBindings": {"3000/tcp": [{"HostIp": "127.0.0.1", "HostPort": "3000"}]}},
    "Mounts": [
        {"Type": "volume", "Name": "seonjalal-monitor-grafana-data", "Source": "/var/lib/docker/volumes/x", "Destination": "/var/lib/grafana", "RW": True},
        {"Type": "bind", "Source": "/opt/seonjalal-observability/secrets/grafana-admin-password", "Destination": "/run/secrets/grafana-admin-password", "RW": False},
        {"Type": "bind", "Source": "/opt/seonjalal-observability/config/provisioning", "Destination": "/etc/grafana/provisioning", "RW": False},
    ],
}


class FakeHost:
    def __init__(self, identity=None, memory=2000, image=DIGEST, fail_run=False, unhealthy_after_swap=False,
                 drop_mounts=False, old_unhealthy=False):
        self.ident = identity or dict(remote.EXPECTED)
        self.memory = memory
        self.files = {str(remote.ROOT / "image-lock.json"): json.dumps({"grafana": {"digest": image}})}
        self.containers = {remote.CONTAINER: copy.deepcopy(OLD_INSPECT)}
        self.running = {remote.CONTAINER}
        self.events = []
        self.run_args = None
        self.fail_run, self.unhealthy_after_swap, self.drop_mounts, self.old_unhealthy = fail_run, unhealthy_after_swap, drop_mounts, old_unhealthy
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

    def healthy(self):
        if self.old_unhealthy and not self.swapped:
            return False
        return remote.CONTAINER in self.running and not (self.swapped and self.unhealthy_after_swap)

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
            if self.drop_mounts:
                mounts = mounts[1:]
            bindings = [{"HostIp": item.rsplit(":3000:3000", 1)[0], "HostPort": "3000"} for item in publish]
            self.containers[remote.CONTAINER] = {
                "State": {"Running": True}, "Config": {"Env": env},
                "HostConfig": {"Memory": remote.MEMORY_BYTES, "PortBindings": {"3000/tcp": bindings}},
                "Mounts": [{"Type": "bind", "Source": m.split(":")[0], "Destination": m.split(":")[1], "RW": not m.endswith(":ro")} for m in mounts]}
            self.running.add(remote.CONTAINER)
            self.swapped = True
            return 0, ""
        raise AssertionError(args)


def execute(host):
    lines = []
    remote.run(host, now="T0", out=lines.append)
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

    def test_only_loopback_and_private_ip_are_published(self):
        host = FakeHost()
        execute(host)
        publish = [host.run_args[i + 1] for i, v in enumerate(host.run_args) if v == "--publish"]
        self.assertEqual(sorted(publish), ["10.20.10.150:3000:3000", "127.0.0.1:3000:3000"])
        self.assertFalse([p for p in publish if p.startswith("0.0.0.0")])

    def test_previous_container_is_kept_stopped_for_rollback(self):
        host = FakeHost()
        execute(host)
        self.assertIn(remote.SAVED, host.containers)
        self.assertNotIn(remote.SAVED, host.running)
        self.assertIn(remote.CONTAINER, host.running)

    def test_backup_and_checks_precede_any_change(self):
        host = FakeHost()
        execute(host)
        self.assertLess(host.events.index("identity"), host.events.index("lock"))
        self.assertLess(host.events.index("lock"), host.events.index("backup"))
        self.assertLess(host.events.index("backup"), host.events.index("stop"))
        self.assertTrue(any("before-team-access-T0" in key for key in host.files))

    def test_no_admin_login_is_attempted(self):
        source = (HERE / "grafana_team_access_remote.py").read_text(encoding="utf-8")
        for word in ("Authorization", "Basic ", "grafana-admin-password\").read", "PASSWORD_FILE", "/api/datasources"):
            self.assertNotIn(word, source)
        self.assertFalse(hasattr(remote.Host, "api"))

    def test_output_names_the_next_manual_step_and_no_secret(self):
        output = "\n".join(execute(FakeHost()))
        self.assertIn("NEXT=admin sets Shared folder permissions in the UI", output)
        self.assertIn("NO_ADMIN_LOGIN_NO_PASSWORD_USE", output)

    def test_second_run_changes_nothing(self):
        host = FakeHost()
        execute(host)
        events_after_first = len(host.events)
        lines = execute(host)
        self.assertEqual(lines, ["CONTAINER_ALREADY_CONFIGURED_NOTHING_CHANGED"])
        self.assertEqual([e for e in host.events[events_after_first:] if e in ("stop", "rename", "run", "rm", "backup")], [])


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

    def test_unhealthy_new_container_is_rolled_back(self):
        host = FakeHost(unhealthy_after_swap=True)
        with self.assertRaises(SystemExit):
            execute(host)
        self.assert_restored(host)

    def test_missing_data_mount_is_rolled_back(self):
        host = FakeHost(drop_mounts=True)
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

    def test_unhealthy_current_grafana_stops_before_any_change(self):
        host = FakeHost(old_unhealthy=True)
        with self.assertRaises(SystemExit):
            execute(host)
        self.assertNotIn("stop", host.events)


class RequestTests(unittest.TestCase):
    def test_request_targets_only_the_monitor_instance(self):
        request = prepare.build_request()
        self.assertEqual(request["InstanceIds"], ["i-0c6a75f630436591c"])
        self.assertEqual(request["DocumentName"], "AWS-RunShellScript")

    def test_payload_roundtrip_contains_the_logic(self):
        command = prepare.build_request()["Parameters"]["commands"][0]
        tree = ast.parse(command.split("\n", 1)[1].rsplit("\nPY", 1)[0])
        call = tree.body[1].value.args[0].args[0]
        payload = base64.b64decode(ast.literal_eval(call.args[0])).decode()
        ast.parse(payload)
        self.assertIn("def swap_container", payload)
        self.assertTrue(payload.rstrip().endswith("main()"))

    def test_dashboard_is_environment_agnostic(self):
        text = DASHBOARD_PATH.read_text(encoding="utf-8")
        dashboard = json.loads(text)
        for fixed in ("seonjalal-v2", "i-0", "app/seonjalal"):
            self.assertNotIn(fixed, text)
        self.assertTrue(all(p["datasource"]["uid"] == "${ds}" for p in dashboard["panels"]))


if __name__ == "__main__":
    unittest.main()
