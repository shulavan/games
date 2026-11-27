import sys
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import edogawa_ical as app


class V3213CompatibilityRegressionTests(unittest.TestCase):
    def test_version_is_3214(self):
        self.assertEqual(app.APP_VERSION, "3.2.16")

    def test_archive_url_year_prevents_old_month_day_rollover(self):
        html = """<html><body><main><h1>過去イベント</h1>
        <h2>開催日時</h2><p>11月3日開催</p>
        <h2>会場</h2><p>なぎさポニーランド</p></main></body></html>"""
        with patch.object(app, "TODAY", date(2026, 11, 27)):
            detail = app.extract_detail(html, "https://example.jp/2022/11/03/event.html")
        self.assertEqual(detail["published_year"], 2022)
        self.assertEqual(detail["spans"], [])

    def test_body_event_year_overrides_archive_year(self):
        html = """<html><body><main><h1>来年のイベント</h1>
        <h2>開催日時</h2><p>2027年4月10日開催</p>
        <h2>会場</h2><p>なぎさポニーランド</p></main></body></html>"""
        with patch.object(app, "TODAY", date(2026, 11, 27)):
            detail = app.extract_detail(html, "https://example.jp/2022/event.html")
        self.assertEqual(detail["spans"], [(date(2027, 4, 10), date(2027, 4, 10))])

    def test_unknown_year_is_not_guessed_in_detail(self):
        html = """<html><body><main><h1>年不明イベント</h1>
        <h2>開催日時</h2><p>4月10日開催</p>
        <h2>会場</h2><p>なぎさポニーランド</p></main></body></html>"""
        with patch.object(app, "TODAY", date(2026, 11, 27)):
            detail = app.extract_detail(html, "https://example.jp/event.html")
        self.assertEqual(detail["spans"], [])

    def test_plain_multi_day_period_is_kept_as_one_span(self):
        span = (date(2026, 12, 1), date(2026, 12, 5))
        self.assertEqual(app.expand_recurring_spans("開催期間 2026年12月1日～5日", [span]), [span])

    def test_explicit_daily_event_is_split(self):
        span = (date(2026, 12, 1), date(2026, 12, 3))
        expected = [(date(2026, 12, d), date(2026, 12, d)) for d in range(1, 4)]
        self.assertEqual(app.expand_recurring_spans("12月1日～3日 毎日開催", [span]), expected)

    def test_explicit_closure_days_are_excluded(self):
        span = (date(2026, 12, 1), date(2026, 12, 5))
        got = app.expand_recurring_spans("開催期間12月1日～5日、休館日12月3日", [span])
        self.assertEqual([x[0] for x in got], [date(2026, 12, 1), date(2026, 12, 2), date(2026, 12, 4), date(2026, 12, 5)])

    def test_explicit_yearless_parser_compatibility_does_not_apply_to_detail(self):
        with patch.object(app, "TODAY", date(2026, 11, 26)):
            self.assertEqual(app.parse_date_spans("4月10日開催"), [(date(2027, 4, 10), date(2027, 4, 10))])
            self.assertEqual(app.parse_date_spans("4月10日開催", require_year=True), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
