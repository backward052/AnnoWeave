"""Runtime data locations must honour ``ANNOWEAVE_CONFIG_DIR`` everywhere.

The `.env.example` file and both READMEs document that ``ANNOWEAVE_CONFIG_DIR``
relocates local AnnoWeave state. Before this test existed only two of the six
resolvers honoured it, so the documentation was wrong about the other four.

Every consumer resolves its path through :mod:`annoweave.paths` at call time, so
reloading that single module is enough to pick up a changed environment variable.
Nothing else is reloaded, which keeps module-level classes identical to the ones
the rest of the test suite compares against.
"""

from __future__ import annotations

import importlib
import os
import unittest
from pathlib import Path

ALLOWED_EXTRAS = 1


class ConfigDirectoryTests(unittest.TestCase):
    def setUp(self):
        # Remember and restore the variable without leaking state into other tests.
        self._previous = os.environ.get("ANNOWEAVE_CONFIG_DIR")
        self.addCleanup(self._restore_env)

    def _restore_env(self):
        if self._previous is None:
            os.environ.pop("ANNOWEAVE_CONFIG_DIR", None)
        else:
            os.environ["ANNOWEAVE_CONFIG_DIR"] = self._previous

    @staticmethod
    def _reload_paths():
        import annoweave.paths as paths

        return importlib.reload(paths)

    def _override(self, value: str | None):
        if value is None:
            os.environ.pop("ANNOWEAVE_CONFIG_DIR", None)
        else:
            os.environ["ANNOWEAVE_CONFIG_DIR"] = value
        return self._reload_paths()

    def test_override_relocates_every_data_path(self):
        custom = Path("C:/AnnoWeaveTestData").resolve()
        paths = self._override(str(custom))

        from annoweave.inference.config import inference_config_path
        from annoweave.plugins import default_plugin_root
        from annoweave.ui.project_context import app_data_root, project_database_path
        from annoweave.workflow.workflow import workflows_catalog_path

        self.assertEqual(custom, paths.app_root())
        for label, path in (
            ("inference_config_path", inference_config_path()),
            ("workflows_catalog_path", workflows_catalog_path()),
            ("default_plugin_root", default_plugin_root()),
            ("app_data_root", app_data_root()),
            ("project_database_path", project_database_path("D:/media/sample")),
        ):
            self.assertEqual(
                custom,
                Path(*Path(path).parts[: len(custom.parts)]),
                f"{label}() escaped the ANNOWEAVE_CONFIG_DIR override: {path}",
            )

    def test_project_database_is_distinct_per_source_root(self):
        paths = self._override(str(Path("C:/AnnoWeaveTestData").resolve()))
        self.assertTrue(paths.app_root())
        from annoweave.ui.project_context import project_database_path

        self.assertNotEqual(
            project_database_path("D:/media/a"),
            project_database_path("D:/media/b"),
        )

    def test_default_root_uses_local_appdata_when_override_is_absent(self):
        paths = self._override(None)
        base = os.environ.get("LOCALAPPDATA")
        if base:
            self.assertEqual(Path(base) / "AnnoWeave", paths.app_root())
        else:
            self.assertTrue(paths.app_root().is_absolute())

    def test_blank_override_is_ignored(self):
        paths = self._override("   ")
        self.assertTrue(paths.app_root().is_absolute())
        self.assertNotEqual("   ", str(paths.app_root()))

    def test_session_and_cache_paths_inherit_the_override(self):
        # These two derive from inference_config_path().parent, so they follow the
        # override automatically; this guards that inheritance.
        custom = Path("C:/AnnoWeaveTestData").resolve()
        self._override(str(custom))
        from annoweave.inference.config import inference_config_path
        from annoweave.services.precompute_cache import _path as cache_path
        from annoweave.services.review_session import session_path

        self.assertEqual(custom, Path(inference_config_path().parent))
        for path in (session_path("D:/media/a", 0), cache_path("media", 0, "workflow")):
            self.assertEqual(
                custom,
                Path(*Path(path).parts[: len(custom.parts)]),
                f"{path} escaped the ANNOWEAVE_CONFIG_DIR override",
            )


if __name__ == "__main__":
    unittest.main()
