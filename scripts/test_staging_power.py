import importlib.util
import pathlib
import unittest

SPEC = importlib.util.spec_from_file_location("staging_power", pathlib.Path(__file__).with_name("staging-power.py"))
power = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(power)

PREFIX = power.SERVICE_PREFIX


class FakeAws:
    """AWS를 흉내 낸다. 변경 호출은 즉시 반영되고 호출 순서를 기록한다."""

    def __init__(self, account="721744297924", db=("available", "available"), hosts=0, suspended=True,
                 services=None, protected=True, stopping_polls=None, ai_db=False, origin_rule=True,
                 ai_host=False, ai_service=False, ai_hosts_on=0):
        self.account = account
        self.origin_rule = origin_rule
        self.ai_host = ai_host
        self.ai_desired = ai_hosts_on
        self.ai_instances = [f"ai-{n:04d}" for n in range(ai_hosts_on)]
        self.calls = []
        self.dbs = {"seonjalal-v2-staging": db[0]}
        if ai_db:
            self.dbs["seonjalal-v2-staging-ai-postgres"] = db[1]
        self.stopping_polls = stopping_polls
        self.suspended = list(power.DORMANT_PROCESSES) if suspended else []
        self.min = self.desired = hosts
        self.max = 4
        self.protected = protected
        self.instances = [f"i-{n:04d}" for n in range(hosts)]
        base = services or {}
        self.services = {}
        for name in ("main-backend", "payment-server", "main-backend-b", "payment-server-b", "backend"):
            desired = base.get(name, 0)
            self.services[PREFIX + name] = {"desired": desired, "running": desired}
        if ai_service:
            self.services[power.AI_SERVICE] = {"desired": 1 if ai_hosts_on else 0, "running": 1 if ai_hosts_on else 0}

    def mutating(self):
        return [c for c in self.calls if c[1] in power.MUTATING]

    def __call__(self, *args):
        self.calls.append((args[0], args[1], args))
        group, op = args[0], args[1]
        if (group, op) == ("sts", "get-caller-identity"):
            return {"Account": self.account}
        if (group, op) == ("rds", "describe-db-instances"):
            name = args[args.index("--db-instance-identifier") + 1]
            if name not in self.dbs:
                raise RuntimeError("DBInstanceNotFound")
            # stopping_polls가 정수일 때만 그 횟수만큼 조회한 뒤 stopped로 바뀐다. None이면 stopping을 유지한다.
            if self.dbs[name] == "stopping" and self.stopping_polls is not None:
                if self.stopping_polls > 0:
                    self.stopping_polls -= 1
                else:
                    self.dbs[name] = "stopped"
            return {"DBInstances": [{"DBInstanceStatus": self.dbs[name]}]}
        if (group, op) == ("rds", "start-db-instance"):
            self.dbs[args[args.index("--db-instance-identifier") + 1]] = "available"
            return {}
        if (group, op) == ("rds", "stop-db-instance"):
            self.dbs[args[args.index("--db-instance-identifier") + 1]] = "stopping"
            return {}
        if (group, op) == ("autoscaling", "describe-auto-scaling-groups"):
            if args[args.index("--auto-scaling-group-names") + 1] == power.AI_ASG:
                if not self.ai_host:
                    return {"AutoScalingGroups": []}
                return {"AutoScalingGroups": [{
                    "MinSize": 0, "DesiredCapacity": self.ai_desired, "MaxSize": 1, "SuspendedProcesses": [],
                    "Instances": [{"InstanceId": i, "LifecycleState": "InService"} for i in self.ai_instances]}]}
            return {"AutoScalingGroups": [{
                "MinSize": self.min, "DesiredCapacity": self.desired, "MaxSize": self.max,
                "SuspendedProcesses": [{"ProcessName": p} for p in self.suspended],
                "Instances": [{"InstanceId": i, "LifecycleState": "InService"} for i in self.instances]}]}
        if (group, op) == ("autoscaling", "resume-processes"):
            names = args[args.index("--scaling-processes") + 1:]
            self.suspended = [p for p in self.suspended if p not in names]
            return {}
        if (group, op) == ("autoscaling", "suspend-processes"):
            self.suspended = sorted(set(self.suspended) | set(power.DORMANT_PROCESSES))
            return {}
        if (group, op) == ("autoscaling", "set-instance-protection"):
            self.protected = False
            return {}
        if (group, op) == ("autoscaling", "update-auto-scaling-group") and args[args.index("--auto-scaling-group-name") + 1] == power.AI_ASG:
            self.ai_desired = int(args[args.index("--desired-capacity") + 1])
            self.ai_instances = [f"ai-{n:04d}" for n in range(self.ai_desired)]
            return {}
        if (group, op) == ("autoscaling", "update-auto-scaling-group"):
            self.min = int(args[args.index("--min-size") + 1])
            self.desired = int(args[args.index("--desired-capacity") + 1])
            if self.desired == 0:
                self.instances = list(self.instances) if self.protected else []
            else:
                self.instances = [f"i-{n:04d}" for n in range(self.desired)]
            return {}
        if (group, op) == ("ecs", "list-container-instances"):
            return {"containerInstanceArns": [f"arn:{i}" for i in self.instances + self.ai_instances]}
        if (group, op) == ("ecs", "list-services"):
            return {"serviceArns": ["arn:" + n for n in self.services]}
        if (group, op) == ("ecs", "describe-services"):
            names = [a[4:] for a in args[args.index("--services") + 1:] if a.startswith("arn:")]
            return {"services": [{
                "serviceName": n, "desiredCount": self.services[n]["desired"], "runningCount": self.services[n]["running"],
                "deployments": [{}], "loadBalancers": [{"targetGroupArn": "tg"}] if "main-backend" in n else []} for n in names]}
        if (group, op) == ("ecs", "update-service"):
            name = args[args.index("--service") + 1]
            count = int(args[args.index("--desired-count") + 1])
            self.services[name] = {"desired": count, "running": count}
            return {}
        if (group, op) == ("elbv2", "describe-target-health"):
            running = self.services[PREFIX + "main-backend-b"]["running"] + self.services[PREFIX + "main-backend"]["running"]
            return {"TargetHealthDescriptions": [{"TargetHealth": {"State": "healthy"}} for _ in range(running)]}
        if (group, op) == ("elbv2", "describe-load-balancers"):
            return {"LoadBalancers": [{"LoadBalancerArn": "lb-arn"}]}
        if (group, op) == ("elbv2", "describe-listeners"):
            return {"Listeners": [{"Port": 8443, "ListenerArn": "l8443"}, {"Port": 443, "ListenerArn": "l443"}]}
        if (group, op) == ("elbv2", "describe-rules"):
            host = [{"Conditions": [{"HostHeaderConfig": {"Values": [power.ORIGIN_HOST]}}]}] if self.origin_rule else []
            return {"Rules": host + [{"Conditions": []}]}
        raise AssertionError(f"예상하지 못한 호출: {args}")


def run(argv, api, sleep=lambda _: None):
    import io
    import contextlib
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        power.main(argv, api=api, sleep=sleep)
    return out.getvalue()


def order(api, *ops):
    names = [c[1] for c in api.calls]
    positions = [names.index(op) for op in ops]
    return positions == sorted(positions)


class StagingPowerTest(unittest.TestCase):
    def test_status_changes_nothing_and_warns_about_db_only(self):
        api = FakeAws()
        output = run(["status"], api)
        self.assertEqual(api.mutating(), [])
        self.assertIn("DB만 켜져 있다", output)

    def test_status_warns_when_the_alb_origin_rule_is_missing(self):
        output = run(["status"], FakeAws(origin_rule=False))
        self.assertIn("규칙이 없다", output)
        self.assertNotIn("규칙이 없다", run(["status"], FakeAws(origin_rule=True)))

    def test_dry_run_prints_plan_without_changing_anything(self):
        for command in ("on", "off"):
            api = FakeAws(db=("stopped", "stopped"))
            output = run([command], api)
            self.assertEqual(api.mutating(), [], command)
            self.assertIn("계획만 출력했다", output)

    def test_on_starts_db_then_hosts_then_green_services(self):
        api = FakeAws(db=("stopped", "stopped"))
        run(["on", "--execute"], api)
        self.assertTrue(order(api, "start-db-instance", "resume-processes", "update-auto-scaling-group", "update-service"))
        self.assertEqual(api.dbs["seonjalal-v2-staging"], "available")
        self.assertEqual(api.suspended, [])
        self.assertEqual((api.min, api.desired), (2, 2))
        self.assertEqual(api.services[PREFIX + "main-backend-b"]["desired"], 2)
        self.assertEqual(api.services[PREFIX + "payment-server-b"]["desired"], 2)

    def test_on_never_touches_blue_slot_or_v1_backend(self):
        api = FakeAws(db=("stopped", "stopped"))
        run(["on", "--execute"], api)
        for name in ("main-backend", "payment-server", "backend"):
            self.assertEqual(api.services[PREFIX + name]["desired"], 0, name)

    def test_on_blue_slot_option(self):
        api = FakeAws(db=("stopped", "stopped"))
        run(["on", "--execute", "--slot", "blue"], api)
        self.assertEqual(api.services[PREFIX + "main-backend"]["desired"], 2)
        self.assertEqual(api.services[PREFIX + "main-backend-b"]["desired"], 0)

    def test_on_waits_for_a_stopping_db_before_starting_it(self):
        api = FakeAws(db=("stopping", "stopping"), stopping_polls=2)
        run(["on", "--execute"], api)
        describes = [c for c in api.calls if c[1] == "describe-db-instances"]
        self.assertGreater(len(describes), 3)
        self.assertEqual(api.dbs["seonjalal-v2-staging"], "available")

    def test_on_skip_db_does_not_start_or_wait_for_db(self):
        api = FakeAws(db=("stopped", "stopped"))
        run(["on", "--execute", "--skip-db"], api)
        self.assertEqual([c for c in api.calls if c[1] == "start-db-instance"], [])
        self.assertEqual(api.dbs["seonjalal-v2-staging"], "stopped")

    def test_on_includes_ai_database_when_it_exists(self):
        api = FakeAws(db=("stopped", "stopped"), ai_db=True)
        run(["on", "--execute"], api)
        self.assertEqual(api.dbs["seonjalal-v2-staging-ai-postgres"], "available")

    def test_off_scales_services_before_hosts_and_stops_db_last(self):
        api = FakeAws(hosts=2, suspended=False, services={"main-backend-b": 2, "payment-server-b": 2})
        run(["off", "--execute"], api)
        self.assertTrue(order(api, "update-service", "set-instance-protection", "update-auto-scaling-group",
                              "suspend-processes", "stop-db-instance"))
        self.assertEqual(api.instances, [])
        self.assertEqual((api.min, api.desired), (0, 0))
        self.assertEqual(sorted(api.suspended), sorted(power.DORMANT_PROCESSES))
        self.assertEqual(api.dbs["seonjalal-v2-staging"], "stopping")
        self.assertTrue(all(s["desired"] == 0 for s in api.services.values()))

    def test_off_releases_scale_in_protection_so_instances_terminate(self):
        api = FakeAws(hosts=2, suspended=False, protected=True, services={"main-backend-b": 2})
        run(["off", "--execute"], api)
        self.assertFalse(api.protected)
        self.assertEqual(api.instances, [])

    def test_off_when_already_off_only_stops_the_database(self):
        api = FakeAws(hosts=0, suspended=True)
        run(["off", "--execute"], api)
        self.assertEqual([c[1] for c in api.mutating()], ["stop-db-instance"])

    def test_off_skip_db_leaves_the_database_running(self):
        api = FakeAws(hosts=0, suspended=True)
        run(["off", "--execute", "--skip-db"], api)
        self.assertEqual(api.dbs["seonjalal-v2-staging"], "available")

    def test_wrong_account_stops_before_any_change(self):
        api = FakeAws(account="111111111111")
        with self.assertRaises(SystemExit):
            run(["off", "--execute"], api)
        self.assertEqual(api.mutating(), [])

    def test_rejects_invalid_capacity_arguments(self):
        for bad in (["on", "--hosts", "1"], ["on", "--hosts", "9"], ["on", "--tasks", "1"], ["on", "--hosts", "2", "--tasks", "5"]):
            with self.assertRaises(SystemExit, msg=str(bad)):
                run(bad, FakeAws())

    def test_mutations_never_reference_production_or_unknown_names(self):
        api = FakeAws(db=("stopped", "stopped"))
        run(["on", "--execute"], api)
        run(["off", "--execute"], api)
        for call in api.mutating():
            joined = " ".join(call[2])
            self.assertNotIn("production", joined)
            if call[1] == "update-service":
                self.assertIn(PREFIX, joined)
                self.assertIn(power.CLUSTER, joined)

    def test_allowlist_names_are_staging_only(self):
        self.assertTrue(power.CLUSTER.startswith("seonjalal-v2-staging"))
        self.assertTrue(power.ASG.startswith("seonjalal-v2-staging"))
        self.assertTrue(all(n.startswith("seonjalal-v2-staging") for n in power.DATABASES))
        self.assertTrue(power.AI_ASG.startswith("seonjalal-v2-staging"))
        self.assertTrue(power.AI_SERVICE.startswith("seonjalal-v2-staging"))

    def test_dormant_process_list_matches_terraform(self):
        text = (pathlib.Path(__file__).resolve().parent.parent
                / "infrastructure/terraform/environments/staging/rehearsal/compute.tf").read_text(encoding="utf-8")
        for process in power.DORMANT_PROCESSES:
            self.assertIn(f'"{process}"', text)
        listed = text.split("dormant_asg_suspended_processes = [")[1].split("]")[0]
        self.assertEqual(listed.count('"') // 2, len(power.DORMANT_PROCESSES))

    def test_on_without_ai_resources_never_touches_the_ai_host(self):
        api = FakeAws(db=("stopped", "stopped"))
        output = run(["on", "--execute"], api)
        self.assertEqual([c for c in api.mutating() if power.AI_ASG in c[2]], [])
        self.assertNotIn("AI 호스트", output.split("-- ON 계획")[1])

    def test_on_starts_the_ai_host_in_parallel_and_the_ai_service_after_hosts_are_ready(self):
        api = FakeAws(db=("stopped", "stopped"), ai_host=True, ai_service=True)
        run(["on", "--execute"], api)
        self.assertEqual(api.ai_desired, 1)
        self.assertEqual(api.services[power.AI_SERVICE]["desired"], 1)
        mutations = [(c[1], power.AI_ASG in c[2], power.AI_SERVICE in c[2]) for c in api.mutating()]
        ai_host_at = mutations.index(("update-auto-scaling-group", True, False))
        be_host_at = next(i for i, m in enumerate(mutations) if m[0] == "update-auto-scaling-group" and not m[1])
        first_service_at = next(i for i, m in enumerate(mutations) if m[0] == "update-service")
        ai_service_at = mutations.index(("update-service", False, True))
        self.assertLess(be_host_at, ai_host_at)
        self.assertLess(ai_host_at, first_service_at)
        self.assertLess(first_service_at, ai_service_at)

    def test_on_with_ai_host_but_without_ai_service_only_starts_the_host(self):
        api = FakeAws(db=("stopped", "stopped"), ai_host=True, ai_service=False)
        output = run(["on", "--execute"], api)
        self.assertEqual(api.ai_desired, 1)
        self.assertIn("AI 서비스가 아직 없어", output)

    def test_on_no_ai_skips_the_ai_host_and_service(self):
        api = FakeAws(db=("stopped", "stopped"), ai_host=True, ai_service=True)
        run(["on", "--execute", "--no-ai"], api)
        self.assertEqual((api.ai_desired, api.services[power.AI_SERVICE]["desired"]), (0, 0))

    def test_off_turns_the_ai_service_and_host_off_too(self):
        api = FakeAws(hosts=2, suspended=False, services={"main-backend-b": 2}, ai_host=True, ai_service=True, ai_hosts_on=1)
        run(["off", "--execute"], api)
        self.assertEqual(api.services[power.AI_SERVICE]["desired"], 0)
        self.assertEqual((api.ai_desired, api.ai_instances), (0, []))
        names = [(c[1], power.AI_ASG in c[2]) for c in api.mutating()]
        self.assertLess(names.index(("update-service", False)), names.index(("update-auto-scaling-group", True)))
        self.assertLess(names.index(("update-auto-scaling-group", True)), names.index(("stop-db-instance", False)))

    def test_off_ignores_the_ai_host_when_it_does_not_exist_or_is_already_off(self):
        for api in (FakeAws(hosts=0, suspended=True), FakeAws(hosts=0, suspended=True, ai_host=True, ai_service=True)):
            run(["off", "--execute"], api)
            self.assertEqual([c for c in api.mutating() if power.AI_ASG in c[2]], [])

    def test_status_warns_when_only_the_ai_host_is_running(self):
        api = FakeAws(hosts=0, suspended=True, ai_host=True, ai_service=True, ai_hosts_on=1)
        api.services[power.AI_SERVICE] = {"desired": 0, "running": 0}
        self.assertIn("AI 호스트만 켜져 있다", run(["status"], api))

    def test_plan_lists_ai_steps_only_when_the_ai_host_exists(self):
        with_ai = run(["on"], FakeAws(db=("stopped", "stopped"), ai_host=True, ai_service=True))
        without_ai = run(["on"], FakeAws(db=("stopped", "stopped")))
        self.assertIn("AI 호스트 ASG: desired 1", with_ai)
        self.assertNotIn("AI 호스트 ASG: desired 1", without_ai)

    def test_wait_gives_up_instead_of_hanging(self):
        class Stuck(FakeAws):
            def __call__(self, *args):
                result = super().__call__(*args)
                if (args[0], args[1]) == ("ecs", "describe-services"):
                    for s in result["services"]:
                        s["runningCount"] = 0
                return result
        api = Stuck(db=("available", "available"))
        with self.assertRaises(SystemExit):
            run(["on", "--execute", "--skip-db"], api)


if __name__ == "__main__":
    unittest.main()
