import unittest


class ProjectSkeletonTests(unittest.TestCase):
    def test_backend_app_imports(self):
        from backend.app.main import app, game_service

        self.assertEqual(app.title, "SoloAvalon")
        self.assertIsNotNone(game_service)
