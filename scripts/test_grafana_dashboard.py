"""Grafana CloudWatch 대시보드 JSON의 쿼리 모양을 검사한다. AWS/Grafana 호출 없음.

대시보드 JSON으로 가져온 CloudWatch 쿼리는 편집기를 열어 기본값이 채워지는 일이 없으므로
쿼리 방식(metricQueryType)과 편집 모드(metricEditorMode)가 JSON에 직접 있어야 실행된다.
"""
import json
from pathlib import Path
import re
import unittest

PATH = Path(__file__).resolve().parent.parent / "operations" / "grafana" / "dashboards" / "cloudwatch-load-test.json"
DASHBOARD = json.loads(PATH.read_text(encoding="utf-8"))
TARGETS = [(panel["title"], target) for panel in DASHBOARD["panels"] for target in panel["targets"]]
VARIABLES = {item["name"] for item in DASHBOARD["templating"]["list"]}


class DashboardQueryTests(unittest.TestCase):
    def test_every_query_declares_search_builder_mode(self):
        for title, target in TARGETS:
            self.assertEqual(target.get("metricQueryType"), 0, title)
            self.assertEqual(target.get("metricEditorMode"), 0, title)

    def test_every_query_has_the_fields_the_builder_needs(self):
        for title, target in TARGETS:
            for field in ("region", "namespace", "metricName", "statistic", "period", "dimensions"):
                self.assertTrue(target.get(field), f"{title}: {field}")
            self.assertEqual(target["queryMode"], "Metrics", title)
            self.assertTrue(target["matchExact"], title)

    def test_every_variable_used_in_a_query_is_defined(self):
        used = set()
        for _, target in TARGETS:
            used |= set(re.findall(r"\$\{(\w+)\}", json.dumps(target)))
        self.assertLessEqual(used, VARIABLES)

    def test_every_query_uses_the_datasource_variable(self):
        for title, target in TARGETS:
            self.assertEqual(target["datasource"]["uid"], "${ds}", title)

    def test_refids_are_unique_per_panel(self):
        for panel in DASHBOARD["panels"]:
            refs = [target["refId"] for target in panel["targets"]]
            self.assertEqual(len(refs), len(set(refs)), panel["title"])

    def test_dashboard_has_a_stable_uid_and_a_bumped_version(self):
        self.assertEqual(DASHBOARD["uid"], "seonjalal-cw-loadtest")
        self.assertGreaterEqual(DASHBOARD["version"], 4)

    def test_legends_use_the_cloudwatch_dynamic_label_syntax(self):
        for title, target in TARGETS:
            self.assertNotIn("{{", target["label"], title)
        labels = {title: target["label"] for title, target in TARGETS}
        self.assertEqual(labels["ECS 서비스 CPU"], "${PROP('Dim.ServiceName')}")
        self.assertEqual(labels["ECS 서비스 메모리"], "${PROP('Dim.ServiceName')}")
        self.assertEqual(labels["호스트(ASG) CPU"], "${PROP('Dim.AutoScalingGroupName')}")

    def test_stale_resources_are_filtered_out_of_the_selectors(self):
        variables = {item["name"]: item for item in DASHBOARD["templating"]["list"]}
        for name in ("cluster", "rds", "asg"):
            pattern = variables[name]["regex"].strip("/")
            self.assertFalse(re.search(pattern, "seonjalal-v2-migration-rehearsal"), name)
            self.assertFalse(re.search(pattern, "seonjalal-v2-develop-ecs-application"), name)
            self.assertTrue(re.search(pattern, "seonjalal-v2-staging"), name)
            self.assertTrue(re.search(pattern, "seonjalal-v2-production-cluster"), name)

    def test_filter_regexes_have_no_capture_group(self):
        """Grafana는 정규식에 캡처 그룹이 있으면 그 그룹을 선택 항목의 값으로 쓴다.

        '이 단어가 없는 것만 통과'하는 규칙은 그룹이 아무것도 잡지 못해 값이 undefined가 된다.
        """
        for item in DASHBOARD["templating"]["list"]:
            if item.get("regex"):
                pattern = item["regex"].strip("/")
                self.assertEqual(re.compile(pattern).groups, 0, item["name"])
                for name in ("seonjalal-v2-staging", "seonjalal-v2-production-cluster"):
                    match = re.search(pattern, name)
                    self.assertIsNotNone(match, name)
                    self.assertEqual(match.group(0), name)

    def test_selectors_without_a_filter_are_the_ones_that_need_none(self):
        variables = {item["name"]: item for item in DASHBOARD["templating"]["list"]}
        for name in ("alb", "service"):
            self.assertFalse(variables[name].get("regex"), name)


if __name__ == "__main__":
    unittest.main()
