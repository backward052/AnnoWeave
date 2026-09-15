"""The shipped examples must stay importable and schema-valid.

`examples/workflows/associate-crop-infer.json` is advertised in the README as the
thing to import first. If a node type or parameter is renamed without updating the
example, the advertised onboarding path breaks silently, so pin it here.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from annoweave.workflow.node import NODE_REGISTRY
from annoweave.workflow.validation import error_messages, validate_workflow
from annoweave.workflow.workflow import WorkflowConfig, load_workflow

REPO_ROOT = Path(__file__).resolve().parents[1]
EXAMPLE_WORKFLOW = REPO_ROOT / "examples" / "workflows" / "associate-crop-infer.json"
EXAMPLE_PLUGIN = REPO_ROOT / "examples" / "plugins" / "gaussian_blur"


class ExampleWorkflowTests(unittest.TestCase):
    def test_example_workflow_exists(self):
        self.assertTrue(EXAMPLE_WORKFLOW.is_file(), f"missing {EXAMPLE_WORKFLOW}")

    def test_example_workflow_loads(self):
        config = load_workflow(EXAMPLE_WORKFLOW)
        self.assertIsInstance(config, WorkflowConfig)
        self.assertTrue(config.name)
        self.assertGreaterEqual(len(config.nodes), 2)

    def test_example_workflow_uses_registered_node_types(self):
        config = load_workflow(EXAMPLE_WORKFLOW)
        unknown = [node.type for node in config.nodes if node.type not in NODE_REGISTRY]
        self.assertEqual([], unknown, f"example references unregistered node types: {unknown}")

    def test_example_workflow_passes_preflight_validation(self):
        config = load_workflow(EXAMPLE_WORKFLOW)
        self.assertEqual([], error_messages(validate_workflow(config)))

    def test_example_workflow_declares_every_parameter_it_sets(self):
        # Parameters the node schema does not know about are silently ignored at
        # runtime, which makes an example look wired-up while doing nothing.
        config = load_workflow(EXAMPLE_WORKFLOW)
        unknown: list[str] = []
        for node in config.nodes:
            schema = getattr(NODE_REGISTRY[node.type], "Meta", {}).get("param_schema", {})
            for key in node.params:
                if key not in schema:
                    unknown.append(f"{node.type}.{key}")
        self.assertEqual([], unknown, f"example sets parameters no node accepts: {unknown}")

    def test_example_workflow_contains_no_local_or_absolute_paths(self):
        text = EXAMPLE_WORKFLOW.read_text(encoding="utf-8")
        self.assertNotIn("model_path", text)
        for token in (":\\", "C:/", "D:/", "/home/", "/Users/"):
            self.assertNotIn(token, text, f"example leaks a local path ({token})")


class ExamplePluginTests(unittest.TestCase):
    def test_example_plugin_is_self_contained_and_healthy(self):
        manifest_path = EXAMPLE_PLUGIN / "plugin.json"
        self.assertTrue(manifest_path.is_file(), f"missing {manifest_path}")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertIn("name", manifest)
        self.assertTrue((EXAMPLE_PLUGIN / manifest.get("entry", "plugin.py")).is_file())

    def test_example_plugin_registers_a_node(self):
        import importlib.util

        spec = importlib.util.spec_from_file_location("example_gaussian_blur", EXAMPLE_PLUGIN / "plugin.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        self.assertTrue(callable(module.healthcheck))
        self.assertIsInstance(module.healthcheck(), str)

        node_class = module.build_node_class()
        self.assertIn("type_name", node_class.Meta)
        self.assertEqual("plugin_gaussian_blur", node_class.Meta["type_name"])


if __name__ == "__main__":
    unittest.main()
