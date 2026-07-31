import tempfile
import unittest
from pathlib import Path

from llama_router.core.storage import db_read, db_write, init_db
from tools import screenshots


class ScreenshotStateTests(unittest.TestCase):
    def test_camera_crop_keeps_aspect_and_stays_in_bounds(self):
        crop = screenshots._camera_box(
            (1920, 1080), (960, 504), (0, 0), 1.5)
        self.assertEqual(crop[:2], (0, 0))
        self.assertLessEqual(crop[2], 1920)
        self.assertLessEqual(crop[3], 1080)
        self.assertAlmostEqual(
            (crop[2] - crop[0]) / (crop[3] - crop[1]),
            960 / 504, places=2)

    def test_ease_has_stable_endpoints(self):
        self.assertEqual(screenshots._ease(-1), 0)
        self.assertEqual(screenshots._ease(0), 0)
        self.assertEqual(screenshots._ease(1), 1)
        self.assertEqual(screenshots._ease(2), 1)
        self.assertAlmostEqual(screenshots._ease(0.5), 0.5)

    def test_restore_db_value_preserves_value_and_absence(self):
        with tempfile.TemporaryDirectory() as temp:
            database = Path(temp) / "capture.db"
            init_db(database)

            db_write(database, "existing", {"page": "dashboard"})
            screenshots._restore_db_value(
                database, "existing", {"page": "settings"})
            self.assertEqual(db_read(database, "existing"),
                             {"page": "settings"})

            db_write(database, "temporary", ["demo"])
            screenshots._restore_db_value(
                database, "temporary", screenshots._MISSING)
            marker = object()
            self.assertIs(db_read(database, "temporary", marker), marker)


if __name__ == "__main__":
    unittest.main()
