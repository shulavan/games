import unittest
from datetime import date
from unittest.mock import patch

import edogawa_ical as ical


class DateRegressionV3213Tests(unittest.TestCase):
    def test_plain_multiday_period_is_not_expanded_to_daily_events(self):
        spans = ical.parse_date_spans("2026年10月16日～12月11日", base_year=2026, include_past=True)
        self.assertEqual(spans, [(date(2026, 10, 16), date(2026, 12, 11))])
        self.assertEqual(ical.expand_recurring_spans("開催期間", spans), spans)

    def test_explicit_weekly_rule_still_expands(self):
        spans = ical.parse_date_spans("2026年11月1日～11月30日", base_year=2026, include_past=True)
        expanded = ical.expand_recurring_spans("毎週金曜日", spans)
        self.assertTrue(expanded)
        self.assertTrue(all(a == b and a.weekday() == 4 for a, b in expanded))

    def test_old_article_url_year_is_used_as_date_context(self):
        self.assertEqual(ical.extract_url_year("https://example.jp/news/2022/05/04/article.html"), 2022)
        html = """<html><body><h1>古いイベント記事</h1><h2>開催日</h2><p>5月4日</p>
        <h2>会場</h2><p>葛西臨海公園</p></body></html>"""
        detail = ical.extract_detail(html, "https://example.jp/news/2022/05/04/article.html")
        self.assertEqual(detail["published_year"], 2022)
        self.assertEqual(detail["spans"], [])  # 2022年の開催日を2027年へ繰り越さない

    def test_explicit_year_in_event_text_overrides_old_url_year(self):
        spans = ical.parse_date_spans("2027年5月4日", base_year=2022, include_past=True)
        self.assertEqual(spans, [(date(2027, 5, 4), date(2027, 5, 4))])

    def test_yearless_date_does_not_roll_forward(self):
        spans = ical.parse_date_spans("5月4日", base_year=2022, include_past=True)
        self.assertEqual(spans, [(date(2022, 5, 4), date(2022, 5, 4))])

    def test_article_date_without_any_year_is_not_guessed(self):
        self.assertEqual(ical.parse_date_spans("5月4日", require_year=True), [])

    def test_asahi_course_range_only_is_not_emitted_as_daily_events(self):
        html = """<html><body><p>〖えどがわ朝日創業塾〗</p>
        <p>2026年10月16日～12月11日</p><p>開催時間 18時00分～20時00分</p>
        <p>会場</p><p>タワーホール船堀</p></body></html>"""
        with patch.object(ical, "fetch_text", return_value=(html, {"http_status": 200})):
            events, status = ical.parse_asahi_startup_course()
        self.assertEqual(events, [])
        self.assertEqual(status["status"], "zero_valid_rows")


if __name__ == "__main__":
    unittest.main()
