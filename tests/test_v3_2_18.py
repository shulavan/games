import sys
import unittest
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import edogawa_ical as app


class V3218DateWindowTests(unittest.TestCase):
    def event(self, start, end=None, title="sample"):
        return app.Event(
            title=title,
            start_date=start.isoformat(),
            end_date=(end or start).isoformat(),
            venue="船堀コミュニティ会館",
            address="東京都江戸川区船堀1丁目",
            url="https://example.jp/event",
            latitude=35.68,
            longitude=139.86,
            verified_location=True,
        )

    def test_current_version_and_prodid(self):
        self.assertEqual(app.APP_VERSION, "3.2.19")
        self.assertIn("PRODID:-//Edogawa Ward Events//JP v3.2.19//EN", app.build_ics([]))

    def test_event_exactly_60_days_ahead_is_kept(self):
        today = date(2026, 11, 28)
        ev = self.event(today + timedelta(days=60))
        kept, removed = app.filter_events_by_lookahead([ev], today=today)
        self.assertEqual(kept, [ev])
        self.assertEqual(removed, 0)

    def test_event_61_days_ahead_is_removed(self):
        today = date(2026, 11, 28)
        ev = self.event(today + timedelta(days=61))
        kept, removed = app.filter_events_by_lookahead([ev], today=today)
        self.assertEqual(kept, [])
        self.assertEqual(removed, 1)

    def test_event_started_within_window_keeps_full_end_date(self):
        today = date(2026, 11, 28)
        ev = self.event(today + timedelta(days=30), today + timedelta(days=120))
        kept, removed = app.filter_events_by_lookahead([ev], today=today)
        self.assertEqual(kept, [ev])
        self.assertEqual(kept[0].end_date, (today + timedelta(days=120)).isoformat())
        self.assertEqual(removed, 0)

    def test_filter_keeps_today_and_removes_only_far_future(self):
        today = date(2026, 11, 28)
        events = [self.event(today, title="today"),
                  self.event(today + timedelta(days=60), title="limit"),
                  self.event(today + timedelta(days=61), title="outside")]
        kept, removed = app.filter_events_by_lookahead(events, today=today)
        self.assertEqual([e.title for e in kept], ["today", "limit"])
        self.assertEqual(removed, 1)

    def test_output_validation_rejects_event_beyond_window(self):
        today = app.TODAY
        ev = self.event(today + timedelta(days=61))
        ics = app.build_ics([ev], generated_at="2026-11-28T08:33:49+09:00")
        with self.assertRaisesRegex(RuntimeError, "beyond 60-day"):
            app.validate_outputs(ics, [ev])


if __name__ == "__main__":
    unittest.main(verbosity=2)
