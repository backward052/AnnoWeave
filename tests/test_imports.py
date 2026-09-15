import unittest


class ImportTests(unittest.TestCase):
    def test_package_imports(self):
        import annoweave
        from annoweave.workflow import NODE_REGISTRY

        self.assertTrue(annoweave.__version__)
        self.assertIn("generic_associate", NODE_REGISTRY)
        self.assertIn("inference", NODE_REGISTRY)


if __name__ == "__main__":
    unittest.main()
