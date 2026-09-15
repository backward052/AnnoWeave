import unittest

import numpy as np

from annoweave.inference.datatypes import Detection, Frame, ModelResult, TaskType
from annoweave.workflow.association import GenericAssociateNode
from annoweave.workflow.packet import Packet
from annoweave.workflow.templates import TEMPLATES, starter_workflow_config


class PublicWorkflowTests(unittest.TestCase):
    def test_public_templates_have_no_model_bindings(self):
        self.assertTrue(TEMPLATES)
        for template in TEMPLATES:
            self.assertTrue(all("model_path" not in str(value) for value in template.defaults.values()))
        starter = starter_workflow_config()
        self.assertEqual([node.type for node in starter.nodes], ["source_video", "draw"])

    def test_generic_association_marks_contained_candidate(self):
        subject = Detection(10, 10, 90, 90, "container", 0.9)
        candidate = Detection(30, 30, 40, 40, "marker", 0.8)
        packet = Packet(
            frame=Frame(0, 0.0, np.zeros((100, 100, 3), dtype=np.uint8)),
            full_results={
                "subject": ModelResult(TaskType.DETECTION, [subject]),
                "candidate": ModelResult(TaskType.DETECTION, [candidate]),
            },
        )
        result = GenericAssociateNode({
            "left_key": "subject", "right_key": "candidate",
            "metric": "center_inside", "threshold": 1.0,
        }).run(packet)
        self.assertEqual(len(result.associations), 1)
        self.assertTrue(result.associations[0].should_crop)
        self.assertEqual(result.associations[0].matched_candidates, [candidate])


if __name__ == "__main__":
    unittest.main()
