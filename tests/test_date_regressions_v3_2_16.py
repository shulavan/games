import sys
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import edogawa_ical as app


class V3216RegressionTests(unittest.TestCase):
    def test_version_and_prodid_are_3216(self):
        self.assertEqual(app.APP_VERSION, "3.2.17")
        self.assertIn("PRODID:-//Edogawa Ward Events//JP v3.2.17//EN", app.build_ics([]))

    def test_week_period_is_not_reexpanded_by_description_event_wording(self):
        # 日別一覧の各日リンクが、同じ開催期間全体を繰り返し持つ実データ形。
        repeated_period = [(date(2026, 12, 3), date(2026, 12, 9))] * 7
        description = (
            "2026年12月3日から12月9日は障害者週間です。"
            "区内図書館等でイベントを開催します。"
        )
        got, is_week_period = app.normalize_calendar_candidate_spans(
            "障害者週間", repeated_period, description
        )
        self.assertTrue(is_week_period)
        self.assertEqual(got, [(date(2026, 12, 3), date(2026, 12, 9))])

    def test_week_daily_cells_collapse_to_one_period(self):
        daily = [(date(2026, 12, day), date(2026, 12, day)) for day in range(3, 10)]
        got, is_week_period = app.normalize_calendar_candidate_spans(
            "障害者週間", daily, "期間中に関連イベントを開催します。"
        )
        self.assertTrue(is_week_period)
        self.assertEqual(got, [(date(2026, 12, 3), date(2026, 12, 9))])

    def test_week_period_keeps_exclusive_ical_end_date(self):
        got, _ = app.normalize_calendar_candidate_spans(
            "障害者週間", [(date(2026, 12, 3), date(2026, 12, 9))] * 7,
            "期間中にイベントを開催します。"
        )
        # iCalの終日予定ではDTENDが終了日の翌日になることを確認。
        sample = app.Event(
            title="障害者週間", start_date=got[0][0].isoformat(), end_date=got[0][1].isoformat(),
            venue="", address="", url="https://example.jp/week",
            source="区公式", category="区公式", priority=1
        )
        ics = app.build_ics([sample], generated_at="2026-11-27T17:00:00+09:00")
        self.assertIn("DTSTART;VALUE=DATE:20261203", ics)
        self.assertIn("DTEND;VALUE=DATE:20261210", ics)

    def test_family_seminar_both_explicit_sessions_are_parsed(self):
        html = """<html><body><main><h1>家庭教育セミナー 幼児コース</h1>
        <h2>開催日時</h2>
        <ul>
          <li>2026年11月20日（金曜日）10時30分から12時00分</li>
          <li>2026年11月27日（金曜日）10時30分から12時00分</li>
        </ul>
        <h2>申し込み</h2><p>2026年10月15日 9時から</p>
        <h2>場所</h2><p>船堀コミュニティ会館</p></main></body></html>"""
        with patch.object(app, "TODAY", date(2026, 11, 1)):
            detail = app.extract_detail(html, "https://example.jp/kateikyoiku26-11.html")
        got = [
            (s["span"][0], s["start_time"], s["end_time"])
            for s in detail["sessions"]
        ]
        self.assertEqual(got, [
            (date(2026, 11, 20), "10:30", "12:00"),
            (date(2026, 11, 27), "10:30", "12:00"),
        ])

    def test_past_seminar_session_is_filtered_only_after_parsing(self):
        html = """<html><body><main><h1>家庭教育セミナー 幼児コース</h1>
        <h2>開催日時</h2>
        <ul>
          <li>2026年11月20日（金曜日）10時30分から12時00分</li>
          <li>2026年11月27日（金曜日）10時30分から12時00分</li>
        </ul><h2>場所</h2><p>船堀コミュニティ会館</p></main></body></html>"""
        with patch.object(app, "TODAY", date(2026, 11, 27)):
            detail = app.extract_detail(html, "https://example.jp/kateikyoiku26-11.html")
            # TODAY時点で11/20は過去なので、開催日抽出後に公開対象から除外される。
            self.assertEqual(detail["spans"], [
                (date(2026, 11, 27), date(2026, 11, 27)),
            ])
            self.assertEqual([s["span"][0] for s in detail["sessions"]],
                             [date(2026, 11, 27)])
            upcoming = app.expand_recurring_spans(detail["date_text"], detail["spans"])
        self.assertEqual(upcoming, [(date(2026, 11, 27), date(2026, 11, 27))])

    def test_gapped_week_dates_are_not_collapsed_or_fabricated(self):
        dates = [(date(2026, 12, 3), date(2026, 12, 3)),
                 (date(2026, 12, 5), date(2026, 12, 5)),
                 (date(2026, 12, 9), date(2026, 12, 9))]
        got, is_week_period = app.normalize_calendar_candidate_spans(
            "障害者週間", dates, "関連イベントを開催します。"
        )
        self.assertFalse(is_week_period)
        self.assertEqual(got, dates)


if __name__ == "__main__":
    unittest.main(verbosity=2)
