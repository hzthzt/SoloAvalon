import unittest


class ProjectSkeletonTests(unittest.TestCase):
    def test_backend_app_imports(self):
        from backend.app.main import app, create_app

        self.assertEqual(app.title, "SoloAvalon")
        self.assertIsNotNone(app.state.services.application)
        self.assertIsNotNone(app.state.services.queries)
        self.assertTrue(callable(create_app))
