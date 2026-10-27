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


FLAG_OPTIONS = {"--detach"}
VALUE_OPTIONS = {"--restart", "--network", "--cap-drop", "--security-opt", "--pids-limit", "--log-driver", "--log-opt",
                 "--name", "--user", "--memory", "--memory-swap", "--cpus", "--publish", "-e", "-v"}


def parse_docker_run(args):
    """실제 `docker run`처럼 옵션, 값, 이미지, 명령을 나눈다. 모르는 옵션이면 AssertionError.

    `docker run` 뒤에서 옵션이 아닌 첫 단어가 이미지이고 그 뒤는 컨테이너 안에서 실행할 명령이다.
    """
    assert args[0] == "run"
    options, index = [], 1
    while index < len(args):
        token = args[index]
        if token in FLAG_OPTIONS:
            options.append((token, None))
            index += 1
        elif token in VALUE_OPTIONS:
            options.append((token, args[index + 1]))
            index += 2
        elif token.startswith("-"):
            raise AssertionError("알 수 없는 docker run 옵션: " + token)
        else:
            break
    image = args[index] if index < len(args) else None
    return options, image, list(args[index + 1:])


class FakeHost:
    def __init__(self, identity=None, memory=2000, image=DIGEST, fail_run=False, unhealthy_after_swap=False,
                 drop_mounts=False, old_unhealthy=False, misconfigure=False, log_text=""):
        self.ident = identity or dict(remote.EXPECTED)
        self.memory = memory
        self.files = {str(remote.ROOT / "image-lock.json"): json.dumps({"grafana": {"digest": image}})}
        self.containers = {remote.CONTAINER: copy.deepcopy(OLD_INSPECT)}
        self.running = {remote.CONTAINER}
        self.events = []
        self.run_args = None
        self.fail_run, self.unhealthy_after_swap, self.drop_mounts, self.old_unhealthy = fail_run, unhealthy_after_swap, drop_mounts, old_unhealthy
        self.misconfigure = misconfigure
        self.log_text = log_text
        self.last_stderr = ""
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
        self.events.append("evidence" if str(path).endswith("failure-evidence.txt") else "backup")
        self.files[str(path)] = text

    def logs(self, name, tail):
        self.events.append("logs")
        return self.log_text

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
            self.swapped = False
            return 0, ""
        if verb == "run":
            self.run_args = list(args)
            options, image, command = parse_docker_run(args)
            self.run_image, self.run_command = image, command
            if image is None or "@sha256:" not in image:
                # 실제 Docker와 같은 실패: 옵션이 아닌 첫 단어를 이미지로 보고 찾지 못한다.
                self.last_stderr = f"Unable to find image '{image}:latest' locally"
                if check:
                    raise SystemExit("STOP: Docker 단계 실패")
                return 1, ""
            if self.fail_run:
                self.last_stderr = "docker: Error response from daemon: port is already allocated\nsecond line"
                if check:
                    raise SystemExit("STOP: Docker 단계 실패")
                return 1, ""
            env = [value for option, value in options if option == "-e"]
            if self.misconfigure:
                env = [item for item in env if not item.startswith("GF_USERS_ALLOW_SIGN_UP")]
            publish = [value for option, value in options if option == "--publish"]
            mounts = [value for option, value in options if option == "-v"]
            if self.drop_mounts:
                mounts = mounts[1:]
            bindings = [{"HostIp": item.rsplit(":3000:3000", 1)[0], "HostPort": "3000"} for item in publish]
            self.containers[remote.CONTAINER] = {
                "State": {"Running": True, "Status": "exited" if self.unhealthy_after_swap else "running",
                          "ExitCode": 137 if self.unhealthy_after_swap else 0, "OOMKilled": self.unhealthy_after_swap,
                          "Error": "", "StartedAt": "T1", "FinishedAt": "T2"},
                "Config": {"Env": env},
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


def run_failing(host):
    """실패하는 실행의 출력과 예외를 함께 돌려준다."""
    lines = []
    with unittest.TestCase().assertRaises(SystemExit):
        remote.run(host, now="T0", out=lines.append)
    return lines


def evidence_file(host):
    return host.files[str(remote.ROOT / "before-team-access-T0" / "failure-evidence.txt")]


class FailureEvidenceTests(unittest.TestCase):
    def test_docker_run_failure_reports_the_first_stderr_line(self):
        host = FakeHost(fail_run=True)
        lines = run_failing(host)
        text = evidence_file(host)
        self.assertIn("docker run 실패: docker: Error response from daemon: port is already allocated", text)
        self.assertNotIn("second line", text)
        self.assertIn("ROLLBACK_RESTORED_HEALTHY", lines)

    def test_slow_health_reports_the_wait_time_and_container_state(self):
        host = FakeHost(unhealthy_after_swap=True, log_text="level=info msg=starting\nlevel=error msg=boom")
        lines = run_failing(host)
        text = evidence_file(host)
        self.assertIn(f"/api/health가 {remote.HEALTH_ATTEMPTS * remote.HEALTH_INTERVAL}초 안에 정상 응답하지 않음", text)
        self.assertIn("exit_code=137", text)
        self.assertIn("oom_killed=True", text)
        self.assertIn("level=error msg=boom", text)
        self.assertIn("HOST_MEMORY_AVAILABLE_MIB=2000", text)
        self.assertIn("FAILURE_EVIDENCE", lines)
        self.assertIn("ROLLBACK_RESTORED_HEALTHY", lines)

    def test_lost_mount_names_the_destination_only(self):
        host = FakeHost(drop_mounts=True)
        run_failing(host)
        text = evidence_file(host)
        self.assertIn("마운트 누락: /var/lib/grafana", text)
        self.assertNotIn("/var/lib/docker/volumes", text)

    def test_unapplied_setting_names_the_key_without_values(self):
        host = FakeHost(misconfigure=True)
        run_failing(host)
        text = evidence_file(host)
        self.assertIn("설정값 미반영: GF_USERS_ALLOW_SIGN_UP", text)
        self.assertNotIn("GF_USERS_ALLOW_SIGN_UP=", text)

    def test_all_reasons_are_collected_together(self):
        host = FakeHost(unhealthy_after_swap=True, drop_mounts=True, misconfigure=True)
        run_failing(host)
        text = evidence_file(host)
        for expected in ("/api/health", "마운트 누락", "설정값 미반영"):
            self.assertIn(expected, text)

    def test_evidence_is_saved_before_the_failed_container_is_removed(self):
        host = FakeHost(unhealthy_after_swap=True)
        run_failing(host)
        self.assertLess(host.events.index("logs"), host.events.index("rm"))
        self.assertLess(host.events.index("evidence"), host.events.index("rm"))

    def test_lines_with_sensitive_words_are_hidden_everywhere(self):
        secret_lines = "password=hunter2\nAuthorization: Bearer abc\nlevel=info msg=ok\nsession cookie=xyz\napi_key=k1"
        host = FakeHost(unhealthy_after_swap=True, log_text=secret_lines)
        lines = run_failing(host)
        everything = evidence_file(host) + "\n".join(lines)
        for leaked in ("hunter2", "Bearer abc", "xyz", "k1"):
            self.assertNotIn(leaked, everything)
        self.assertIn("level=info msg=ok", everything)
        self.assertIn("[민감 단어가 있어 가린 줄]", everything)

    def test_only_the_last_lines_are_kept(self):
        many = "\n".join(f"line{i}" for i in range(500))
        host = FakeHost(unhealthy_after_swap=True, log_text=many)
        run_failing(host)
        text = evidence_file(host)
        self.assertIn("line499", text)
        self.assertNotIn("line100\n", text)

    def test_successful_swap_writes_no_evidence(self):
        host = FakeHost()
        execute(host)
        self.assertNotIn("evidence", host.events)
        self.assertNotIn("logs", host.events)

    def test_evidence_does_not_change_the_rollback_result(self):
        host = FakeHost(unhealthy_after_swap=True)
        run_failing(host)
        self.assertIn(remote.CONTAINER, host.running)
        self.assertEqual(host.containers[remote.CONTAINER]["HostConfig"]["Memory"], 384 * 1024 ** 2)
        self.assertNotIn(remote.SAVED, host.containers)


class DockerRunShapeTests(unittest.TestCase):
    def test_run_reaches_docker_with_the_verb_once_and_the_pinned_image(self):
        host = FakeHost()
        execute(host)
        self.assertEqual(host.run_args.count("run"), 1)
        self.assertEqual(host.run_args[0], "run")
        self.assertEqual(host.run_image, DIGEST)
        self.assertEqual(host.run_command, [])

    def test_the_fake_rejects_a_duplicated_run_verb_like_real_docker(self):
        host = FakeHost()
        code, _ = host.docker("run", "run", "--detach", "--name", remote.CONTAINER, DIGEST, check=False)
        self.assertEqual(code, 1)
        self.assertEqual(host.last_stderr, "Unable to find image 'run:latest' locally")
        self.assertEqual(host.run_command, ["--detach", "--name", remote.CONTAINER, DIGEST])

    def test_the_fake_rejects_unknown_options(self):
        host = FakeHost()
        with self.assertRaises(AssertionError):
            host.docker("run", "--surprise", DIGEST, check=False)

    def test_every_option_value_pair_is_understood(self):
        args = remote.run_args(DIGEST, "10.20.10.150", ["vol:/data", "/a:/b:ro"])
        options, image, command = parse_docker_run(args)
        self.assertEqual((image, command), (DIGEST, []))
        names = [option for option, _ in options]
        for required in ("--detach", "--name", "--memory", "--memory-swap", "--publish", "-e", "-v"):
            self.assertIn(required, names)


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
