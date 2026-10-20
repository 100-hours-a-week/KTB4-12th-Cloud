#!/usr/bin/env python3
"""시험 노드 삭제 범위의 로컬 검사. AWS 실행 없음."""
import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location("remove_nodes", Path(__file__).with_name("remove-rehearsal-nodes.py"))
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class Tests(unittest.TestCase):
    def row(self):
        return {"Instances": [{"InstanceId": n} for n in module.IDS], "MinSize":2, "DesiredCapacity":2, "SuspendedProcesses":[]}

    def test_exact_test_nodes_only(self):
        module.validate_group(self.row())
        self.assertNotIn(module.PRODUCTION, module.IDS)
        self.assertNotIn("i-0c6f5ea09b5e20859", module.IDS)

    def test_extra_node_blocks(self):
        row = self.row()
        row["Instances"].append({"InstanceId": module.PRODUCTION})
        with self.assertRaises(RuntimeError): module.validate_group(row)

    def test_suspended_termination_blocks(self):
        row = self.row()
        row["SuspendedProcesses"] = [{"ProcessName":"Terminate"}]
        with self.assertRaises(RuntimeError): module.validate_group(row)


if __name__ == "__main__": unittest.main()
