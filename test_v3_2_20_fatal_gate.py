import sys
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import edogawa_ical as app


class V3220FatalGateTests(unittest.TestCase):
    def test_multi_venue_event_without_single_geo_is_not_a_health_failure(self):
        event = app.make_event(
            "区内複数会場イベント", (date(2026, 12, 20), date(2026, 12, 20)),
            "区役所前、船堀駅、小岩駅", "", "https://example.invalid/multi", "test",
            description="区内複数会場で開催"
        )
        self.assertIsNotNone(event)
        self.assertTrue(event.multi_venue)
        self.assertIsNone(event.latitude)
        self.assertIsNone(event.longitude)
        self.assertFalse(app.has_unmapped_single_venue_event([event]))

    def test_single_venue_event_without_geo_remains_a_health_failure(self):
        event = app.Event(
            title="単一会場", start_date="2026-12-20", end_date="2026-12-20",
            venue="船堀コミュニティ会館", address="東京都江戸川区船堀1丁目",
            url="https://example.invalid/single", latitude=None, longitude=None,
            verified_location=True, multi_venue=False
        )
        self.assertTrue(app.has_unmapped_single_venue_event([event]))

    def test_single_venue_with_coordinates_passes_coordinate_gate(self):
        event = app.Event(
            title="単一会場", start_date="2026-12-20", end_date="2026-12-20",
            venue="船堀コミュニティ会館", address="東京都江戸川区船堀1丁目",
            url="https://example.invalid/single", latitude=35.68, longitude=139.86,
            verified_location=True, multi_venue=False
        )
        self.assertFalse(app.has_unmapped_single_venue_event([event]))

    def test_version_and_prodid_are_3220(self):
        self.assertEqual(app.APP_VERSION, "3.2.20")
        self.assertIn("PRODID:-//Edogawa Ward Events//JP v3.2.20//EN", app.build_ics([]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
