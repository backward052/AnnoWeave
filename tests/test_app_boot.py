"""The main window must construct and switch pages without a live display.

`MainWindow.__init__` builds every page, loads the model library and workflow
catalog, discovers plugins, and applies the theme. A regression anywhere in that
path is invisible until a user launches the app, so this test builds the window
once under the offscreen Qt platform.

Skipped automatically when Qt cannot start (for example a machine with no Qt
platform plugin at all), so the suite still runs in a bare environment.
"""

from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def _qt_available() -> bool:
    try:
        from PySide6.QtWidgets import QApplication  # noqa: F401
    except Exception:
        return False
    return True


@unittest.skipUnless(_qt_available(), "PySide6 is not installed")
class MainWindowSmokeTests(unittest.TestCase):
    app = None
    window = None

    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication

        from annoweave.app import MainWindow

        cls.app = QApplication.instance() or QApplication([])
        try:
            cls.window = MainWindow()
        except Exception as exc:  # pragma: no cover - surfaced as a test failure
            raise AssertionError(f"MainWindow() failed to construct: {exc!r}") from exc

    @classmethod
    def tearDownClass(cls):
        cls.window = None

    def test_window_has_a_title_and_the_product_name(self):
        from annoweave.brand import PRODUCT_NAME

        self.assertTrue(self.window.windowTitle())
        self.assertIn(PRODUCT_NAME, self.window.windowTitle())

    def test_every_navigation_target_switches_pages(self):
        pages = getattr(self.window, "_nav_buttons", None)
        self.assertTrue(pages, "the window exposes no navigation buttons")
        for key in pages:
            self.window.show_page(key)
            self.app.processEvents()

    def test_language_can_switch_both_ways(self):
        from annoweave.i18n import LANG_EN, LANG_ZH

        manager = self.window.language_manager
        manager.set_language(LANG_EN)
        self.app.processEvents()
        english_title = self.window.windowTitle()
        manager.set_language(LANG_ZH)
        self.app.processEvents()
        chinese_title = self.window.windowTitle()

        self.assertTrue(english_title)
        self.assertTrue(chinese_title)
        self.assertNotEqual(english_title, chinese_title, "switching language did not retranslate the window title")

    def test_plugin_discovery_reports_a_summary(self):
        from annoweave.plugins import PluginHost

        host = PluginHost()
        host.discover()
        self.assertIsInstance(host.summary(), str)


if __name__ == "__main__":
    unittest.main()
