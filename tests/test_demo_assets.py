"""The README demo animation must stay reproducible.

``docs/assets/workflow-associate-crop.gif`` is committed so GitHub can render it, which
means it can silently rot: rename a node, change the association rule, or move a box, and
the committed GIF keeps advertising behaviour the code no longer has.

``tools/make_demo_gifs.py`` is deterministic and records a digest of everything that
determines its output in ``docs/assets/demo.json``. These tests recompute that digest and
fail when the committed animation is out of date.

Pillow is only needed for the deeper checks; the digest checks work without it.
"""

from __future__ import annotations

import importlib.util
import json
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
TOOL_PATH = REPO_ROOT / "tools" / "make_demo_gifs.py"
GIF_PATH = REPO_ROOT / "docs" / "assets" / "workflow-associate-crop.gif"
MANIFEST_PATH = REPO_ROOT / "docs" / "assets" / "demo.json"

#: Keep the README asset in a range GitHub serves comfortably.
MAX_GIF_BYTES = 3 * 1024 * 1024


def _load_tool():
    """Import the generator without executing its main()."""
    spec = importlib.util.spec_from_file_location("make_demo_gifs", TOOL_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class DemoAssetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
        # Importing the generator runs its layout validation, so load it once.
        cls.tool = _load_tool()

    def test_gif_and_manifest_exist(self):
        self.assertTrue(GIF_PATH.is_file(), f"missing {GIF_PATH.relative_to(REPO_ROOT)}")
        self.assertTrue(MANIFEST_PATH.is_file(), f"missing {MANIFEST_PATH.relative_to(REPO_ROOT)}")

    def test_readme_references_the_gif(self):
        for name in ("README.md", "README_zh-CN.md"):
            text = (REPO_ROOT / name).read_text(encoding="utf-8")
            self.assertIn(
                "docs/assets/workflow-associate-crop.gif",
                text,
                f"{name} no longer embeds the demo animation",
            )

    def test_gif_is_not_absurdly_large(self):
        size = GIF_PATH.stat().st_size
        self.assertLess(
            size,
            MAX_GIF_BYTES,
            f"demo GIF is {size / 1024 / 1024:.1f} MB; regenerate with fewer colours or frames",
        )

    def test_manifest_matches_the_committed_file(self):
        entry = self.manifest["assets"][0]
        self.assertEqual("workflow-associate-crop.gif", entry["file"])
        self.assertEqual(
            GIF_PATH.stat().st_size,
            entry["bytes"],
            "the committed GIF does not match demo.json; rerun tools/make_demo_gifs.py",
        )

    def test_source_digest_is_current(self):
        self.assertEqual(
            self.tool.source_digest(),
            self.manifest["digest"],
            "the demo generator's inputs changed, so the committed GIF is stale. "
            "Rerun: python tools/make_demo_gifs.py",
        )

    def test_recorded_association_still_holds(self):
        # Ties the animation's claim ("two matched, one rejected") to the current node.
        entry = self.manifest["assets"][0]
        self.assertEqual(self.tool.PAIRS, [tuple(pair) for pair in entry["association_pairs"]])
        self.assertEqual(self.tool.MATCHED_SUBJECTS, entry["matched_subjects"])
        self.assertEqual(self.tool.REJECTED_CANDIDATES, entry["rejected_candidates"])
        self.assertTrue(self.tool.REJECTED_CANDIDATES, "the demo must show a rejected marker")
        self.assertLess(
            len(self.tool.MATCHED_SUBJECTS),
            len(self.tool.SUBJECTS),
            "the demo must skip a container",
        )

    def test_gif_is_animated_and_readable(self):
        try:
            from PIL import Image
        except ImportError:  # pragma: no cover - Pillow ships in the dev extra
            self.skipTest("Pillow is not installed")

        entry = self.manifest["assets"][0]
        with Image.open(GIF_PATH) as image:
            self.assertGreater(getattr(image, "n_frames", 1), 1, "the demo must animate")
            self.assertTrue(image.is_animated)
            self.assertEqual(image.width, entry["width"])
            self.assertEqual(image.height, entry["height"])
            self.assertGreaterEqual(image.width, 800, "the animation must stay readable in the README")


if __name__ == "__main__":
    unittest.main()
