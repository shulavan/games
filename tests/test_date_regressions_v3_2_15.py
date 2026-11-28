import sys
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import edogawa_ical as app


class V3215RegressionTests(unittest.TestCase):
    def test_version_and_prodid_are_3215(self):
        self.assertEqual(app.APP_VERSION, "3.2.19")
        self.assertIn("PRODID:-//Edogawa Ward Events//JP v3.2.19//EN", app.build_ics([]))

    def test_week_title_collapses_consecutive_daily_calendar_entries(self):
        daily = [(date(2026, 12, day), date(2026, 12, day)) for day in range(3, 10)]
        self.assertEqual(
            app.collapse_weekly_period_spans("障害者週間", daily),
            [(date(2026, 12, 3), date(2026, 12, 9))],
        )

    def test_week_title_does_not_collapse_gapped_dates(self):
        dates = [(date(2026, 12, 3), date(2026, 12, 3)),
                 (date(2026, 12, 5), date(2026, 12, 5)),
                 (date(2026, 12, 9), date(2026, 12, 9))]
        self.assertEqual(app.collapse_weekly_period_spans("障害者週間", dates), dates)

    def test_non_week_title_keeps_daily_entries_separate(self):
        daily = [(date(2026, 12, day), date(2026, 12, day)) for day in range(3, 10)]
        self.assertEqual(app.collapse_weekly_period_spans("冬の展示イベント", daily), daily)

    def test_miraikan_facility_top_page_is_excluded(self):
        self.assertTrue(app.is_non_event_page(
            "あたらしい夢との出会いが待ってます。",
            url="https://www.city.edogawa.tokyo.jp/miraikan/index.html",
        ))

    def test_miraikan_individual_event_page_is_not_excluded_by_path_rule(self):
        self.assertFalse(app.is_non_event_page(
            "おりがみヒコーキで科学する",
            url="https://www.city.edogawa.tokyo.jp/miraikan/event/origami.html",
        ))

    def test_week_period_detail_collapses_daily_sessions(self):
        html = """<html><body><main><h1>障害者週間</h1>
        <h2>開催日</h2>
        <p>2026年12月3日</p><p>2026年12月4日</p><p>2026年12月5日</p>
        <p>2026年12月6日</p><p>2026年12月7日</p><p>2026年12月8日</p><p>2026年12月9日</p>
        <h2>会場</h2><p>タワーホール船堀</p></main></body></html>"""
        with patch.object(app, "TODAY", date(2026, 11, 1)):
            detail = app.extract_detail(html, "https://example.jp/shogaisha.html")
        self.assertEqual(detail["spans"], [(date(2026, 12, 3), date(2026, 12, 9))])
        self.assertEqual(len(detail["sessions"]), 1)
        self.assertEqual(detail["sessions"][0]["span"], (date(2026, 12, 3), date(2026, 12, 9)))
        self.assertIsNone(detail["sessions"][0]["start_time"])
        self.assertIsNone(detail["sessions"][0]["end_time"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
